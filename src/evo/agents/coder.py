"""Coder agent - implements source code changes based on design doc."""

from typing import Any

from evo.agents.base import BaseAgent, AgentConfig


class CoderAgent(BaseAgent):
    def __init__(self):
        super().__init__(AgentConfig(
            name="coder",
            role="Code implementer",
            system_prompt=(
                "You are a senior coding agent. Your job is to implement source code changes "
                "based on the design document provided by the planner.\n\n"
                "## Workflow\n"
                "1. Read the design document carefully — it specifies exact files, functions, and changes.\n"
                "2. Explore the relevant parts of the codebase to understand existing conventions.\n"
                "3. Implement the changes exactly as described in the design doc.\n\n"
                "## CRITICAL RULES\n"
                "- Implement EXACTLY what the design document specifies — no more, no less.\n"
                "- If the design doc asks you to create test files as part of the deliverable, create them.\n"
                "- Do NOT run tests (pytest, bun test, npm test, etc.). Testing is handled by a "
                "separate agent. Your job is ONLY to write code, not to verify it.\n"
                "- Do NOT proactively create verification test scripts on your own.\n"
                "- Match existing code style and conventions in the project.\n"
                "- If the design doc references specific files or paths, use those exactly.\n"
                "- If a test retry fails, read the test failure carefully and fix only what's broken.\n"
                "- Use your tools (Read, Write, Edit, Bash) to actually create/modify files — "
                "do not just output code in text."
            ),
            allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
            permission_mode="acceptEdits",
            max_turns=15,
        ))

    async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        design_doc = state.get("design_doc", "")
        test_result = state.get("test_result", "")
        test_passed = state.get("test_passed", True)

        if test_result and not test_passed:
            # Retry: include failure details
            prompt = (
                f"## Design Document\n{design_doc}\n\n"
                f"## Test Failure\n{test_result}\n\n"
                f"The source code you previously wrote failed the tests above. "
                f"Read the failure carefully and fix the specific issue in the source code. "
                f"Do not rewrite everything — make minimal, targeted fixes."
            )
        else:
            prompt = f"## Design Document\n{design_doc}\n\n"
            if design_doc:
                prompt += "Implement the source code changes described in the design document above. "
            else:
                prompt += f"Implement the following task:\n{state.get('task', '')}\n\n"
            prompt += "Remember: implement exactly what the design document specifies."

        agent_response = await self.invoke_agent(prompt)
        return {
            "code": agent_response.text,
            "current_step": "code",
            "_sdk_meta": {
                "tokens_used": agent_response.tokens_used,
                "cost_usd": agent_response.cost_usd,
                "tool_calls": agent_response.tool_calls,
                "num_turns": agent_response.num_turns,
            },
        }
