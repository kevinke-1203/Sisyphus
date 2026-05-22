import unittest

from evo.agents.base import AgentResponse
from evo.agents.planner import PlannerAgent


class PlannerAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_planner_generates_design_and_case_artifacts(self):
        agent = PlannerAgent()
        captured_prompt = ""

        async def fake_invoke(prompt):
            nonlocal captured_prompt
            captured_prompt = prompt
            return AgentResponse(text=(
                "===REQUIREMENTS_DOC===\nrequirements\n"
                "===DESIGN_DOC===\ndesign\n"
                "===CASE_INVENTORY===\napi cases found\n"
                "===BEHAVIOR_SPECS===\n### Requirement: Export\n"
                "#### Scenario: Successful export\n"
                "- **WHEN** exporting\n"
                "- **THEN** csv downloads\n"
                "===TEST_CASE_CHANGES===\n"
                "## Modified Existing Cases\n"
                "- path: test-cases/api-tests/export.md\n"
                "  reason: existing export coverage\n"
                "## Created New Cases\n"
                "===VERIFICATION_PLAN===\n# Verification Plan\n"
                "- test-cases/api-tests/export.md\n"
                "===TEST_CASES===\n# Verification Plan\n"
                "- test-cases/api-tests/export.md\n"
            ))

        agent.invoke_agent = fake_invoke
        result = await agent.execute({"task": "add export"})

        self.assertEqual(agent.config.allowed_tools, ["Read", "Write", "Edit", "Glob", "Grep"])
        self.assertEqual(agent.config.permission_mode, "acceptEdits")
        self.assertEqual(result["requirements_doc"], "requirements")
        self.assertEqual(result["design_doc"], "design")
        self.assertEqual(result["existing_test_case_inventory"], "api cases found")
        self.assertIn("### Requirement: Export", result["behavior_specs"])
        self.assertIn("test-cases/api-tests/export.md", result["test_case_changes"])
        self.assertEqual(result["test_cases"], result["verification_plan"])
        self.assertIn("test-cases/api-tests/", agent.config.system_prompt)
        self.assertIn("test-cases/ui-tests/", agent.config.system_prompt)
        self.assertIn("modify_existing", agent.config.system_prompt)
        self.assertIn("create_new", agent.config.system_prompt)
        self.assertIn("author the verification case files", captured_prompt)


if __name__ == "__main__":
    unittest.main()
