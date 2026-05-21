"""LangGraph workflow engine - builds and executes the plan->code->test graph."""

import os
from typing import Any, Mapping, cast

from langgraph.graph import StateGraph, END

from evo.orchestrator.state import WorkflowState
from evo.orchestrator import display
from evo.agents.planner import PlannerAgent
from evo.agents.coder import CoderAgent
from evo.agents.tester import TesterAgent
from evo.agents.api_tester import ApiTesterAgent
from evo.agents.ui_tester import UiTesterAgent
from evo.evolution.tracker import TrajectoryTracker, TrajectoryStore
from evo.runtime_events import check_cancelled, emit_event
from evo.task import TaskManager

planner = PlannerAgent()
coder = CoderAgent()
tester = TesterAgent()
api_tester = ApiTesterAgent()
ui_tester = UiTesterAgent()

_tracker: TrajectoryTracker | None = None
_skip_confirm: bool = False
_task_manager: TaskManager | None = None


def _get_task_manager() -> TaskManager:
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager


def _get_tracker() -> TrajectoryTracker:
    if _tracker is None:
        raise RuntimeError("Trajectory tracker has not been initialized.")
    return _tracker


def _set_agent_cwd(state: Mapping[str, Any]):
    """Set agent working directories to the task's worktree path."""
    worktree = state.get("worktree_path", "")
    if worktree and os.path.isdir(worktree):
        planner.config.cwd = worktree
        coder.config.cwd = worktree
        tester.config.cwd = worktree
        api_tester.config.cwd = worktree
        ui_tester.config.cwd = worktree


# ── Setup node ─────────────────────────────────────────────────

async def setup_node(state: WorkflowState) -> dict[str, Any]:
    """Create task directory, generate name, setup worktree."""
    check_cancelled()
    mgr = _get_task_manager()
    repo_path = state.get("repo_path", "")
    branch = state.get("repo_branch", "main")
    description = state["task"]

    display.step_start("setup")
    task_info = mgr.create_task(description, repo_path, branch)

    print(f"[Setup] Task: {task_info['dir_name']}")
    print(f"[Setup] Worktree: {task_info['worktree_path']}")
    emit_event({
        "type": "task_update",
        "message": f"Task workspace ready: {task_info['dir_name']}",
        "fields": dict(task_info),
    })

    # Set agent cwd to worktree for subsequent nodes
    _set_agent_cwd(task_info)

    display.step_end(0, True, task_info["dir_name"])
    return dict(task_info)


# ── Plan node ──────────────────────────────────────────────────

async def plan_node(state: WorkflowState) -> dict:
    check_cancelled()
    _set_agent_cwd(state)
    tracker = _get_tracker()

    display.step_start("plan")
    tracker.begin_step()
    result = await planner.execute(cast(dict[str, Any], state))
    sdk_meta = result.pop("_sdk_meta", {})
    tracker.end_step("plan", state["task"][:200], result["plan"][:200],
                     tokens_used=sdk_meta.get("tokens_used", 0),
                     cost_usd=sdk_meta.get("cost_usd", 0.0),
                     tool_calls=sdk_meta.get("tool_calls", []))
    duration = tracker.trajectory.steps[-1].duration_ms
    display.step_end(duration, True, f"{len(result['plan'])} chars")

    # Increment plan retry counter
    retries = dict(state.get("_retries", {}))
    retries["plan"] = retries.get("plan", 0) + 1
    result["_retries"] = retries

    # Write artifacts
    task_dir = state.get("task_dir", "")
    mgr = _get_task_manager()
    mgr.write_artifact(task_dir, "requirements", result.get("requirements_doc", ""))
    mgr.write_artifact(task_dir, "design", result.get("design_doc", ""))
    mgr.write_artifact(task_dir, "test_cases", result.get("test_cases", ""))

    return result


# ── Confirm node ───────────────────────────────────────────────

def confirm_node(state: WorkflowState) -> dict:
    """Show requirements doc to user and collect feedback. Auto-approves if skip_confirm is set."""
    check_cancelled()
    if _skip_confirm:
        print("\n[Confirm] Auto-approved (skip_confirm=True)")
        return {}

    requirements = state.get("requirements_doc", "")
    if not requirements:
        print("\n[Confirm] No requirements doc found, auto-approving.")
        return {}

    print(f"\n{'='*60}")
    print("[Confirm] Requirements Analysis — Please Review")
    print(f"{'='*60}")
    print(requirements)
    print(f"{'='*60}")

    print("\nOptions:")
    print("  [Enter]  Approve and proceed")
    print("  [text]   Provide feedback to revise the plan")
    feedback = input("\nYour choice: ").strip()

    if feedback:
        print(f"\n[Confirm] Feedback recorded. Returning to planner for revision.")
        return {"user_feedback": feedback}

    print("\n[Confirm] Approved! Proceeding to code.")
    return {}


# ── Code node ──────────────────────────────────────────────────

async def code_node(state: WorkflowState) -> dict:
    check_cancelled()
    iteration = state.get("iteration", 0) + 1
    _set_agent_cwd(state)
    tracker = _get_tracker()

    display.step_start("code", iteration)
    tracker.begin_step()
    result = await coder.execute(cast(dict[str, Any], state))
    sdk_meta = result.pop("_sdk_meta", {})
    tracker.end_step("code", state.get("plan", "")[:200], result["code"][:200],
                     tokens_used=sdk_meta.get("tokens_used", 0),
                     cost_usd=sdk_meta.get("cost_usd", 0.0),
                     tool_calls=sdk_meta.get("tool_calls", []))
    duration = tracker.trajectory.steps[-1].duration_ms
    display.step_end(duration, True, f"{len(result['code'])} chars")

    # Increment code retry counter
    retries = dict(state.get("_retries", {}))
    retries["code"] = retries.get("code", 0) + 1
    result["_retries"] = retries

    result["iteration"] = iteration
    return result


# ── Test nodes ─────────────────────────────────────────────────

async def api_test_node(state: WorkflowState) -> dict:
    check_cancelled()
    _set_agent_cwd(state)
    tracker = _get_tracker()

    display.step_start("api_test")
    tracker.begin_step()
    result = await api_tester.execute(cast(dict[str, Any], state))
    sdk_meta = result.pop("_sdk_meta", {})
    passed = result.get("api_test_passed", False)
    tracker.end_step("api_test", state.get("code", "")[:200], result.get("api_test_result", "")[:200],
                     success=passed,
                     tokens_used=sdk_meta.get("tokens_used", 0),
                     cost_usd=sdk_meta.get("cost_usd", 0.0),
                     tool_calls=sdk_meta.get("tool_calls", []))
    duration = tracker.trajectory.steps[-1].duration_ms
    display.step_end(duration, passed, "PASS" if passed else "FAIL")

    result["_retries"] = dict(state.get("_retries", {}))
    task_dir = state.get("task_dir", "")
    _get_task_manager().write_artifact(task_dir, "api_test_result", result.get("api_test_result", ""))
    return result


async def ui_test_node(state: WorkflowState) -> dict:
    check_cancelled()
    _set_agent_cwd(state)
    tracker = _get_tracker()

    display.step_start("ui_test")
    tracker.begin_step()
    result = await ui_tester.execute(cast(dict[str, Any], state))
    sdk_meta = result.pop("_sdk_meta", {})
    passed = result.get("ui_test_passed", False)
    tracker.end_step("ui_test", state.get("api_test_result", "")[:200], result.get("ui_test_result", "")[:200],
                     success=passed,
                     tokens_used=sdk_meta.get("tokens_used", 0),
                     cost_usd=sdk_meta.get("cost_usd", 0.0),
                     tool_calls=sdk_meta.get("tool_calls", []))
    duration = tracker.trajectory.steps[-1].duration_ms
    display.step_end(duration, passed, "PASS" if passed else "FAIL")

    result["_retries"] = dict(state.get("_retries", {}))
    task_dir = state.get("task_dir", "")
    _get_task_manager().write_artifact(task_dir, "ui_test_result", result.get("ui_test_result", ""))
    return result


async def test_node(state: WorkflowState) -> dict:
    check_cancelled()

    if "api_test_result" not in state and "ui_test_result" not in state:
        _set_agent_cwd(state)
        tracker = _get_tracker()
        display.step_start("test")
        tracker.begin_step()
        result = await tester.execute(cast(dict[str, Any], state))
        sdk_meta = result.pop("_sdk_meta", {})
        passed = result["test_passed"]
        tracker.end_step("test", state.get("code", "")[:200], result["test_result"][:200],
                         success=passed,
                         tokens_used=sdk_meta.get("tokens_used", 0),
                         cost_usd=sdk_meta.get("cost_usd", 0.0),
                         tool_calls=sdk_meta.get("tool_calls", []))
        duration = tracker.trajectory.steps[-1].duration_ms
        display.step_end(duration, passed, "PASS" if passed else "FAIL")
        result["_retries"] = dict(state.get("_retries", {}))
        _get_task_manager().write_artifact(state.get("task_dir", ""), "test_result", result.get("test_result", ""))
        return result

    api_passed = state.get("api_test_passed", True)
    ui_passed = state.get("ui_test_passed", True)
    passed = bool(api_passed and ui_passed)
    api_result = state.get("api_test_result", "")
    ui_result = state.get("ui_test_result", "")
    test_result = (
        f"RESULT: {'PASS' if passed else 'FAIL'}\n\n"
        "## API Test Result\n"
        f"{api_result or 'API test did not run.'}\n\n"
        "## UI Test Result\n"
        f"{ui_result or 'UI test did not run.'}"
    )
    result = {
        "test_result": test_result,
        "test_passed": passed,
        "current_step": "test",
        "_retries": dict(state.get("_retries", {})),
    }
    _get_task_manager().write_artifact(state.get("task_dir", ""), "test_result", test_result)
    return result


# ── Archive node ───────────────────────────────────────────────

async def archive_node(state: WorkflowState) -> dict:
    """Archive the task: merge branch, remove worktree, move to archive/."""
    check_cancelled()
    if not state.get("test_passed", False):
        print("[Archive] Skipped: workflow did not pass tests.")
        return {"error": "Archive skipped: workflow did not pass tests."}

    mgr = _get_task_manager()
    task_id = state.get("task_id", "")
    task_dir = state.get("task_dir", "")

    try:
        archived_task_dir = mgr.archive_task(task_id)
        print(f"[Archive] Task {task_id} archived and merged.")
        return {"current_step": "archived", "task_dir": archived_task_dir}
    except Exception as e:
        print(f"[Archive] Failed to archive task {task_id}: {e}")
        return {"error": f"Archive failed: {e}"}


# ── Graph construction ────────────────────────────────────────

def build_graph() -> Any:
    graph = StateGraph(WorkflowState)

    graph.add_node("setup", setup_node)
    graph.add_node("plan", plan_node)
    graph.add_node("confirm", confirm_node)
    graph.add_node("code", code_node)
    graph.add_node("api_test", api_test_node)
    graph.add_node("ui_test", ui_test_node)
    graph.add_node("test", test_node)
    graph.add_node("archive", archive_node)

    graph.set_entry_point("setup")
    graph.add_edge("setup", "plan")
    graph.add_edge("plan", "confirm")

    # Confirm: approved → code, feedback → back to plan
    graph.add_conditional_edges("confirm", _confirm_router, {
        "code": "code",
        "plan": "plan",
    })

    graph.add_edge("code", "api_test")
    graph.add_edge("api_test", "ui_test")
    graph.add_edge("ui_test", "test")

    # Multi-level conditional routing from test node
    graph.add_conditional_edges("test", _test_router, {
        "archive": "archive",
        "code": "code",
        "plan": "plan",
        "end": END,
    })

    graph.add_edge("archive", END)

    return graph.compile()


def _confirm_router(state: WorkflowState) -> str:
    """Route after confirm: approved → code, feedback → back to plan."""
    if state.get("user_feedback", ""):
        return "plan"
    return "code"


def _test_router(state: WorkflowState) -> str:
    """Route after test: archive / retry code / retry plan / end as failed."""
    if state.get("test_passed", False):
        return "archive"
    retries = state.get("_retries", {})
    code_retries = retries.get("code", 0)
    plan_retries = retries.get("plan", 0)
    if code_retries < 3:
        return "code"
    if plan_retries < 3:
        return "plan"
    return "end"


# ── Workflow runner ────────────────────────────────────────────

async def run_workflow(task: str, repo_path: str = "", branch: str = "main",
                       max_iterations: int = 3, skip_confirm: bool = False):
    """Execute the setup->plan->confirm->code->test workflow."""
    global _tracker, _skip_confirm, _task_manager
    _tracker = TrajectoryTracker(workflow_name="plan_code_test", task=task)
    tracker = _get_tracker()
    _skip_confirm = skip_confirm
    _task_manager = TaskManager()

    display.header(task, "plan_code_test", max_iterations)

    graph = build_graph()

    initial_state: WorkflowState = {
        "task": task,
        "plan": "",
        "code": "",
        "test_result": "",
        "test_passed": False,
        "api_test_result": "",
        "api_test_passed": False,
        "ui_test_result": "",
        "ui_test_passed": False,
        "current_step": "",
        "iteration": 0,
        "max_iterations": max_iterations,
        "error": "",
        "_sdk_meta": {},
        "_retries": {},
        "requirements_doc": "",
        "design_doc": "",
        "test_cases": "",
        "user_feedback": "",
        "task_id": "",
        "task_name": "",
        "task_dir": "",
        "dir_name": "",
        "worktree_path": "",
        "repo_path": repo_path,
        "repo_branch": branch,
    }

    final_state = await graph.ainvoke(initial_state)

    # Write final state snapshot
    _task_manager.write_state(final_state.get("task_dir", ""), final_state)

    # Persist trajectory
    outcome = "success" if final_state.get("test_passed") else "failure"
    tracker.complete(outcome)

    store = TrajectoryStore()
    store.save(tracker.trajectory)

    # Calculate total duration and tokens
    total_ms = sum(s.duration_ms for s in tracker.trajectory.steps)
    total_tokens = sum(s.tokens_used for s in tracker.trajectory.steps)

    display.footer(
        passed=final_state.get("test_passed", False),
        iterations=final_state.get("iteration", 0),
        trajectory_id=tracker.trajectory.id,
        total_ms=total_ms,
        total_tokens=total_tokens,
    )

    if final_state.get("test_passed"):
        display.code_output(final_state.get("code", ""))
    else:
        display.error("Failed to produce passing code within iteration limit.")
        display.info(f"Last test result: {final_state.get('test_result', '')[:200]}")

    return final_state
