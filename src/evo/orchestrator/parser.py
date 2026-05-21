"""YAML workflow parser - converts YAML config to LangGraph graphs."""

import os
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping

import yaml
from langgraph.graph import StateGraph, END

from evo.orchestrator.state import WorkflowState
from evo.agents.registry import apply_config_to_agent, register_from_config, register_builtin_agents, get_agent
from evo.runtime_events import check_cancelled


_CONFIG_ENV_VAR = "EVO_WORKFLOW_CONFIG"


def _read_yaml_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _resolve_config_path(config_path: str) -> Path:
    path = Path(config_path).expanduser()
    if path.is_dir():
        for filename in ("workflows.yaml", "workflow.yaml"):
            candidate = path / filename
            if candidate.exists():
                return candidate
        raise FileNotFoundError(f"Config not found in directory: {path}")
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    return path


def load_config(config_path: str | None = None) -> dict:
    """Load the workflows.yaml config file."""
    explicit_path = config_path or os.environ.get(_CONFIG_ENV_VAR, "")
    if explicit_path:
        return _read_yaml_config(_resolve_config_path(explicit_path))

    candidates = [
        Path.cwd() / "config" / "workflows.yaml",
        Path.cwd() / "config" / "workflow.yaml",
        Path.cwd() / "workflows.yaml",
        Path.cwd() / "workflow.yaml",
        Path.home() / ".evo" / "config" / "workflows.yaml",
        Path.home() / ".evo" / "config" / "workflow.yaml",
    ]
    for candidate in candidates:
        if candidate.exists():
            return _read_yaml_config(candidate)

    default_config = files("evo.defaults").joinpath("workflows.yaml")
    with default_config.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def register_agents_from_config(config: dict):
    """Register all agents defined in the config."""
    register_builtin_agents()
    agents_config = config.get("agents", {})
    for name, agent_conf in agents_config.items():
        agent = get_agent(name)
        if agent is None:
            register_from_config(name, agent_conf)
        else:
            apply_config_to_agent(agent, agent_conf)


def _evaluate_condition(condition: str, state: Mapping[str, Any]) -> bool:
    """Evaluate a condition string against state.

    Supports:
      - key == value, key != value
      - key >= value, key <= value, key > value, key < value
      - "needle" in key
      - compound conditions with 'and'
    """
    # Split on ' and ' for compound conditions
    if " and " in condition:
        parts = condition.split(" and ", 1)
        return _evaluate_condition(parts[0].strip(), state) and _evaluate_condition(parts[1].strip(), state)

    # "needle" in haystack_key
    if " in " in condition:
        needle, haystack_key = condition.split(" in ", 1)
        needle = needle.strip().strip('"').strip("'")
        haystack_key = haystack_key.strip()
        haystack = str(state.get(haystack_key, ""))
        return needle in haystack

    # Comparison operators (check >= and <= before > and <)
    for op in (">=", "<=", "!=", "==", ">", "<"):
        if op in condition:
            key, value = condition.split(op, 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            # Resolve state value — support _retries.node_name syntax
            if key.startswith("_retries."):
                node_name = key[len("_retries."):]
                state_val = state.get("_retries", {}).get(node_name, 0)
            else:
                state_val = state.get(key, "")
            # Try numeric comparison
            try:
                num_state = float(state_val) if not isinstance(state_val, (int, float)) else state_val
                num_value = float(value)
                if op == "==": return num_state == num_value
                if op == "!=": return num_state != num_value
                if op == ">=": return num_state >= num_value
                if op == "<=": return num_state <= num_value
                if op == ">": return num_state > num_value
                if op == "<": return num_state < num_value
            except (ValueError, TypeError):
                pass
            # Fallback to string comparison
            str_state = str(state_val).lower()
            str_value = value.lower()
            if op == "==": return str_state == str_value
            if op == "!=": return str_state != str_value
            return False

    # No operator — treat as truthy
    return True


# Built-in engine nodes that are not agent-based
_BUILTIN_NODES = {"setup", "confirm", "test", "archive"}


def _get_confirm_node_fn(skip_confirm: bool = False):
    """Return the confirm node function with skip_confirm wired in."""
    from evo.orchestrator.engine import confirm_node as _engine_confirm

    async def confirm_fn(state: WorkflowState) -> dict:
        check_cancelled()
        # Temporarily patch the skip_confirm flag for this invocation
        import evo.orchestrator.engine as engine_mod
        original = engine_mod._skip_confirm
        engine_mod._skip_confirm = skip_confirm
        try:
            return _engine_confirm(state)
        finally:
            engine_mod._skip_confirm = original
    return confirm_fn


def _get_setup_node_fn():
    """Return the setup node function."""
    from evo.orchestrator.engine import setup_node as _engine_setup

    async def setup_fn(state: WorkflowState) -> dict:
        check_cancelled()
        return await _engine_setup(state)
    return setup_fn




def _get_test_node_fn():
    """Return the test aggregation node function."""
    from evo.orchestrator.engine import test_node as _engine_test

    async def test_fn(state: WorkflowState) -> dict:
        check_cancelled()
        return await _engine_test(state)
    return test_fn

def _get_archive_node_fn():
    """Return the archive node function."""
    from evo.orchestrator.engine import archive_node as _engine_archive

    async def archive_fn(state: WorkflowState) -> dict:
        check_cancelled()
        return await _engine_archive(state)
    return archive_fn


# Map built-in node names to their factory functions
_BUILTIN_NODE_FACTORIES = {
    "setup": lambda _skip: _get_setup_node_fn(),
    "confirm": lambda skip: _get_confirm_node_fn(skip),
    "test": lambda _skip: _get_test_node_fn(),
    "archive": lambda _skip: _get_archive_node_fn(),
}


def build_graph_from_config(workflow_config: dict) -> tuple:
    """Build a LangGraph StateGraph from a workflow config dict.

    Returns (compiled_graph, max_iterations).
    """
    nodes = workflow_config.get("nodes", [])
    edges = workflow_config.get("edges", [])
    max_iterations = workflow_config.get("max_iterations", 5)
    skip_confirm = workflow_config.get("skip_confirm", False)

    graph = StateGraph(WorkflowState)

    # Add agent-based nodes from config
    for node_conf in nodes:
        node_name = node_conf["name"]

        # Handle built-in engine nodes
        if node_name in _BUILTIN_NODES:
            factory = _BUILTIN_NODE_FACTORIES.get(node_name)
            if factory:
                graph.add_node(node_name, factory(skip_confirm))
            continue

        agent_name = node_conf.get("agent", node_name)
        agent = get_agent(agent_name)
        if agent is None:
            raise ValueError(f"Agent '{agent_name}' not found in registry")

        def make_node_fn(ag, nname):
            async def node_fn(state: WorkflowState) -> dict:
                check_cancelled()
                print(f"[Evo] Running {nname}...")
                result = await ag.execute(state)
                # Auto-increment per-node retry counter
                retries = dict(state.get("_retries", {}))
                retries[nname] = retries.get(nname, 0) + 1
                result["_retries"] = retries
                return result
            return node_fn

        graph.add_node(node_name, make_node_fn(agent, node_name))

    # Ensure built-in nodes exist if edges reference them (even if not in nodes list)
    for builtin_name in _BUILTIN_NODES:
        if any(e.get("from") == builtin_name or e.get("to") == builtin_name for e in edges):
            try:
                graph.nodes[builtin_name]
            except KeyError:
                factory = _BUILTIN_NODE_FACTORIES.get(builtin_name)
                if factory:
                    graph.add_node(builtin_name, factory(skip_confirm))

    if nodes:
        graph.set_entry_point(nodes[0]["name"])

    conditional_sources = set()
    for edge in edges:
        if edge.get("condition"):
            conditional_sources.add(edge["from"])

    simple_edges = [e for e in edges if not e.get("condition") and e["from"] not in conditional_sources]
    for edge in simple_edges:
        to = END if edge["to"] == "END" else edge["to"]
        graph.add_edge(edge["from"], to)

    for source in conditional_sources:
        source_edges = [e for e in edges if e["from"] == source]

        def make_router(src_edges):
            def router(state: WorkflowState) -> str:
                for e in src_edges:
                    cond = e.get("condition", "")
                    target = "end" if e["to"] == "END" else e["to"]
                    if not cond or _evaluate_condition(cond, state):
                        return target
                return "end"
            return router

        route_map = {}
        for e in source_edges:
            target_key = "end" if e["to"] == "END" else e["to"]
            target_val = END if e["to"] == "END" else e["to"]
            route_map[target_key] = target_val

        graph.add_conditional_edges(source, make_router(source_edges), route_map)

    return graph.compile(), max_iterations


def list_workflows(config: dict) -> list[dict]:
    """List available workflows from config."""
    workflows = config.get("workflows", {})
    return [
        {"name": name, "description": wf.get("description", ""), "nodes": len(wf.get("nodes", []))}
        for name, wf in workflows.items()
    ]
