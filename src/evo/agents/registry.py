"""Agent registry - maps agent names to instances."""

from collections.abc import Sequence
from typing import Any

from evo.agents.base import BaseAgent, AgentConfig


class DynamicAgent(BaseAgent):
    """An agent created from YAML config at runtime."""

    def __init__(self, config: AgentConfig):
        super().__init__(config)

    async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        task = state.get("task", "")
        plan = state.get("plan", "")
        code = state.get("code", "")
        test_result = state.get("test_result", "")

        context_parts = []
        if plan:
            context_parts.append(f"Plan:\n{plan}")
        if code:
            context_parts.append(f"Code:\n{code}")
        if test_result and not state.get("test_passed", True):
            context_parts.append(f"Test feedback:\n{test_result}")

        prompt = f"Task: {task}\n\n" + "\n\n".join(context_parts) if context_parts else f"Task: {task}"
        agent_response = await self.invoke_agent(prompt)

        output_key = self.config.name.replace("-", "_")
        return {
            output_key: agent_response.text,
            "current_step": self.config.name,
            "_sdk_meta": {
                "tokens_used": agent_response.tokens_used,
                "cost_usd": agent_response.cost_usd,
                "tool_calls": agent_response.tool_calls,
                "num_turns": agent_response.num_turns,
            },
        }


def _coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _configured_skills(config: dict) -> list[str]:
    if "skills" in config:
        return _coerce_string_list(config.get("skills"))
    return _coerce_string_list(config.get("skill_commands"))


_registry: dict[str, BaseAgent] = {}


def apply_config_to_agent(agent: BaseAgent, config: dict):
    """Apply supported runtime config to an already-registered agent."""
    configured_skills = _configured_skills(config)
    if configured_skills:
        agent.config.skills = configured_skills


def register_agent(agent: BaseAgent):
    """Register an agent instance by name."""
    _registry[agent.name] = agent


def get_agent(name: str) -> BaseAgent | None:
    """Get a registered agent by name."""
    return _registry.get(name)


def register_from_config(name: str, config: dict) -> BaseAgent:
    """Create and register an agent from a config dict (YAML-sourced)."""
    agent_config = AgentConfig(
        name=name,
        role=config.get("role", name),
        model=config.get("model", ""),
        system_prompt=config.get("system_prompt", ""),
        temperature=config.get("temperature", 0.7),
        max_tokens=config.get("max_tokens", 4096),
        allowed_tools=config.get("allowed_tools", ["Read", "Glob", "Grep"]),
        permission_mode=config.get("permission_mode", "bypassPermissions"),
        max_turns=config.get("max_turns", 10),
        cwd=config.get("cwd", ""),
        max_budget_usd=config.get("max_budget_usd"),
        skills=_configured_skills(config),
    )
    agent = DynamicAgent(agent_config)
    register_agent(agent)
    return agent


def register_builtin_agents():
    """Register the built-in agents."""
    from evo.agents.planner import PlannerAgent
    from evo.agents.coder import CoderAgent
    from evo.agents.tester import TesterAgent
    from evo.agents.api_tester import ApiTesterAgent
    from evo.agents.ui_tester import UiTesterAgent

    register_agent(PlannerAgent())
    register_agent(CoderAgent())
    register_agent(TesterAgent())
    register_agent(ApiTesterAgent())
    register_agent(UiTesterAgent())


def list_agents() -> list[str]:
    """List all registered agent names."""
    return list(_registry.keys())
