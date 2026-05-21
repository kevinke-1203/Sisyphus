import unittest
from unittest.mock import Mock, patch

from evo.agents.api_tester import ApiTesterAgent
from evo.agents.base import AgentResponse
from evo.agents.registry import get_agent
from evo.orchestrator.parser import register_agents_from_config
from evo.agents.ui_tester import UiTesterAgent
from evo.orchestrator.engine import test_node


class SplitTesterAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_ui_tester_invokes_claude_code_with_hephaestus_skill(self):
        agent = UiTesterAgent()
        captured_prompt = ""

        async def fake_invoke(prompt):
            nonlocal captured_prompt
            captured_prompt = prompt
            return AgentResponse(text="RESULT: PASS\n\nui ok")

        agent.invoke_agent = fake_invoke
        result = await agent.execute({
            "task": "verify UI",
            "test_cases": "run ui cases",
            "task_dir": "/tmp/task",
            "worktree_path": "/tmp/worktree",
        })

        self.assertEqual(agent.config.skills, ["hephaestus-ui-test"])
        self.assertTrue(result["ui_test_passed"])
        self.assertIn("hephaestus-ui-test skill", agent.config.system_prompt)
        self.assertIn("Source worktree: /tmp/worktree", captured_prompt)
        self.assertIn("Do not run API tests", captured_prompt)

    async def test_api_tester_defaults_to_pass_until_external_skill_is_connected(self):
        agent = ApiTesterAgent()

        async def fail_if_invoked(_prompt):
            raise AssertionError("api_tester should not invoke Claude Code until external skill is connected")

        agent.invoke_agent = fail_if_invoked
        result = await agent.execute({
            "task": "verify API",
            "test_cases": "run interface cases",
            "task_dir": "/tmp/task",
            "worktree_path": "/tmp/worktree",
        })

        self.assertEqual(agent.config.skills, [])
        self.assertTrue(result["api_test_passed"])
        self.assertIn("external API testing", result["api_test_result"])

    async def test_test_node_aggregates_api_and_ui_results(self):
        manager = Mock()
        state = {
            "api_test_result": "RESULT: PASS\n\napi ok",
            "api_test_passed": True,
            "ui_test_result": "RESULT: FAIL\n\nui failed",
            "ui_test_passed": False,
            "_retries": {"code": 1},
            "task_dir": "/tmp/task",
        }

        with patch("evo.orchestrator.engine._get_task_manager", return_value=manager):
            result = await test_node(state)

        self.assertFalse(result["test_passed"])
        self.assertIn("## API Test Result", result["test_result"])
        self.assertIn("## UI Test Result", result["test_result"])
        self.assertIn("RESULT: FAIL", result["test_result"])
        manager.write_artifact.assert_called_once_with("/tmp/task", "test_result", result["test_result"])


class SplitTesterRegistryTests(unittest.TestCase):
    def test_builtin_registry_includes_split_testers(self):
        register_agents_from_config({"agents": {}})

        self.assertIsNotNone(get_agent("api_tester"))
        self.assertIsNotNone(get_agent("ui_tester"))
        self.assertEqual(get_agent("ui_tester").config.skills, ["hephaestus-ui-test"])


if __name__ == "__main__":
    unittest.main()
