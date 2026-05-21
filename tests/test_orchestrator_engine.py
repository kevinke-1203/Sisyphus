import unittest
from unittest.mock import Mock, patch

from evo.agents.base import AgentResponse
from evo.agents.coder import CoderAgent
from evo.orchestrator.engine import _test_router, archive_node


class TestRouterTests(unittest.TestCase):
    def test_failed_test_routes_back_to_code_before_retry_limit(self):
        state = {
            "test_passed": False,
            "_retries": {"code": 1, "plan": 1},
        }

        self.assertEqual(_test_router(state), "code")

    def test_retries_exhausted_ends_without_human_prompt(self):
        state = {
            "test_passed": False,
            "_retries": {"code": 3, "plan": 3},
        }

        self.assertEqual(_test_router(state), "end")


class TestArchiveNode(unittest.IsolatedAsyncioTestCase):
    async def test_archive_node_skips_merge_when_tests_failed(self):
        manager = Mock()
        state = {
            "test_passed": False,
            "task_id": "abc123",
            "task_dir": "/tmp/task",
        }

        with patch("evo.orchestrator.engine._get_task_manager", return_value=manager):
            result = await archive_node(state)

        manager.archive_task.assert_not_called()
        self.assertEqual(result["error"], "Archive skipped: workflow did not pass tests.")


class TestCoderAgent(unittest.IsolatedAsyncioTestCase):
    async def test_failed_test_result_is_passed_to_coder_prompt(self):
        agent = CoderAgent()
        captured_prompt = ""

        async def fake_invoke(prompt):
            nonlocal captured_prompt
            captured_prompt = prompt
            return AgentResponse(text="fixed")

        agent.invoke_agent = fake_invoke

        result = await agent.execute({
            "design_doc": "Update src/app.py",
            "test_result": "RESULT: FAIL\nAssertionError: expected 200, got 500",
            "test_passed": False,
        })

        self.assertEqual(result["code"], "fixed")
        self.assertIn("## Test Failure", captured_prompt)
        self.assertIn("AssertionError: expected 200, got 500", captured_prompt)
        self.assertIn("minimal, targeted fixes", captured_prompt)


if __name__ == "__main__":
    unittest.main()
