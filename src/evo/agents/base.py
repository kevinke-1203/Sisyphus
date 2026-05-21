"""Base agent class for all Evo agents."""

import asyncio
import contextlib
import json
import os
import shutil
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from evo.config import get_data_dir
from evo.runtime_events import WorkflowCancelled, emit_event, get_user_messages, is_cancel_requested
from evo.tmux import (
    TmuxError,
    capture_pane as tmux_capture_pane,
    read_log_tail,
    send_keys as tmux_send_keys,
    send_text as tmux_send_text,
    session_exists as tmux_session_exists,
    start_command_session,
    stop_session as tmux_stop_session,
)

load_dotenv()


@dataclass
class AgentConfig:
    name: str
    role: str
    model: str = ""
    system_prompt: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096
    # Claude Code CLI-specific fields
    allowed_tools: list[str] = field(default_factory=lambda: ["Read", "Glob", "Grep"])
    permission_mode: str = "bypassPermissions"
    max_turns: int = 10
    cwd: str = ""
    max_budget_usd: float | None = None
    skills: list[str] = field(default_factory=list)


@dataclass
class AgentResponse:
    text: str
    tokens_used: int = 0
    cost_usd: float = 0.0
    tool_calls: list[dict] = field(default_factory=list)
    num_turns: int = 0
    is_error: bool = False
    session_id: str = ""


class BaseAgent(ABC):
    def __init__(self, config: AgentConfig):
        self.config = config

    @property
    def name(self) -> str:
        return self.config.name

    @abstractmethod
    async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        """Execute the agent's task given the current workflow state.
        Returns a dict of state updates."""
        ...

    def _get_system_prompt(self) -> str:
        """Get system prompt enhanced with any applied optimization patches."""
        base = self.config.system_prompt
        if not base:
            return ""
        try:
            from evo.evolution.optimizer import PromptOptimizer
            optimizer = PromptOptimizer()
            return optimizer.get_enhanced_prompt(self.config.name, base)
        except Exception:
            return base

    def _build_cli_command(self, session_id: str = "", resume: bool = False) -> list[str]:
        """Build a Claude Code CLI command from agent config and environment."""
        claude_bin = os.getenv("CLAUDE_CODE_BIN") or shutil.which("claude")
        if not claude_bin:
            raise RuntimeError("Claude Code CLI not found. Install `claude` or set CLAUDE_CODE_BIN.")

        cmd = [
            claude_bin,
            "--print",
            "--output-format",
            "stream-json",
            "--system-prompt",
            self._get_system_prompt(),
            "--permission-mode",
            self.config.permission_mode,
            "--include-partial-messages",
            "--verbose",
        ]

        if resume and session_id:
            cmd.extend(["--resume", session_id])
        elif session_id:
            cmd.extend(["--session-id", session_id])

        if self.config.allowed_tools:
            cmd.extend(["--allowedTools", ",".join(self.config.allowed_tools)])
        else:
            cmd.extend(["--tools", ""])

        if self.config.max_budget_usd is not None:
            cmd.extend(["--max-budget-usd", str(self.config.max_budget_usd)])

        return cmd

    def _build_cli_env(self) -> dict[str, str]:
        """Build the environment for Claude Code CLI execution."""
        return os.environ.copy()

    @staticmethod
    def _normalize_skill_command(skill: str) -> str:
        skill = skill.strip()
        if not skill:
            return ""
        if skill.startswith("/"):
            return skill
        return f"/{skill}"

    def _apply_skill_commands(self, prompt: str) -> str:
        commands = [
            command
            for command in (self._normalize_skill_command(skill) for skill in self.config.skills)
            if command
        ]
        if not commands:
            return prompt
        return "\n".join(commands) + "\n\n" + prompt

    def _build_interactive_cli_command(self) -> list[str]:
        """Build an interactive Claude Code CLI command for tmux transport."""
        claude_bin = os.getenv("CLAUDE_CODE_BIN") or shutil.which("claude")
        if not claude_bin:
            raise RuntimeError("Claude Code CLI not found. Install `claude` or set CLAUDE_CODE_BIN.")

        cmd = [
            claude_bin,
            "--system-prompt",
            self._get_system_prompt(),
            "--permission-mode",
            self.config.permission_mode,
        ]
        if self.config.allowed_tools:
            cmd.extend(["--allowedTools", ",".join(self.config.allowed_tools)])
        else:
            cmd.extend(["--tools", ""])
        if self.config.max_budget_usd is not None:
            cmd.extend(["--max-budget-usd", str(self.config.max_budget_usd)])
        return cmd

    @staticmethod
    def _is_claude_trust_prompt(screen: str) -> bool:
        return (
            "Quick safety check: Is this a project you created or one you trust?" in screen
            and "Yes, I trust this folder" in screen
            and "No, exit" in screen
        )

    @staticmethod
    def _is_claude_ready_prompt(screen: str) -> bool:
        return "Claude Code v" in screen and "❯" in screen and not BaseAgent._is_claude_trust_prompt(screen)

    @staticmethod
    def _resolve_cwd(cwd: str | None = None) -> str:
        return str(Path(cwd or os.getcwd()).expanduser().resolve())

    @staticmethod
    def _trusted_claude_dirs_path() -> str:
        return os.path.join(get_data_dir(), "trusted_claude_dirs.json")

    @classmethod
    def _load_trusted_claude_dirs(cls) -> set[str]:
        path = cls._trusted_claude_dirs_path()
        if not os.path.isfile(path):
            return set()
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return set()
        if not isinstance(data, dict):
            return set()
        dirs = data.get("directories", [])
        if not isinstance(dirs, list):
            return set()
        return {str(item) for item in dirs if isinstance(item, str)}

    @classmethod
    def _is_claude_cwd_trusted(cls, cwd: str) -> bool:
        return cls._resolve_cwd(cwd) in cls._load_trusted_claude_dirs()

    @classmethod
    def _mark_claude_cwd_trusted(cls, cwd: str):
        trusted = cls._load_trusted_claude_dirs()
        trusted.add(cls._resolve_cwd(cwd))
        path = cls._trusted_claude_dirs_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"directories": sorted(trusted)}, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _format_user_messages(user_messages: list[dict[str, Any]]) -> str:
        formatted_messages = []
        for item in user_messages:
            message = str(item.get("message", "")).strip()
            if not message:
                continue
            ts = item.get("ts", "")
            prefix = f"- {ts}: " if ts else "- "
            formatted_messages.append(f"{prefix}{message}")
        return (
            "The user interrupted the current running task with these messages. "
            "Treat them as high-priority instructions and continue from the current session:\n\n"
            + "\n".join(formatted_messages or ["- Continue."])
        )

    @staticmethod
    async def _terminate_process(process: asyncio.subprocess.Process):
        if process.returncode is not None:
            return
        if hasattr(process, "terminate"):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            if hasattr(process, "kill"):
                process.kill()
            await process.wait()

    @staticmethod
    def _extract_stream_text(payload: dict[str, Any]) -> str:
        """Extract useful display text from a Claude Code stream-json event."""
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

    async def _read_stream(
        self,
        process: asyncio.subprocess.Process,
        interrupt_after_message_count: int | None = None,
    ) -> tuple[str, str, int, float, int, bool, str, bool]:
        """Read Claude Code stream-json output while emitting progress events."""
        output_lines: list[str] = []
        text_parts: list[str] = []
        stderr_parts: list[str] = []
        final_result = ""
        tokens = 0
        cost = 0.0
        turns = 0
        is_error = False
        session_id = ""
        interrupted = False

        async def read_stderr():
            if process.stderr is None:
                return
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    stderr_parts.append(text)
                    emit_event({
                        "type": "agent_stderr",
                        "agent": self.config.name,
                        "message": text,
                    })

        stderr_task = asyncio.create_task(read_stderr())

        if process.stdout is not None:
            while True:
                line_task = asyncio.create_task(process.stdout.readline())
                while True:
                    done, _pending = await asyncio.wait({line_task}, timeout=0.5)
                    if done:
                        line = line_task.result()
                        break
                    if (
                        interrupt_after_message_count is not None
                        and len(get_user_messages()) > interrupt_after_message_count
                    ):
                        interrupted = True
                        line_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await line_task
                        emit_event({
                            "type": "agent_interrupt",
                            "agent": self.config.name,
                            "message": "User message received; interrupting current agent run",
                        })
                        await self._terminate_process(process)
                        break
                    if is_cancel_requested():
                        line_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await line_task
                        emit_event({
                            "type": "agent_cancel",
                            "agent": self.config.name,
                            "message": "Stop requested; terminating current agent run",
                        })
                        await self._terminate_process(process)
                        raise WorkflowCancelled("任务已停止")
                if interrupted:
                    break
                if not line:
                    break
                raw = line.decode("utf-8", errors="replace").rstrip()
                if not raw:
                    continue
                output_lines.append(raw)

                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    text_parts.append(raw)
                    emit_event({
                        "type": "agent_output",
                        "agent": self.config.name,
                        "message": raw,
                    })
                    continue

                event_type = str(payload.get("type") or "event")
                if event_type == "result" and isinstance(payload.get("result"), str):
                    final_result = payload["result"]

                message = self._extract_stream_text(payload)
                if message:
                    if event_type != "result":
                        text_parts.append(message)
                    emit_event({
                        "type": "agent_output",
                        "agent": self.config.name,
                        "event": event_type,
                        "message": message,
                    })
                if event_type == "result" or payload.get("result") is not None:
                    cost = float(payload.get("total_cost_usd") or payload.get("cost_usd") or cost)
                    turns = int(payload.get("num_turns") or turns)
                    is_error = bool(payload.get("is_error", is_error))
                    session_id = payload.get("session_id", session_id)
                    usage = payload.get("usage") or {}
                    tokens = int(
                        usage.get("output_tokens")
                        or usage.get("total_tokens")
                        or payload.get("tokens_used")
                        or tokens
                    )

        await stderr_task
        await process.wait()

        result_text = final_result.strip() or "\n".join(part for part in text_parts if part).strip()
        if not result_text:
            result_text = "\n".join(output_lines).strip()

        error_output = "\n".join(stderr_parts).strip()
        if process.returncode != 0 and not result_text:
            result_text = error_output
        elif process.returncode != 0 and error_output:
            result_text = f"{result_text}\n\n[claude stderr]\n{error_output}"

        return result_text, error_output, tokens, cost, turns, is_error, session_id, interrupted

    async def invoke_agent(self, prompt: str) -> AgentResponse:
        """Call Claude Code CLI through a tmux-backed interactive session."""
        return await self._invoke_agent_tmux(prompt)

    async def _invoke_agent_tmux(self, prompt: str) -> AgentResponse:
        cwd = self.config.cwd or None
        execution_cwd = self._resolve_cwd(cwd)
        run_id = uuid.uuid4().hex[:8]
        sentinel = f"EVO_AGENT_DONE_{run_id}"
        session_key = f"{self.config.name}-{run_id}"
        log_dir = os.path.join(get_data_dir(), "tmux_logs")
        session_id = ""
        log_path = ""

        prepared_prompt = self._apply_skill_commands(prompt).rstrip()
        wrapped_prompt = (
            f"{prepared_prompt}\n\n"
            "When this agent step is complete, print the final result needed by the Evo workflow, "
            f"then print this exact completion marker on its own line:\n{sentinel}\n"
            "Do not print the completion marker until the step is actually complete."
        )

        emit_event({
            "type": "agent_start",
            "agent": self.config.name,
            "message": f"Starting {self.config.name}",
        })
        try:
            session = start_command_session(
                session_key,
                self._build_interactive_cli_command(),
                log_dir,
                cwd=execution_cwd,
            )
            session_id = session["session"]
            log_path = session["log_path"]
            emit_event({
                "type": "task_update",
                "fields": {
                    "transport": "tmux",
                    "tmux_session": session_id,
                    "tmux_log_path": log_path,
                    "tmux_cwd": session["cwd"],
                    "tmux_attach_command": session["attach_command"],
                    "tmux_alive": True,
                },
            })
            emit_event({
                "type": "agent_output",
                "agent": self.config.name,
                "message": f"tmux session: {session['attach_command']}",
            })
            last_screen = ""
            trust_prompt_accepted = False
            prompt_sent = False
            while True:
                if is_cancel_requested():
                    tmux_stop_session(session_id)
                    raise WorkflowCancelled("任务已停止")

                log_text, _size = read_log_tail(log_path, max_bytes=200000)
                screen = tmux_capture_pane(session_id)
                if screen and screen != last_screen:
                    last_screen = screen
                    emit_event({
                        "type": "task_update",
                        "fields": {
                            "tmux_screen": screen,
                            "tmux_alive": tmux_session_exists(session_id),
                        },
                    })
                if not trust_prompt_accepted and self._is_claude_trust_prompt(screen):
                    trust_prompt_accepted = True
                    known_trusted = self._is_claude_cwd_trusted(execution_cwd)
                    tmux_send_keys(session_id, "Enter")
                    self._mark_claude_cwd_trusted(execution_cwd)
                    message = "Accepted Claude Code workspace trust prompt for known directory."
                    if not known_trusted:
                        message = "Accepted Claude Code workspace trust prompt and remembered this directory."
                    emit_event({
                        "type": "agent_output",
                        "agent": self.config.name,
                        "message": message,
                    })
                if not prompt_sent and self._is_claude_ready_prompt(screen):
                    prompt_sent = True
                    tmux_send_text(session_id, wrapped_prompt)
                    emit_event({
                        "type": "agent_output",
                        "agent": self.config.name,
                        "message": "Submitted prompt to Claude Code tmux session.",
                    })
                if sentinel in log_text or sentinel in screen:
                    result_text = self._extract_tmux_result(log_text or screen, sentinel)
                    emit_event({
                        "type": "agent_end",
                        "agent": self.config.name,
                        "message": f"Finished {self.config.name} in {session_id}",
                        "returncode": 0,
                    })
                    return AgentResponse(
                        text=result_text,
                        session_id=session_id,
                    )
                if not tmux_session_exists(session_id):
                    result_text = self._extract_tmux_result(log_text or screen, sentinel)
                    emit_event({
                        "type": "agent_end",
                        "agent": self.config.name,
                        "message": f"Finished {self.config.name} in {session_id}",
                        "returncode": 0,
                    })
                    return AgentResponse(
                        text=result_text,
                        session_id=session_id,
                        is_error=not bool(result_text.strip()),
                    )
                await asyncio.sleep(0.8)
        except TmuxError as e:
            emit_event({
                "type": "agent_end",
                "agent": self.config.name,
                "message": f"Failed {self.config.name}: {e}",
                "returncode": 1,
            })
            return AgentResponse(text=str(e), is_error=True, session_id=session_id)

    @staticmethod
    def _extract_tmux_result(text: str, sentinel: str) -> str:
        clean = text.replace("\r", "")
        marker = "then print this exact completion marker on its own line:"
        if marker in clean:
            before_marker, after_marker = clean.split(marker, 1)
            after_lines = after_marker.splitlines()
            for index, line in enumerate(after_lines):
                if sentinel in line:
                    result_lines = after_lines[index + 1:]
                    if result_lines and result_lines[0].startswith("Do not print the completion marker"):
                        result_lines = result_lines[1:]
                    clean = "\n".join(result_lines)
                    break
            else:
                clean = before_marker
        if sentinel in clean:
            clean = clean.split(sentinel, 1)[0]
        return clean.strip()

    async def _invoke_agent_stream_json(self, prompt: str) -> AgentResponse:
        """Call Claude Code CLI and return a structured response."""
        cwd = self.config.cwd or None
        session_id = str(uuid.uuid4())
        resume = False
        restart_count = 0
        prompt = self._apply_skill_commands(prompt)

        while True:
            message_count = len(get_user_messages())
            cmd = self._build_cli_command(session_id=session_id, resume=resume)

            if os.getenv("EVO_DEBUG"):
                print(f"\n[DEBUG] {self.config.name} prompt ({len(prompt)} chars):")
                print(f"  {prompt[:300]}{'...' if len(prompt) > 300 else ''}")
                print(f"[DEBUG] {self.config.name} command: {' '.join(cmd[:6])} ...")

            emit_event({
                "type": "agent_start",
                "agent": self.config.name,
                "message": f"Starting {self.config.name}",
            })
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                env=self._build_cli_env(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            if process.stdin is not None:
                process.stdin.write(prompt.encode("utf-8"))
                await process.stdin.drain()
                process.stdin.close()

            result = await self._read_stream(process, interrupt_after_message_count=message_count)
            result_text, _error_output, tokens, cost, turns, is_error, session_id, interrupted = result
            if not session_id and "--session-id" in cmd:
                session_id = cmd[cmd.index("--session-id") + 1]
            if interrupted and restart_count < 5:
                restart_count += 1
                new_messages = get_user_messages()[message_count:]
                prompt = self._format_user_messages(new_messages)
                resume = True
                emit_event({
                    "type": "agent_end",
                    "agent": self.config.name,
                    "message": f"Resuming {self.config.name} session with user message",
                    "returncode": process.returncode,
                })
                continue
            if process.returncode != 0:
                is_error = True
            emit_event({
                "type": "agent_end",
                "agent": self.config.name,
                "message": f"Finished {self.config.name} (exit {process.returncode})",
                "returncode": process.returncode,
            })

            if os.getenv("EVO_DEBUG"):
                print(f"[DEBUG] {self.config.name} response ({len(result_text)} chars):")
                print(f"  {result_text[:300]}{'...' if len(result_text) > 300 else ''}")
                print(f"  tokens={tokens}, cost=${cost:.4f}, returncode={process.returncode}\n")

            return AgentResponse(
                text=result_text,
                tokens_used=tokens,
                cost_usd=cost,
                tool_calls=[],
                num_turns=turns,
                is_error=is_error,
                session_id=session_id,
            )
