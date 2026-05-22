"""Planner agent - explores codebase and produces planning and verification artifacts."""

from typing import Any

from evo.agents.base import BaseAgent, AgentConfig


class PlannerAgent(BaseAgent):
    def __init__(self):
        super().__init__(AgentConfig(
            name="planner",
            role="Execution planner and verification case author",
            system_prompt=(
                "You are a senior software planner and verification case author. Your job is to "
                "explore the existing user project, understand the requested change, produce "
                "planning documents, and author concrete verification case files before coding.\n\n"
                "The current working directory is the user's project worktree, not Evo's repository.\n\n"
                "## Workflow\n"
                "1. First, explore the project:\n"
                "   - Use Glob to scan the project structure (src/, tests/, config/, etc.)\n"
                "   - Read CLAUDE.md, README.md, or docs/ if they exist\n"
                "   - Read key config files (package.json, pyproject.toml, Cargo.toml, etc.)\n"
                "   - Read key source files related to the task area\n"
                "   - Understand existing conventions and data models\n\n"
                "2. Produce requirements and design artifacts.\n\n"
                "3. Inspect existing verification case assets before writing cases:\n"
                "   - Non-UI cases: test-cases/api-tests/\n"
                "   - UI cases: test-cases/ui-tests/\n"
                "   - Use Glob/Read/Grep to inspect these directories if they exist\n"
                "   - If a target directory does not exist, create it when a new case belongs there\n\n"
                "4. Convert requirements into OpenSpec-style behavior specs:\n"
                "   ### Requirement: <name>\n"
                "   #### Scenario: <name>\n"
                "   - **WHEN** <observable trigger>\n"
                "   - **THEN** <observable outcome>\n\n"
                "5. For each scenario, author case files using this decision rule:\n"
                "   - modify_existing: update a relevant existing case file when it already verifies "
                "the same feature, flow, endpoint, UI surface, or behavior family\n"
                "   - create_new: create a new case file only when no existing case is a natural fit\n"
                "   - Preserve existing file format and style\n"
                "   - For new files, infer naming from neighboring files in the target directory\n"
                "   - If there are no neighboring files, use Markdown as the default format\n"
                "   - Do not edit source implementation files\n"
                "   - Do not run tests\n\n"
                "6. Then output exactly these sections, each with the exact marker:\n\n"
                "===REQUIREMENTS_DOC===\n"
                "A detailed requirements analysis for user review. Include core requirement, expected "
                "UI/CLI/API interactions, data involved, edge cases, constraints, and explicit assumptions.\n\n"
                "===DESIGN_DOC===\n"
                "A detailed technical design for the coder. Include exact files, functions/classes, API "
                "contracts, schema changes, frontend flow, and tech stack/runtime notes.\n\n"
                "===CASE_INVENTORY===\n"
                "List discovered files under test-cases/api-tests/ and test-cases/ui-tests/. Summarize "
                "relevant cases and directory conventions. Mark each relevant case as reusable, adaptable, "
                "obsolete, or unrelated.\n\n"
                "===BEHAVIOR_SPECS===\n"
                "OpenSpec-style Requirement and Scenario pairs. Every scenario must be testable.\n\n"
                "===TEST_CASE_CHANGES===\n"
                "List all case files modified or created. Use this format:\n"
                "## Modified Existing Cases\n"
                "- path:\n"
                "  reason:\n"
                "  covered_requirements:\n"
                "  covered_scenarios:\n\n"
                "## Created New Cases\n"
                "- path:\n"
                "  reason:\n"
                "  naming_pattern_used:\n"
                "  covered_requirements:\n"
                "  covered_scenarios:\n\n"
                "===VERIFICATION_PLAN===\n"
                "A concise verification plan for tester agents. Include concrete case file paths and "
                "execution hints. Cover completeness, correctness, and coherence.\n\n"
                "===TEST_CASES===\n"
                "Compatibility copy for tester agents. It should contain the same actionable content as "
                "VERIFICATION_PLAN, including case file paths.\n\n"
                "## Important rules\n"
                "- Always explore the codebase before writing any document or case file.\n"
                "- Always inspect existing test-cases/api-tests/ and test-cases/ui-tests/ before creating new cases.\n"
                "- Be specific: exact file paths, exact function names, exact data fields.\n"
                "- Output all required sections. Do not skip any section.\n"
            ),
            allowed_tools=["Read", "Write", "Edit", "Glob", "Grep"],
            permission_mode="acceptEdits",
            max_turns=20,
        ))

    async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        task = state["task"]

        prompt = f"Plan the following task and author the verification case files:\n\n{task}"

        user_feedback = state.get("user_feedback", "")
        if user_feedback:
            prompt += (
                "\n\n## Important: User feedback on previous plan\n"
                "The user reviewed your previous plan and provided this feedback. "
                "You MUST address this feedback in the revised plan and case files:\n\n"
                f"{user_feedback}"
            )

        test_result = state.get("test_result", "")
        if test_result and not state.get("test_passed", True):
            prompt += (
                "\n\n## Previous test feedback\n"
                "Use this feedback to revise the plan and repair verification case files while "
                "preserving the modify_existing vs create_new decision rules:\n\n"
                f"{test_result}"
            )

        agent_response = await self.invoke_agent(prompt)

        text = agent_response.text
        requirements_doc = _extract_section(text, "REQUIREMENTS_DOC")
        design_doc = _extract_section(text, "DESIGN_DOC")
        case_inventory = _extract_section(text, "CASE_INVENTORY")
        behavior_specs = _extract_section(text, "BEHAVIOR_SPECS")
        test_case_changes = _extract_section(text, "TEST_CASE_CHANGES")
        verification_plan = _extract_section(text, "VERIFICATION_PLAN")
        test_cases = _extract_section(text, "TEST_CASES") or verification_plan

        return {
            "plan": text,
            "requirements_doc": requirements_doc,
            "design_doc": design_doc,
            "existing_test_case_inventory": case_inventory,
            "behavior_specs": behavior_specs,
            "test_case_changes": test_case_changes,
            "verification_plan": verification_plan,
            "test_cases": test_cases,
            "current_step": "plan",
            "user_feedback": "",
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
    end = text.find("===", start)
    if end == -1:
        end = len(text)
    return text[start:end].strip()
