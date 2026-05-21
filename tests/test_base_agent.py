import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from typing import Any

from evo.agents.base import AgentConfig, BaseAgent
from evo.runtime_events import reset_event_callback, set_event_callback


class DummyAgent(BaseAgent):
    async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        return {}


class FixedUuid:
    hex = "abc12345deadbeef"


class BaseAgentTests(unittest.TestCase):
    def test_invoke_agent_uses_tmux_transport_and_completion_marker(self):
        seen: dict[str, Any] = {}
        sent_text: list[tuple[str, str]] = []

        def fake_start(session_id, cmd, log_dir, cwd=""):
            seen["session_id"] = session_id
            seen["cmd"] = cmd
            seen["log_dir"] = log_dir
            seen["cwd"] = cwd
            return {
                "session": "evo-dummy-abc12345",
                "log_path": "/tmp/evo-dummy-abc12345.log",
                "cwd": cwd,
                "attach_command": "tmux attach -t evo-dummy-abc12345",
            }

        def fake_send_text(session, text):
            sent_text.append((session, text))

        with patch.dict(os.environ, {"CLAUDE_CODE_BIN": "/bin/claude-fake"}), patch(
            "evo.agents.base.uuid.uuid4", return_value=FixedUuid()
        ), patch("evo.agents.base.start_command_session", fake_start), patch(
            "evo.agents.base.tmux_send_text", fake_send_text
        ), patch(
            "evo.agents.base.read_log_tail",
            side_effect=[("", 0), ("final answer\nEVO_AGENT_DONE_abc12345\n", 32)],
        ), patch(
            "evo.agents.base.tmux_capture_pane",
            side_effect=[
                "Claude Code v2.1.144\n❯\n⏵⏵ bypass permissions on",
                "final answer",
            ],
        ), patch(
            "evo.agents.base.tmux_session_exists", return_value=True
        ), patch(
            "evo.agents.base.asyncio.sleep", new_callable=AsyncMock
        ):
            agent = DummyAgent(AgentConfig(name="dummy", role="test"))
            response = asyncio.run(agent.invoke_agent("hello from prompt"))

        self.assertEqual(response.text, "final answer")
        self.assertEqual(response.session_id, "evo-dummy-abc12345")
        self.assertEqual(seen["session_id"], "dummy-abc12345")
        self.assertIn("/bin/claude-fake", seen["cmd"])
        self.assertIn("--permission-mode", seen["cmd"])
        self.assertNotIn("hello from prompt", " ".join(seen["cmd"]))
        self.assertEqual(sent_text[0][0], "evo-dummy-abc12345")
        self.assertIn("hello from prompt", sent_text[0][1])
        self.assertIn("EVO_AGENT_DONE_abc12345", sent_text[0][1])

    def test_invoke_agent_prefixes_configured_skill_commands(self):
        sent_text: list[tuple[str, str]] = []

        def fake_start(session_id, cmd, log_dir, cwd=""):
            return {
                "session": "evo-dummy-abc12345",
                "log_path": "/tmp/evo-dummy-abc12345.log",
                "cwd": cwd,
                "attach_command": "tmux attach -t evo-dummy-abc12345",
            }

        def fake_send_text(session, text):
            sent_text.append((session, text))

        with patch.dict(os.environ, {"CLAUDE_CODE_BIN": "/bin/claude-fake"}), patch(
            "evo.agents.base.uuid.uuid4", return_value=FixedUuid()
        ), patch("evo.agents.base.start_command_session", fake_start), patch(
            "evo.agents.base.tmux_send_text", fake_send_text
        ), patch(
            "evo.agents.base.read_log_tail",
            side_effect=[("", 0), ("final answer\nEVO_AGENT_DONE_abc12345\n", 32)],
        ), patch(
            "evo.agents.base.tmux_capture_pane",
            side_effect=[
                "Claude Code v2.1.144\n❯\n⏵⏵ bypass permissions on",
                "final answer",
            ],
        ), patch(
            "evo.agents.base.tmux_session_exists", return_value=True
        ), patch(
            "evo.agents.base.asyncio.sleep", new_callable=AsyncMock
        ):
            agent = DummyAgent(AgentConfig(
                name="dummy",
                role="test",
                skills=["browser:browser", "/imagegen"],
            ))
            asyncio.run(agent.invoke_agent("hello from prompt"))

        self.assertTrue(sent_text)
        submitted = sent_text[0][1]
        self.assertTrue(submitted.startswith("/browser:browser\n/imagegen\n\nhello from prompt"))

    def test_extract_tmux_result_ignores_echoed_prompt_marker(self):
        transcript = (
            "hello from prompt\n\n"
            "When this agent step is complete, print the final result needed by the Evo workflow, "
            "then print this exact completion marker on its own line:\n"
            "EVO_AGENT_DONE_abc12345\n"
            "Do not print the completion marker until the step is actually complete.\n"
            "RESULT: FAIL\n\n"
            "test_example failed: expected 2, got 3\n"
            "EVO_AGENT_DONE_abc12345\n"
        )

        result = DummyAgent._extract_tmux_result(transcript, "EVO_AGENT_DONE_abc12345")

        self.assertEqual(result, "RESULT: FAIL\n\ntest_example failed: expected 2, got 3")

    def test_build_cli_command_can_resume_persisted_session(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_BIN": "/bin/claude-fake"}):
            agent = DummyAgent(AgentConfig(name="dummy", role="test"))
            cmd = agent._build_cli_command(session_id="00000000-0000-4000-8000-000000000000", resume=True)

        self.assertIn("--resume", cmd)
        self.assertIn("00000000-0000-4000-8000-000000000000", cmd)
        self.assertNotIn("--session-id", cmd)
        self.assertNotIn("--no-session-persistence", cmd)

    def test_invoke_agent_emits_tmux_task_update(self):
        events: list[dict[str, Any]] = []

        def fake_start(session_id, cmd, log_dir, cwd=""):
            return {
                "session": "evo-dummy-abc12345",
                "log_path": "/tmp/evo-dummy-abc12345.log",
                "cwd": cwd,
                "attach_command": "tmux attach -t evo-dummy-abc12345",
            }

        token = set_event_callback(events.append)
        try:
            with patch.dict(os.environ, {"CLAUDE_CODE_BIN": "/bin/claude-fake"}), patch(
                "evo.agents.base.uuid.uuid4", return_value=FixedUuid()
            ), patch("evo.agents.base.start_command_session", fake_start), patch(
                "evo.agents.base.tmux_send_text"
            ), patch(
                "evo.agents.base.read_log_tail",
                return_value=("ok\nEVO_AGENT_DONE_abc12345\n", 24),
            ), patch(
                "evo.agents.base.tmux_capture_pane", return_value="ok"
            ), patch(
                "evo.agents.base.tmux_session_exists", return_value=True
            ):
                agent = DummyAgent(AgentConfig(name="dummy", role="test"))
                response = asyncio.run(agent.invoke_agent("hello"))
        finally:
            reset_event_callback(token)

        self.assertEqual(response.text, "ok")
        task_updates = [event for event in events if event["type"] == "task_update"]
        self.assertTrue(task_updates)
        self.assertEqual(task_updates[0]["fields"]["tmux_session"], "evo-dummy-abc12345")

    def test_claude_trust_prompt_detection(self):
        screen = (
            "Quick safety check: Is this a project you created or one you trust?\n"
            "1. Yes, I trust this folder\n"
            "2. No, exit"
        )

        self.assertTrue(DummyAgent._is_claude_trust_prompt(screen))
        self.assertFalse(DummyAgent._is_claude_trust_prompt("Yes, I trust this folder"))

    def test_invoke_agent_accepts_claude_trust_prompt_once(self):
        sent_keys: list[tuple[str, tuple[str, ...]]] = []
        sent_text: list[tuple[str, str]] = []

        def fake_start(session_id, cmd, log_dir, cwd=""):
            return {
                "session": "evo-dummy-abc12345",
                "log_path": "/tmp/evo-dummy-abc12345.log",
                "cwd": cwd,
                "attach_command": "tmux attach -t evo-dummy-abc12345",
            }

        def fake_send_keys(session, *keys):
            sent_keys.append((session, keys))

        def fake_send_text(session, text):
            sent_text.append((session, text))

        trust_screen = (
            "Quick safety check: Is this a project you created or one you trust?\n"
            "1. Yes, I trust this folder\n"
            "2. No, exit"
        )

        with tempfile.TemporaryDirectory() as data_dir, tempfile.TemporaryDirectory() as cwd:
            with patch.dict(os.environ, {"CLAUDE_CODE_BIN": "/bin/claude-fake", "EVO_DATA_DIR": data_dir}), patch(
                "evo.agents.base.uuid.uuid4", return_value=FixedUuid()
            ), patch("evo.agents.base.start_command_session", fake_start), patch(
                "evo.agents.base.tmux_send_keys", fake_send_keys
            ), patch(
                "evo.agents.base.tmux_send_text", fake_send_text
            ), patch(
                "evo.agents.base.read_log_tail",
                side_effect=[("", 0), ("", 0), ("final answer\nEVO_AGENT_DONE_abc12345\n", 32)],
            ), patch(
                "evo.agents.base.tmux_capture_pane",
                side_effect=[trust_screen, "Claude Code v2.1.144\n❯\n⏵⏵ bypass permissions on", "final answer"],
            ), patch(
                "evo.agents.base.tmux_session_exists", return_value=True
            ), patch(
                "evo.agents.base.asyncio.sleep", new_callable=AsyncMock
            ):
                agent = DummyAgent(AgentConfig(name="dummy", role="test", cwd=cwd))
                response = asyncio.run(agent.invoke_agent("hello"))
                trusted_path = os.path.join(data_dir, "trusted_claude_dirs.json")
                with open(trusted_path, encoding="utf-8") as f:
                    trusted = json.load(f)

        self.assertEqual(response.text, "final answer")
        self.assertEqual(sent_keys, [("evo-dummy-abc12345", ("Enter",))])
        self.assertEqual(sent_text[0][0], "evo-dummy-abc12345")
        self.assertIn("hello", sent_text[0][1])
        self.assertEqual(trusted["directories"], [os.path.realpath(cwd)])

    def test_claude_trusted_dirs_are_keyed_by_resolved_cwd(self):
        with tempfile.TemporaryDirectory() as data_dir, tempfile.TemporaryDirectory() as cwd:
            with patch.dict(os.environ, {"EVO_DATA_DIR": data_dir}):
                self.assertFalse(DummyAgent._is_claude_cwd_trusted(cwd))
                DummyAgent._mark_claude_cwd_trusted(cwd)

                self.assertTrue(DummyAgent._is_claude_cwd_trusted(cwd))
                self.assertFalse(DummyAgent._is_claude_cwd_trusted(os.path.dirname(cwd)))


if __name__ == "__main__":
    unittest.main()
