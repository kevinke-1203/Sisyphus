"""Workflow validator - checks for common configuration errors."""

_BUILTIN_NODES = {"setup", "confirm", "archive"}


def validate_workflow(workflow_config: dict, available_agents: list[str]) -> list[str]:
    """Validate a workflow config and return a list of errors (empty = valid)."""
    errors = []
    nodes = workflow_config.get("nodes", [])
    edges = workflow_config.get("edges", [])

    if not nodes:
        errors.append("Workflow has no nodes defined")
        return errors

    node_names = {n["name"] for n in nodes}

    for node in nodes:
        if node["name"] in _BUILTIN_NODES:
            continue
        agent = node.get("agent", node["name"])
        if agent not in available_agents:
            errors.append(f"Node '{node['name']}' references unknown agent '{agent}'")

    for edge in edges:
        if edge["from"] not in node_names:
            errors.append(f"Edge from unknown node '{edge['from']}'")
        if edge["to"] != "END" and edge["to"] not in node_names:
            errors.append(f"Edge to unknown node '{edge['to']}'")

    has_end = any(e["to"] == "END" for e in edges)
    if not has_end:
        errors.append("Workflow has no path to END (potential infinite loop)")

    reachable = {nodes[0]["name"]}
    changed = True
    while changed:
        changed = False
        for edge in edges:
            if edge["from"] in reachable and edge["to"] != "END" and edge["to"] not in reachable:
                reachable.add(edge["to"])
                changed = True
    unreachable = node_names - reachable
    if unreachable:
        errors.append(f"Unreachable nodes: {', '.join(unreachable)}")

    return errors
