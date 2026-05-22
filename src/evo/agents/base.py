"""Base agent class for all Evo agents."""

import os
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv

from evo.agents.claude_code import ClaudeCodeClient, ClaudeCodeEvent, ClaudeCodeOptions
from evo.runtime_events import WorkflowCancelled, emit_event, get_user_messages, is_cancel_requested
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

    def _build_claude_client(self) -> ClaudeCodeClient:
        return ClaudeCodeClient(ClaudeCodeOptions(
            agent_name=self.config.name,
            cwd=self.config.cwd,
            model=self.config.model,
            system_prompt=self._get_system_prompt(),
            permission_mode=self.config.permission_mode,
            allowed_tools=self.config.allowed_tools,
            max_budget_usd=self.config.max_budget_usd,
        ))

    def _build_cli_command(self, session_id: str = "", resume: bool = False) -> list[str]:
        """Build a Claude Code CLI command using bidirectional stream-json stdio."""
        return self._build_claude_client().build_command(session_id=session_id, resume=resume)

    def _build_cli_env(self) -> dict[str, str]:
        """Build the environment for Claude Code CLI execution."""
        return self._build_claude_client().build_env()

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

    async def invoke_agent(self, prompt: str) -> AgentResponse:
        """Call Claude Code CLI through the bidirectional stream-json protocol."""
        return await self._invoke_agent_stream_json(prompt)

    def _emit_claude_event(self, event: ClaudeCodeEvent):
        if event.session_id and not event.content:
            emit_event({
                "type": "agent_output",
                "agent": self.config.name,
                "event": event.type,
                "session_id": event.session_id,
                "message": "",
            })
            return

        if event.type == "tool_use":
            message = f"[tool] {event.tool_name}"
            if event.tool_input:
                message = f"{message}: {event.tool_input}"
        elif event.type == "permission_request":
            message = f"[permission denied] {event.tool_name}: {event.tool_input}"
        else:
            message = event.content

        if message:
            emit_event({
                "type": "agent_output",
                "agent": self.config.name,
                "event": event.type,
                "message": message,
            })

    async def _read_claude_turn(
        self,
        session,
        interrupt_after_message_count: int | None = None,
    ) -> tuple[str, int, float, int, bool, str, bool]:
        text_parts: list[str] = []
        final_result = ""
        tokens = 0
        cost = 0.0
        turns = 0
        is_error = False
        session_id = session.session_id
        interrupted = False
        saw_result = False

        while True:
            try:
                event = await session.read_event_with_interrupt(timeout=0.5)
            except TimeoutError:
                event = None

            if event is None:
                if (
                    interrupt_after_message_count is not None
                    and len(get_user_messages()) > interrupt_after_message_count
                ):
                    interrupted = True
                    emit_event({
                        "type": "agent_interrupt",
                        "agent": self.config.name,
                        "message": "User message received; interrupting current agent run",
                    })
                    await session.close()
                    break
                if is_cancel_requested():
                    emit_event({
                        "type": "agent_cancel",
                        "agent": self.config.name,
                        "message": "Stop requested; terminating current agent run",
                    })
                    await session.close()
                    raise WorkflowCancelled("任务已停止")
                continue

            if event.type == "eof":
                break

            self._emit_claude_event(event)
            if event.session_id:
                session_id = event.session_id
            if event.type in {"text", "thinking"} and event.content:
                text_parts.append(event.content)
            if event.type == "result":
                final_result = event.content
                tokens = event.output_tokens
                cost = event.cost_usd
                turns = event.num_turns
                is_error = event.is_error
                saw_result = True
                break

        if not interrupted:
            if saw_result:
                await session.close()
            else:
                await session.wait()

        result_text = final_result.strip() or "\n".join(part for part in text_parts if part).strip()
        if not saw_result and session.returncode != 0 and session.stderr_text:
            if result_text:
                result_text = f"{result_text}\n\n[claude stderr]\n{session.stderr_text}"
            else:
                result_text = session.stderr_text
        return result_text, tokens, cost, turns, is_error, session_id, interrupted

    async def _invoke_agent_stream_json(self, prompt: str) -> AgentResponse:
        """Call Claude Code CLI and return a structured response."""
        session_id = str(uuid.uuid4())
        resume = False
        restart_count = 0
        prompt = self._apply_skill_commands(prompt)
        client = self._build_claude_client()

        while True:
            message_count = len(get_user_messages())
            cmd = client.build_command(session_id=session_id, resume=resume)

            if os.getenv("EVO_DEBUG"):
                print(f"\n[DEBUG] {self.config.name} prompt ({len(prompt)} chars):")
                print(f"  {prompt[:300]}{'...' if len(prompt) > 300 else ''}")
                print(f"[DEBUG] {self.config.name} command: {' '.join(cmd[:6])} ...")

            emit_event({
                "type": "agent_start",
                "agent": self.config.name,
                "message": f"Starting {self.config.name}",
            })

            session = await client.start_session(session_id=session_id, resume=resume)
            await session.send(prompt)
            await session.drain()

            result = await self._read_claude_turn(session, interrupt_after_message_count=message_count)
            result_text, tokens, cost, turns, is_error, session_id, interrupted = result
            if not session_id:
                session_id = session.session_id

            if interrupted and restart_count < 5:
                restart_count += 1
                new_messages = get_user_messages()[message_count:]
                prompt = self._format_user_messages(new_messages)
                resume = True
                emit_event({
                    "type": "agent_end",
                    "agent": self.config.name,
                    "message": f"Resuming {self.config.name} session with user message",
                    "returncode": session.returncode,
                })
                continue

            if session.returncode != 0 and not result_text:
                is_error = True
            logical_returncode = session.returncode
            if result_text and session.returncode in {-15, -9}:
                logical_returncode = 0
            emit_event({
                "type": "agent_end",
                "agent": self.config.name,
                "message": f"Finished {self.config.name} (exit {logical_returncode})",
                "returncode": logical_returncode,
            })

            if os.getenv("EVO_DEBUG"):
                print(f"[DEBUG] {self.config.name} response ({len(result_text)} chars):")
                print(f"  {result_text[:300]}{'...' if len(result_text) > 300 else ''}")
                print(f"  tokens={tokens}, cost=${cost:.4f}, returncode={logical_returncode}\n")

            return AgentResponse(
                text=result_text,
                tokens_used=tokens,
                cost_usd=cost,
                tool_calls=[],
                num_turns=turns,
                is_error=is_error,
                session_id=session_id,
            )
