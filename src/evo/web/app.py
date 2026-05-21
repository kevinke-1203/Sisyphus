"""sisyphus Web Dashboard - FastAPI application."""

import asyncio
from datetime import datetime
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, BackgroundTasks, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from evo.evolution.tracker import TrajectoryStore
from evo.runtime_events import (
    WorkflowCancelled,
    reset_cancel_provider,
    reset_event_callback,
    reset_user_message_provider,
    set_cancel_provider,
    set_event_callback,
    set_user_message_provider,
)
from evo.task import _run_git
from evo.config import get_data_dir
from evo.tmux import (
    TmuxError,
    capture_pane as tmux_capture_pane,
    read_log_tail,
    send_text as tmux_send_text,
    session_exists as tmux_session_exists,
    stop_session as tmux_stop_session,
    tmux_available,
)

_BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="sisyphus")

app.mount("/static", StaticFiles(directory=str(_BASE_DIR / "static")), name="static")

_INDEX_HTML = (_BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")

_running_tasks: dict[str, dict] = {}
_MAX_TASK_LOGS = 1000
_ACTIVE_STATUSES = {"pending", "running", "stopping"}


def _append_task_log(task_id: str, event: dict[str, Any]):
    entry = _running_tasks.get(task_id)
    if entry is None:
        return

    if event.get("type") == "task_update" and isinstance(event.get("fields"), dict):
        entry.update(event["fields"])

    if not event.get("message") and event.get("type") == "agent_event":
        return

    log = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "type": event.get("type", "event"),
        "agent": event.get("agent", ""),
        "event": event.get("event", ""),
        "message": str(event.get("message", "")),
    }
    entry.setdefault("logs", []).append(log)
    if len(entry["logs"]) > _MAX_TASK_LOGS:
        entry["logs"] = entry["logs"][-_MAX_TASK_LOGS:]


def _default_browse_path() -> Path:
    return Path.cwd()


def _directory_payload(path: Path) -> dict:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        return {"error": f"路径不存在：{resolved}"}
    if not resolved.is_dir():
        return {"error": f"路径不是目录：{resolved}"}

    dirs = []
    try:
        for child in resolved.iterdir():
            if child.name.startswith("."):
                continue
            try:
                if child.is_dir():
                    dirs.append({
                        "name": child.name,
                        "path": str(child.resolve()),
                    })
            except OSError:
                continue
    except PermissionError:
        return {"error": f"没有权限访问：{resolved}"}

    dirs.sort(key=lambda item: item["name"].lower())
    parent = resolved.parent if resolved.parent != resolved else None
    return {
        "path": str(resolved),
        "parent": str(parent) if parent else "",
        "dirs": dirs,
    }


def _get_current_branch(path: str) -> str:
    """Get the current git branch of the given path."""
    result = _run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=path, check=False)
    if result.returncode == 0:
        branch = result.stdout.strip()
        if branch and branch != "HEAD":
            return branch
    # Fallback to common defaults
    for candidate in ["main", "master", "trunk", "develop"]:
        result = _run_git("rev-parse", "--verify", candidate, cwd=path, check=False)
        if result.returncode == 0:
            return candidate
    return "main"


def _get_workflows() -> list[dict]:
    try:
        from evo.orchestrator.parser import load_config, list_workflows
        config = load_config()
        return list_workflows(config)
    except FileNotFoundError:
        return []


def _find_running_entry(task_id: str) -> dict | None:
    for entry in _running_tasks.values():
        if entry.get("id") == task_id or entry.get("task_id") == task_id:
            return entry
    return None


def _tmux_log_dir() -> Path:
    path = Path(get_data_dir()) / "tmux_logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _refresh_tmux_entry(entry: dict):
    if entry.get("transport") != "tmux":
        return

    session = entry.get("tmux_session", "")
    alive = bool(session and tmux_session_exists(session))
    if session and entry.get("status") in _ACTIVE_STATUSES and not alive:
        entry["status"] = "completed"
        _append_task_log(str(entry.get("id", "")), {
            "type": "workflow",
            "message": "tmux session ended",
        })
    entry["tmux_alive"] = alive

    text, size = read_log_tail(entry.get("tmux_log_path", ""))
    screen = tmux_capture_pane(session) if alive else ""
    entry["tmux_screen"] = screen
    entry["tmux_log"] = text
    entry["tmux_log_size"] = size


def _run_workflow_in_background(task_id: str, task: str, workflow_name: str,
                                repo: str = "", branch: str = "main"):
    entry = _running_tasks[task_id]
    callback_token = set_event_callback(lambda event: _append_task_log(task_id, event))
    message_token = set_user_message_provider(lambda: list(entry.get("messages", [])))
    cancel_token = set_cancel_provider(lambda: bool(entry.get("stop_requested")))
    try:
        entry["status"] = "running"
        _append_task_log(task_id, {
            "type": "workflow",
            "message": f"Workflow started: {workflow_name or 'default'}",
        })

        if workflow_name:
            from evo.orchestrator.parser import load_config, register_agents_from_config, build_graph_from_config
            from evo.orchestrator.state import WorkflowState

            config = load_config()
            register_agents_from_config(config)
            wf_config = config.get("workflows", {}).get(workflow_name)
            if not wf_config:
                entry["status"] = "failed"
                entry["error"] = f"未找到工作流：{workflow_name}"
                return
            wf_config = {**wf_config, "skip_confirm": True}
            graph, max_iterations = build_graph_from_config(wf_config)

            initial_state: WorkflowState = {
                "task": task, "plan": "", "code": "", "test_result": "",
                "test_passed": False, "api_test_result": "", "api_test_passed": False,
                "ui_test_result": "", "ui_test_passed": False,
                "current_step": "", "iteration": 0,
                "max_iterations": max_iterations, "error": "",
                "_sdk_meta": {}, "_retries": {},
                "requirements_doc": "", "design_doc": "", "test_cases": "",
                "user_feedback": "", "task_id": "", "task_name": "",
                "task_dir": "", "dir_name": "", "worktree_path": "",
                "repo_path": repo, "repo_branch": branch,
            }

            final_state = asyncio.run(graph.ainvoke(initial_state))
        else:
            from evo.orchestrator.engine import run_workflow
            final_state = asyncio.run(
                run_workflow(task, repo_path=repo, branch=branch, skip_confirm=True)
            )

        entry["status"] = "completed"
        entry["result"] = {
            "test_passed": final_state.get("test_passed", False),
            "iteration": final_state.get("iteration", 0),
            "error": final_state.get("error", ""),
        }
        _append_task_log(task_id, {
            "type": "workflow",
            "message": "Workflow completed",
        })
    except WorkflowCancelled as e:
        entry["status"] = "stopped"
        entry["error"] = str(e)
        _append_task_log(task_id, {
            "type": "workflow_stopped",
            "message": str(e),
        })
    except Exception as e:
        entry["status"] = "failed"
        entry["error"] = str(e)
        _append_task_log(task_id, {
            "type": "workflow_error",
            "message": str(e),
        })
    finally:
        reset_event_callback(callback_token)
        reset_user_message_provider(message_token)
        reset_cancel_provider(cancel_token)


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(_INDEX_HTML)


@app.get("/api/workflows")
async def api_workflows():
    return _get_workflows()


@app.get("/api/trajectories")
async def api_trajectories(limit: int = 50):
    store = TrajectoryStore()
    return store.list_all(limit=limit)


@app.get("/api/trajectories/{trajectory_id}")
async def api_trajectory_detail(trajectory_id: str):
    store = TrajectoryStore()
    t = store.get(trajectory_id)
    if not t:
        return JSONResponse({"error": "未找到"}, status_code=404)
    import dataclasses
    return dataclasses.asdict(t)


@app.get("/api/browse")
async def api_browse(path: str = Query(default="")):
    target = Path(path) if path else _default_browse_path()
    payload = _directory_payload(target)
    if "error" in payload:
        return JSONResponse(payload, status_code=400)
    return payload


@app.get("/api/repo-branch")
async def api_repo_branch(path: str = Query(default="")):
    target = Path(path) if path else _default_browse_path()
    resolved = target.expanduser().resolve()
    if not resolved.exists() or not resolved.is_dir():
        return {"branch": "main"}
    branch = _get_current_branch(str(resolved))
    return {"branch": branch}


@app.get("/api/running")
async def api_running():
    for entry in _running_tasks.values():
        _refresh_tmux_entry(entry)
    return list(_running_tasks.values())


@app.post("/api/run")
async def api_run(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    task = body.get("task", "").strip()
    if not task:
        return JSONResponse({"error": "任务描述不能为空"}, status_code=400)

    workflow = body.get("workflow", "")
    repo = body.get("repo", "")
    branch = body.get("branch", "main")

    task_id = uuid.uuid4().hex[:8]
    _running_tasks[task_id] = {
        "id": task_id,
        "task": task,
        "workflow": workflow,
        "status": "pending",
        "error": "",
        "result": None,
        "logs": [],
        "messages": [],
        "stop_requested": False,
        "transport": "tmux",
    }

    if not tmux_available():
        _running_tasks.pop(task_id, None)
        return JSONResponse({"error": "tmux 未安装或不在 PATH 中"}, status_code=400)
    background_tasks.add_task(
        _run_workflow_in_background, task_id, task, workflow, repo, branch
    )

    return {"task_id": task_id, "status": "pending"}


@app.post("/api/running/{task_id}/message")
async def api_running_message(task_id: str, request: Request):
    entry = _running_tasks.get(task_id)
    if entry is None:
        return JSONResponse({"error": "未找到运行中的任务"}, status_code=404)
    if entry.get("status") not in {"pending", "running"}:
        return JSONResponse({"error": "任务已结束，不能继续发送消息"}, status_code=400)

    body = await request.json()
    message = body.get("message", "").strip()
    if not message:
        return JSONResponse({"error": "消息不能为空"}, status_code=400)

    if entry.get("transport") == "tmux":
        session = entry.get("tmux_session", "")
        if not session:
            return JSONResponse({"error": "当前还没有正在执行的 agent tmux session"}, status_code=400)
        if not tmux_session_exists(session):
            return JSONResponse({"error": "当前 agent tmux session 已结束"}, status_code=400)
        item = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "message": message,
        }
        entry.setdefault("messages", []).append(item)
        try:
            tmux_send_text(session, message)
        except TmuxError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        _append_task_log(task_id, {
            "type": "user_message",
            "message": message,
        })
        _refresh_tmux_entry(entry)
        return {"ok": True, "message_count": len(entry.get("messages", []))}

    item = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "message": message,
    }
    entry.setdefault("messages", []).append(item)
    _append_task_log(task_id, {
        "type": "user_message",
        "message": message,
    })
    return {"ok": True, "message_count": len(entry["messages"])}


@app.post("/api/running/{task_id}/stop")
async def api_running_stop(task_id: str):
    entry = _find_running_entry(task_id)
    if entry is None:
        return JSONResponse({"error": "未找到运行中的任务"}, status_code=404)
    if entry.get("status") not in _ACTIVE_STATUSES:
        return JSONResponse({"error": "任务已结束，不能停止"}, status_code=400)

    if entry.get("transport") == "tmux":
        session = entry.get("tmux_session", "")
        if session:
            try:
                tmux_stop_session(session)
            except TmuxError as e:
                return JSONResponse({"error": str(e)}, status_code=400)
        entry["stop_requested"] = True
        entry["status"] = "stopping"
        entry["tmux_alive"] = False
        _append_task_log(str(entry.get("id", task_id)), {
            "type": "workflow_stop_requested",
            "message": "Stop requested",
        })
        _refresh_tmux_entry(entry)
        return {"ok": True, "status": "stopping"}

    entry["stop_requested"] = True
    entry["status"] = "stopping"
    _append_task_log(str(entry.get("id", task_id)), {
        "type": "workflow_stop_requested",
        "message": "Stop requested",
    })
    return {"ok": True, "status": "stopping"}
