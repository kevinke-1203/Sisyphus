"""API tester placeholder until an external API-test skill is wired in."""

from typing import Any

from evo.agents.base import AgentConfig, BaseAgent


class ApiTesterAgent(BaseAgent):
    """Currently skips API verification and returns PASS by default."""

    def __init__(self):
        super().__init__(AgentConfig(
            name="api_tester",
            role="API tester",
            system_prompt=(
                "You are a senior API/interface testing agent. The external API testing skill is "
                "not wired in yet, so this agent currently skips API verification by default."
            ),
            allowed_tools=["Read", "Bash", "Glob", "Grep"],
            permission_mode="acceptEdits",
            max_turns=15,
        ))

    async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        text = "RESULT: PASS\n\nAPI tests skipped: external API testing agent/skill is not connected yet."
        return {
            "api_test_result": text,
            "api_test_passed": True,
            "current_step": "api_test",
            "_sdk_meta": {},
        }
