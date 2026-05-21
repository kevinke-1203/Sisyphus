"""Tester agent - runs pytest against source code changes."""

from typing import Any

from evo.agents.base import BaseAgent, AgentConfig


class TesterAgent(BaseAgent):
    def __init__(self):
        super().__init__(AgentConfig(
            name="tester",
            role="Code tester",
            system_prompt=(
                "You are a senior testing agent. Your job is to independently verify that the source "
                "code changes correctly implement the task by writing and running pytest test cases.\n\n"
                "## Important Distinction\n"
                "Your pytest test files are evo VERIFICATION tests — they verify the implementation "
                "from the outside. They are separate from any test files the task may require as "
                "deliverables (which the coder creates as part of source code).\n\n"
                "## Workflow\n"
                "1. Read the test cases from the planner's output.\n"
                "2. If the test cases specify a service startup command, start the service first.\n"
                "3. Create a single pytest test file (test_verify.py) in the task directory.\n"
                "4. Run pytest and collect results.\n"
                "5. After testing, stop any services you started.\n\n"
                "## Testing Strategy by Project Type\n"
                "- Python project: import the module and test directly.\n"
                "- API service (any language): start the service, test via HTTP requests with `requests` "
                "or `httpx` library.\n"
                "- Pure function/library in non-Python project: test via subprocess calls (e.g., "
                "`bun -e`, `node -e`, `go run`). Keep it simple — one subprocess call per assertion.\n"
                "- Do NOT try to run the project's own test framework (bun test, jest, etc.) from "
                "pytest. That adds unnecessary complexity and failure points.\n\n"
                "## Rules\n"
                "- Backend verification tests are ALWAYS run with pytest.\n"
                "- Create test files ONLY in the task directory, NEVER in the source worktree.\n"
                "- Actually run the tests with Bash — do not just reason about them mentally.\n"
                "- If a test approach doesn't work after 2 attempts, switch to a simpler approach.\n"
                "- Report specific failures: which test, what was expected, what actually happened.\n\n"
                "## Output Format\n"
                "After testing, respond with EXACTLY this format:\n"
                "RESULT: PASS or RESULT: FAIL\n\n"
                "Then provide a detailed explanation of what was tested and what passed/failed."
            ),
            allowed_tools=["Read", "Bash", "Glob", "Grep"],
            permission_mode="acceptEdits",
            max_turns=15,
        ))

    async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        task = state["task"]
        test_cases = state.get("test_cases", "")
        task_dir = state.get("task_dir", "")
        worktree_path = state.get("worktree_path", "")

        prompt = (
            f"## Task\n{task}\n\n"
            f"## Test Cases\n{test_cases}\n\n"
            f"## Paths\n"
            f"- Task directory (create test files HERE): {task_dir}\n"
            f"- Source worktree (read-only, do NOT write test files here): {worktree_path}\n\n"
        )
        prompt += (
            "Follow these steps:\n"
            "1. If the test cases specify a service startup command, start the service from the worktree.\n"
            "2. Create pytest test files in the task directory based on the test cases.\n"
            "3. Run pytest and collect results.\n"
            "4. Stop any services you started.\n"
            "5. Respond with RESULT: PASS or RESULT: FAIL, then explain.\n\n"
            "Remember: test files go in the task directory, NOT in the worktree."
        )

        agent_response = await self.invoke_agent(prompt)
        passed = "RESULT: PASS" in agent_response.text.upper()
        return {
            "test_result": agent_response.text,
            "test_passed": passed,
            "current_step": "test",
            "_sdk_meta": {
                "tokens_used": agent_response.tokens_used,
                "cost_usd": agent_response.cost_usd,
                "tool_calls": agent_response.tool_calls,
                "num_turns": agent_response.num_turns,
            },
        }
