"""Claude Code CLI transport based on the bidirectional stream-json protocol."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from typing import Any

from evo.runtime_events import emit_event


CONTINUE_SESSION = "__continue__"
EDIT_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
HOOK_BLOCKED_RE = re.compile(
    r"blocked by hook:\s*\n?\s*\[([^\]]+)\]:?\s*([\s\S]*?)(?=\n\s*Original prompt:|$)"
)


@dataclass
class ClaudeCodeOptions:
    agent_name: str
    cwd: str = ""
    model: str = ""
    system_prompt: str = ""
    permission_mode: str = "bypassPermissions"
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    max_budget_usd: float | None = None
    no_hooks: bool = False
    session_env: list[str] = field(default_factory=list)


@dataclass
class ClaudeCodeEvent:
    type: str
    content: str = ""
    session_id: str = ""
    tool_name: str = ""
    tool_input: str = ""
    tool_input_raw: Any = None
    request_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    num_turns: int = 0
    is_error: bool = False
    hook_script: str = ""
    hook_command: str = ""
    hook_message: str = ""
    error: str = ""


@dataclass
class PermissionResult:
    behavior: str
    updated_input: Any = None
    message: str = ""


def normalize_permission_mode(raw: str) -> str:
    if not raw:
        return "default"
    mode = raw.strip().lower()
    mode_map = {
        "acceptedits": "acceptEdits",
        "accept-edits": "acceptEdits",
        "accept_edits": "acceptEdits",
        "edit": "acceptEdits",
        "plan": "plan",
        "auto": "auto",
        "bypasspermissions": "bypassPermissions",
        "bypass-permissions": "bypassPermissions",
        "bypass_permissions": "bypassPermissions",
        "yolo": "bypassPermissions",
        "dontask": "dontAsk",
        "dont-ask": "dontAsk",
        "dont_ask": "dontAsk",
    }
    return mode_map.get(mode, raw)


def claude_bin() -> str:
    path = os.getenv("CLAUDE_CODE_BIN") or shutil.which("claude")
    if not path:
        raise RuntimeError("Claude Code CLI not found. Install `claude` or set CLAUDE_CODE_BIN.")
    return path


class ClaudeCodeClient:
    def __init__(self, options: ClaudeCodeOptions):
        self.options = options

    def build_command(self, session_id: str = "", resume: bool = False) -> list[str]:
        args = [
            claude_bin(),
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--permission-prompt-tool", "stdio",
            "--verbose",
        ]
        if self.options.no_hooks:
            args.append("--no-hooks")
        mode = normalize_permission_mode(self.options.permission_mode)
        if mode and mode != "default":
            args.extend(["--permission-mode", mode])
        if resume and session_id:
            args.extend(["--resume", session_id])
        elif session_id == CONTINUE_SESSION:
            args.extend(["--continue", "--fork-session"])
        elif session_id:
            args.extend(["--resume", session_id])
        if self.options.model:
            args.extend(["--model", self.options.model])
        if self.options.allowed_tools:
            args.extend(["--allowedTools", ",".join(self.options.allowed_tools)])
        if self.options.disallowed_tools:
            args.extend(["--disallowedTools", ",".join(self.options.disallowed_tools)])
        if self.options.system_prompt:
            args.extend(["--append-system-prompt", self.options.system_prompt])
        if self.options.max_budget_usd is not None:
            args.extend(["--max-budget-usd", str(self.options.max_budget_usd)])
        return args

    def build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        for item in self.options.session_env:
            key, sep, value = item.partition("=")
            if sep and key:
                env[key] = value
        env.pop("CLAUDECODE", None)
        return env

    async def start_session(self, session_id: str = "", resume: bool = False) -> "ClaudeCodeSession":
        process = await asyncio.create_subprocess_exec(
            *self.build_command(session_id=session_id, resume=resume),
            cwd=self.options.cwd or None,
            env=self.build_env(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return ClaudeCodeSession(process, self.options.agent_name, self.options.permission_mode)


class ClaudeCodeSession:
    def __init__(self, process: asyncio.subprocess.Process, agent_name: str, permission_mode: str):
        self.process = process
        self.agent_name = agent_name
        self.permission_mode = normalize_permission_mode(permission_mode)
        self.session_id = ""
        self.stderr_parts: list[str] = []
        self._stderr_task = asyncio.create_task(self._read_stderr())

    async def send(self, prompt: str):
        self.write_json({"type": "user", "message": {"role": "user", "content": prompt}})

    def write_json(self, payload: dict[str, Any]):
        if self.process.stdin is None:
            raise RuntimeError("Claude Code stdin is not available.")
        self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))

    async def drain(self):
        if self.process.stdin is not None:
            await self.process.stdin.drain()

    async def read_event_with_interrupt(self, timeout: float = 0.5) -> ClaudeCodeEvent | None:
        if self.process.stdout is None:
            return None
        line = await asyncio.wait_for(self.process.stdout.readline(), timeout=timeout)
        if not line:
            return ClaudeCodeEvent("eof")
        raw_line = line.decode("utf-8", errors="replace").strip()
        if not raw_line:
            return None
        try:
            raw = json.loads(raw_line)
        except json.JSONDecodeError:
            return ClaudeCodeEvent("text", content=raw_line)
        return await self._handle_raw_event(raw)

    async def _handle_raw_event(self, raw: dict[str, Any]) -> ClaudeCodeEvent | None:
        event_type = raw.get("type")
        if event_type == "system":
            if raw.get("session_id"):
                self.session_id = str(raw["session_id"])
                return ClaudeCodeEvent("text", session_id=self.session_id)
            return None
        if event_type == "assistant":
            return self._handle_assistant(raw)
        if event_type == "result":
            return self._handle_result(raw)
        if event_type == "control_request":
            return await self._handle_control_request(raw)
        if event_type in {"user", "control_cancel_request"}:
            return None
        return ClaudeCodeEvent(event_type or "event", content=extract_stream_text(raw))

    def _handle_assistant(self, raw: dict[str, Any]) -> ClaudeCodeEvent | None:
        message = raw.get("message")
        if not isinstance(message, dict):
            return None
        content = message.get("content")
        items = content if isinstance(content, list) else [{"type": "text", "text": content}]
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "tool_use":
                tool_name = str(item.get("name") or "")
                if tool_name == "AskUserQuestion":
                    continue
                return ClaudeCodeEvent(
                    "tool_use",
                    tool_name=tool_name,
                    tool_input=summarize_tool_input(tool_name, item.get("input")),
                    tool_input_raw=item.get("input"),
                )
            if item_type == "thinking" and item.get("thinking"):
                return ClaudeCodeEvent("thinking", content=str(item["thinking"]))
            if item_type == "text" and item.get("text"):
                return ClaudeCodeEvent("text", content=str(item["text"]))
        return None

    def _handle_result(self, raw: dict[str, Any]) -> ClaudeCodeEvent:
        if raw.get("session_id"):
            self.session_id = str(raw["session_id"])
        raw_usage = raw.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        output_tokens = int(usage.get("output_tokens") or usage.get("total_tokens") or raw.get("tokens_used") or 0)
        return ClaudeCodeEvent(
            "result",
            content=str(raw.get("result") or ""),
            session_id=self.session_id,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=output_tokens,
            cost_usd=float(raw.get("total_cost_usd") or raw.get("cost_usd") or 0.0),
            num_turns=int(raw.get("num_turns") or 0),
            is_error=bool(raw.get("is_error") or False),
        )

    async def _handle_control_request(self, raw: dict[str, Any]) -> ClaudeCodeEvent | None:
        request_id = str(raw.get("request_id") or "")
        request = raw.get("request")
        if not isinstance(request, dict) or request.get("subtype") != "can_use_tool":
            return None
        tool_name = str(request.get("tool_name") or "")
        tool_input = request.get("input")
        decision = self._permission_decision(tool_name, tool_input)
        if decision is not None:
            await self.respond_permission(request_id, decision)
            return None
        return ClaudeCodeEvent(
            "permission_request",
            request_id=request_id,
            tool_name=tool_name,
            tool_input=summarize_tool_input(tool_name, tool_input),
            tool_input_raw=tool_input,
        )

    def _permission_decision(self, tool_name: str, tool_input: Any) -> PermissionResult | None:
        if self.permission_mode == "bypassPermissions":
            return PermissionResult("allow", tool_input)
        if self.permission_mode == "dontAsk":
            return PermissionResult("deny", None, "Permission mode is dontAsk")
        if self.permission_mode == "acceptEdits" and tool_name in EDIT_TOOLS:
            return PermissionResult("allow", tool_input)
        return PermissionResult("deny", None, f"Interactive permission prompt for {tool_name} is not available in workflow mode.")

    async def respond_permission(self, request_id: str, result: PermissionResult):
        if result.behavior == "allow":
            response = {"behavior": "allow", "updatedInput": result.updated_input or {}}
        else:
            response = {"behavior": "deny", "message": result.message or "The user denied this tool use."}
        self.write_json({
            "type": "control_response",
            "response": {"subtype": "success", "request_id": request_id, "response": response},
        })
        await self.drain()

    async def close(self):
        if self.process.returncode is not None:
            await self._finish_stderr()
            return
        if self.process.stdin is not None:
            with contextlib.suppress(Exception):
                self.process.stdin.close()
        if hasattr(self.process, "terminate"):
            self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=5)
        except asyncio.TimeoutError:
            if hasattr(self.process, "kill"):
                self.process.kill()
            await self.process.wait()
        await self._finish_stderr()

    async def wait(self):
        await self.process.wait()
        await self._finish_stderr()

    async def _read_stderr(self):
        if self.process.stderr is None:
            return
        while True:
            line = await self.process.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                self.stderr_parts.append(text)
                emit_event({"type": "agent_stderr", "agent": self.agent_name, "message": text})
                hook_event = parse_hook_blocked("\n".join(self.stderr_parts))
                if hook_event is not None:
                    emit_event({
                        "type": "agent_hook_blocked",
                        "agent": self.agent_name,
                        "hook_script": hook_event.hook_script,
                        "hook_command": hook_event.hook_command,
                        "message": hook_event.hook_message,
                    })

    async def _finish_stderr(self):
        if self._stderr_task.done():
            await self._stderr_task
            return
        self._stderr_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._stderr_task

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    @property
    def stderr_text(self) -> str:
        return "\n".join(self.stderr_parts).strip()


def extract_stream_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("result"), str):
        return payload["result"]
    if isinstance(payload.get("text"), str):
        return payload["text"]
    message = payload.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("input"), dict):
                    parts.append(json.dumps(item["input"], ensure_ascii=False))
                elif isinstance(item.get("name"), str):
                    parts.append(f"[tool] {item['name']}")
            return "\n".join(parts)
    delta = payload.get("delta")
    if isinstance(delta, dict):
        if isinstance(delta.get("text"), str):
            return delta["text"]
        if isinstance(delta.get("partial_json"), str):
            return delta["partial_json"]
    return ""


def summarize_tool_input(tool: str, input_value: Any) -> str:
    if not isinstance(input_value, dict):
        return ""
    if tool in {"Read", "Edit", "Write"}:
        return str(input_value.get("file_path") or "")
    if tool == "Bash":
        return str(input_value.get("command") or "")
    if tool in {"Grep", "Glob"}:
        return str(input_value.get("pattern") or "")
    return ""


def parse_hook_blocked(stderr_text: str) -> ClaudeCodeEvent | None:
    if "blocked by hook" not in stderr_text:
        return None
    match = HOOK_BLOCKED_RE.search(stderr_text)
    if not match:
        return None
    hook_script = match.group(1).strip()
    hook_message = match.group(2).strip()
    command_match = re.search(r"/[\w-]+", hook_message)
    return ClaudeCodeEvent(
        "hook_blocked",
        hook_script=hook_script,
        hook_message=hook_message,
        hook_command=command_match.group(0) if command_match else "",
    )
