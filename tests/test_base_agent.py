import asyncio
import json
import os
import unittest
from unittest.mock import patch
from typing import Any

from evo.agents.base import AgentConfig, BaseAgent
from evo.runtime_events import reset_event_callback, set_event_callback


class DummyAgent(BaseAgent):
    async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        return {}


class FixedUuid:
    def __str__(self):
        return "00000000-0000-4000-8000-000000000000"


class FakeStream:
    def __init__(self, lines: list[bytes]):
        self.lines = list(lines)

    async def readline(self):
        if self.lines:
            return self.lines.pop(0)
        return b""


class FakeStdin:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, data: bytes):
        self.data += data

    async def drain(self):
        return None

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, stdout_lines: list[bytes], stderr_lines: list[bytes] | None = None, returncode: int = 0):
        self.stdin = FakeStdin()
        self.stdout = FakeStream(stdout_lines)
        self.stderr = FakeStream(stderr_lines or [])
        self.returncode = returncode
        self.terminated = False

    async def wait(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.terminated = True
        self.returncode = -9


class BaseAgentTests(unittest.TestCase):
    def _result_line(self, result: str = "final answer", session_id: str = "session-123") -> bytes:
        return (json.dumps({
            "type": "result",
            "result": result,
            "session_id": session_id,
            "num_turns": 2,
            "total_cost_usd": 0.01,
            "usage": {"output_tokens": 7},
        }) + "\n").encode("utf-8")

    def test_invoke_agent_uses_direct_stream_json_transport(self):
        seen: dict[str, Any] = {}
        process = FakeProcess([self._result_line()])

        async def fake_create_subprocess_exec(*cmd, **kwargs):
            seen["cmd"] = list(cmd)
            seen["kwargs"] = kwargs
            return process

        with patch.dict(os.environ, {"CLAUDE_CODE_BIN": "/bin/claude-fake"}), patch(
            "evo.agents.base.uuid.uuid4", return_value=FixedUuid()
        ), patch("evo.agents.claude_code.asyncio.create_subprocess_exec", fake_create_subprocess_exec):
            agent = DummyAgent(AgentConfig(name="dummy", role="test"))
            response = asyncio.run(agent.invoke_agent("hello from prompt"))

        self.assertEqual(response.text, "final answer")
        self.assertEqual(response.session_id, "session-123")
        self.assertEqual(response.tokens_used, 7)
        self.assertEqual(response.num_turns, 2)
        self.assertIn("/bin/claude-fake", seen["cmd"])
        self.assertNotIn("--print", seen["cmd"])
        self.assertIn("--output-format", seen["cmd"])
        self.assertIn("--input-format", seen["cmd"])
        self.assertIn("--permission-prompt-tool", seen["cmd"])
        self.assertIn("stream-json", seen["cmd"])
        self.assertNotIn("hello from prompt", " ".join(seen["cmd"]))
        sent = json.loads(process.stdin.data.decode("utf-8"))
        self.assertEqual(sent["type"], "user")
        self.assertEqual(sent["message"]["role"], "user")
        self.assertEqual(sent["message"]["content"], "hello from prompt")

    def test_invoke_agent_prefixes_configured_skill_commands(self):
        process = FakeProcess([self._result_line()])

        async def fake_create_subprocess_exec(*cmd, **kwargs):
            return process

        with patch.dict(os.environ, {"CLAUDE_CODE_BIN": "/bin/claude-fake"}), patch(
            "evo.agents.base.uuid.uuid4", return_value=FixedUuid()
        ), patch("evo.agents.claude_code.asyncio.create_subprocess_exec", fake_create_subprocess_exec):
            agent = DummyAgent(AgentConfig(
                name="dummy",
                role="test",
                skills=["browser:browser", "/imagegen"],
            ))
            asyncio.run(agent.invoke_agent("hello from prompt"))

        sent = json.loads(process.stdin.data.decode("utf-8"))
        self.assertEqual(
            sent["message"]["content"],
            "/browser:browser\n/imagegen\n\nhello from prompt",
        )

    def test_build_cli_command_can_resume_persisted_session(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_BIN": "/bin/claude-fake"}):
            agent = DummyAgent(AgentConfig(name="dummy", role="test"))
            cmd = agent._build_cli_command(session_id="00000000-0000-4000-8000-000000000000", resume=True)

        self.assertIn("--resume", cmd)
        self.assertIn("00000000-0000-4000-8000-000000000000", cmd)
        self.assertNotIn("--session-id", cmd)
        self.assertNotIn("--no-session-persistence", cmd)

    def test_invoke_agent_emits_start_and_end_events(self):
        events: list[dict[str, Any]] = []
        process = FakeProcess([self._result_line("ok")])

        async def fake_create_subprocess_exec(*cmd, **kwargs):
            return process

        token = set_event_callback(events.append)
        try:
            with patch.dict(os.environ, {"CLAUDE_CODE_BIN": "/bin/claude-fake"}), patch(
                "evo.agents.base.uuid.uuid4", return_value=FixedUuid()
            ), patch("evo.agents.claude_code.asyncio.create_subprocess_exec", fake_create_subprocess_exec):
                agent = DummyAgent(AgentConfig(name="dummy", role="test"))
                response = asyncio.run(agent.invoke_agent("hello"))
        finally:
            reset_event_callback(token)

        self.assertEqual(response.text, "ok")
        event_types = [event["type"] for event in events]
        self.assertIn("agent_start", event_types)
        self.assertIn("agent_end", event_types)
        self.assertNotIn("task_update", event_types)


if __name__ == "__main__":
    unittest.main()
