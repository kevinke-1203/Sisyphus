"""UI tester agent shell that delegates browser testing to Claude Code skills."""

from typing import Any

from evo.agents.base import AgentConfig, BaseAgent


class UiTesterAgent(BaseAgent):
    """Invokes Claude Code for browser UI verification, typically via Hephaestus."""

    def __init__(self):
        super().__init__(AgentConfig(
            name="ui_tester",
            role="UI tester",
            system_prompt=(
                "You are a senior browser UI testing agent. Use the hephaestus-ui-test skill when "
                "available to run markdown browser UI test cases. Run from the source worktree, not "
                "from the Hephaestus checkout. Do not run API/backend verification; that belongs to "
                "the api_tester agent.\n\n"
                "Report with EXACTLY this leading line: RESULT: PASS or RESULT: FAIL. Then provide "
                "the pass/fail summary, failed case names, concrete failure reasons, and report/log paths."
            ),
            allowed_tools=["Read", "Bash", "Glob", "Grep"],
            permission_mode="acceptEdits",
            max_turns=20,
            skills=["hephaestus-ui-test"],
        ))

    async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        prompt = (
            f"## Task\n{state.get('task', '')}\n\n"
            f"## Planner Test Cases\n{state.get('test_cases', '')}\n\n"
            f"## API Test Result\n{state.get('api_test_result', '')}\n\n"
            "## Paths\n"
            f"- Task directory: {state.get('task_dir', '')}\n"
            f"- Source worktree: {state.get('worktree_path', '')}\n\n"
            "Run browser UI verification using the hephaestus-ui-test skill if UI cases exist or the "
            "planner requested UI verification. If there is no UI surface or no UI test case scope, "
            "return RESULT: PASS and explain that UI testing was skipped. Do not run API tests."
        )
        agent_response = await self.invoke_agent(prompt)
        passed = "RESULT: PASS" in agent_response.text.upper()
        return {
            "ui_test_result": agent_response.text,
            "ui_test_passed": passed,
            "current_step": "ui_test",
            "_sdk_meta": {
                "tokens_used": agent_response.tokens_used,
                "cost_usd": agent_response.cost_usd,
                "tool_calls": agent_response.tool_calls,
                "num_turns": agent_response.num_turns,
            },
        }
