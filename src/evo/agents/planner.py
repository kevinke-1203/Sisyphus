"""Planner agent - explores codebase and produces structured planning documents."""

from typing import Any

from evo.agents.base import BaseAgent, AgentConfig


class PlannerAgent(BaseAgent):
    def __init__(self):
        super().__init__(AgentConfig(
            name="planner",
            role="Execution planner",
            system_prompt=(
                "You are a senior software planner. Your job is to explore the existing codebase, "
                "understand the architecture, and produce three structured documents for a given task.\n\n"
                "## Workflow\n"
                "1. First, explore the project:\n"
                "   - Use Glob to scan the project structure (src/, tests/, config/, etc.)\n"
                "   - Read CLAUDE.md, README.md, or any docs/ if they exist\n"
                "   - Read key config files (package.json, pyproject.toml, Cargo.toml, etc.) "
                "to identify the tech stack\n"
                "   - Read key source files related to the task area\n"
                "   - Understand the existing conventions and data models\n\n"
                "2. Then produce exactly these three documents, each in its own section:\n\n"
                "### ===REQUIREMENTS_DOC===\n"
                "A detailed requirements analysis for the user to review. Include:\n"
                "   - Core requirement: what the task is asking for\n"
                "   - Specific interactions: UI/CLI/API behaviors expected\n"
                "   - Data involved: which tables/models, what fields, relationships\n"
                "   - Edge cases and constraints\n"
                "   Write this in clear, non-technical language so the user can confirm understanding.\n\n"
                "### ===DESIGN_DOC===\n"
                "A detailed technical design for the coder agent. Include:\n"
                "   - Files to create or modify (with exact paths)\n"
                "   - For each file: what to add/change, function signatures, class structures\n"
                "   - API endpoint definitions (method, path, request/response schema)\n"
                "   - Database schema changes (table, columns, types, constraints)\n"
                "   - Frontend component structure and interaction flow\n"
                "   - Tech stack summary (language, framework, runtime) — this informs the tester "
                "on how to start services\n"
                "   Be precise and concrete — the coder will implement exactly what you specify.\n\n"
                "### ===TEST_CASES===\n"
                "Test cases for the tester agent. IMPORTANT RULES:\n"
                "   - Backend tests are ALWAYS written as pytest functions, regardless of the project's "
                "language or framework. Even if the project uses Node.js, Go, Java, etc., the test "
                "cases must be pytest functions.\n"
                "   - For API/service projects: write pytest functions that send HTTP requests to the "
                "running service. Include the service startup command in a comment.\n"
                "   - For library/function projects: write pytest functions that test the functionality "
                "directly (via subprocess, HTTP, or any mechanism that works with pytest).\n"
                "   - Frontend UI tests: write as browser-harness interaction checklists.\n"
                "   - Include: happy path, edge cases, error handling tests.\n"
                "   - If the project is not Python, specify how to run/start the service in the "
                "test_cases so the tester knows how to prepare the environment.\n\n"
                "## Important rules\n"
                "- Always explore the codebase BEFORE writing any document. Do not assume project structure.\n"
                "- Be specific: exact file paths, exact function names, exact data fields. No vagueness.\n"
                "- If the task is unclear or ambiguous, state your assumptions explicitly in the requirements doc.\n"
                "- Output all three sections. Do not skip any section.\n"
                "- Each section must start with the exact marker: ===REQUIREMENTS_DOC===, ===DESIGN_DOC===, ===TEST_CASES==="
            ),
            allowed_tools=["Read", "Glob", "Grep"],
            max_turns=15,
        ))

    async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        task = state["task"]

        # Build prompt — include user feedback if this is a retry
        prompt = f"Plan the following task:\n\n{task}"

        user_feedback = state.get("user_feedback", "")
        if user_feedback:
            prompt += (
                f"\n\n## Important: User feedback on previous plan\n"
                f"The user reviewed your previous plan and provided this feedback. "
                f"You MUST address this feedback in your revised plan:\n\n{user_feedback}"
            )

        agent_response = await self.invoke_agent(prompt)

        # Parse structured output
        text = agent_response.text
        requirements_doc = _extract_section(text, "REQUIREMENTS_DOC")
        design_doc = _extract_section(text, "DESIGN_DOC")
        test_cases = _extract_section(text, "TEST_CASES")

        return {
            "plan": text,
            "requirements_doc": requirements_doc,
            "design_doc": design_doc,
            "test_cases": test_cases,
            "current_step": "plan",
            "user_feedback": "",  # Clear feedback after consuming it
            "_sdk_meta": {
                "tokens_used": agent_response.tokens_used,
                "cost_usd": agent_response.cost_usd,
                "tool_calls": agent_response.tool_calls,
                "num_turns": agent_response.num_turns,
            },
        }


def _extract_section(text: str, marker: str) -> str:
    """Extract content between ===MARKER=== and the next === marker or end of text."""
    start_tag = f"==={marker}==="
    start = text.find(start_tag)
    if start == -1:
        return ""
    start += len(start_tag)
    # Find the next === marker
    end = text.find("===", start)
    if end == -1:
        end = len(text)
    return text[start:end].strip()
