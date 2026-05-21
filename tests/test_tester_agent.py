import unittest

from evo.agents.tester import TesterAgent


class TesterAgentConfigTests(unittest.TestCase):
    def test_tester_agent_does_not_allow_write_tool(self):
        agent = TesterAgent()

        self.assertNotIn("Write", agent.config.allowed_tools)
        self.assertEqual(agent.config.allowed_tools, ["Read", "Bash", "Glob", "Grep"])


if __name__ == "__main__":
    unittest.main()
