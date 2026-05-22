"""Evo CLI entry point."""

import asyncio
import json
import sys

from evo.config import load_project_env


def main():
    load_project_env()

    if len(sys.argv) < 2:
        print("Usage:")
        print("  evo run --task <description> [--repo <path>] [--branch <name>]")
        print("                              [--workflow <name>] [--config <workflow.yaml>] [--yes]")
        print("  evo tasks [--all]           # list tasks")
        print("  evo archive <task_id>       # archive task: merge + cleanup")
        print("  evo list [--config <path>]  # list available workflows")
        print("  evo validate [workflow] [--config <path>]")
        print("  evo trajectories list|show")
        print("  evo optimize status|run|list|apply")
        print("  evo projects")
        print("  evo web [--port 8080]       # start web dashboard")
        sys.exit(1)

    command = sys.argv[1]

    # Handle --project flag globally
    args_all = _parse_args(sys.argv[2:])
    if "project" in args_all:
        from evo.config import set_project
        set_project(args_all["project"])

    if command == "run":
        _handle_run()
    elif command == "tasks":
        _handle_tasks()
    elif command == "archive":
        _handle_archive()
    elif command == "list":
        _handle_list()
    elif command == "validate":
        _handle_validate()
    elif command == "trajectories":
        _handle_trajectories()
    elif command == "optimize":
        _handle_optimize()
    elif command == "projects":
        from evo.config import list_projects, get_project
        projects = list_projects()
        current = get_project()
        for p in projects:
            marker = " (active)" if p == current else ""
            print(f"  {p}{marker}")
    elif command == "web":
        _handle_web()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


def _parse_args(args: list[str]) -> dict[str, str]:
    """Simple --key value parser."""
    result = {}
    i = 0
    positional = []
    while i < len(args):
        if args[i].startswith("--"):
            key = args[i][2:]
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                result[key] = args[i + 1]
                i += 2
            else:
                result[key] = "true"
                i += 1
        else:
            positional.append(args[i])
            i += 1
    if positional:
        result["_positional"] = " ".join(positional)
    return result


def _get_config_arg(args: dict[str, str]) -> str:
    """Return workflow config path from supported CLI flags."""
    return args.get("config", args.get("workflow-config", ""))


def _handle_run():
    args = _parse_args(sys.argv[2:])
    task = args.get("task", args.get("_positional", "")).strip()
    repo = args.get("repo", "")
    branch = args.get("branch", "main")
    workflow_name = args.get("workflow", "")
    config_path = _get_config_arg(args)
    debug = "debug" in args
    skip_confirm = "yes" in args or "skip_confirm" in args

    if not task:
        print("Error: --task is required")
        sys.exit(1)

    if debug:
        import os
        os.environ["EVO_DEBUG"] = "1"

    if workflow_name:
        asyncio.run(_run_yaml_workflow(task, workflow_name, repo=repo, branch=branch,
                                       skip_confirm=skip_confirm, config_path=config_path))
    else:
        from evo.orchestrator.engine import run_workflow
        asyncio.run(run_workflow(task, repo_path=repo, branch=branch, skip_confirm=skip_confirm))


async def _run_yaml_workflow(task: str, workflow_name: str, repo: str = "",
                             branch: str = "main", skip_confirm: bool = False,
                             config_path: str = ""):
    from evo.orchestrator.parser import load_config, register_agents_from_config, build_graph_from_config
    from evo.orchestrator.state import WorkflowState

    config = load_config(config_path or None)
    register_agents_from_config(config)

    workflows = config.get("workflows", {})
    if workflow_name not in workflows:
        print(f"Error: workflow '{workflow_name}' not found. Available: {', '.join(workflows.keys())}")
        sys.exit(1)

    wf_config = workflows[workflow_name]
    # CLI --yes overrides config skip_confirm
    if skip_confirm:
        wf_config = {**wf_config, "skip_confirm": True}
    graph, max_iterations = build_graph_from_config(wf_config)

    print(f"\n{'='*50}")
    print(f"[Evo] Running workflow: {workflow_name}")
    print(f"[Evo] Task: {task}")
    if repo:
        print(f"[Evo] Repo: {repo} (branch: {branch})")
    print(f"[Evo] Max iterations: {max_iterations}")
    print(f"{'='*50}\n")

    initial_state: WorkflowState = {
        "task": task,
        "plan": "",
        "code": "",
        "test_result": "",
        "test_passed": False,
        "api_test_result": "",
        "api_test_passed": False,
        "ui_test_result": "",
        "ui_test_passed": False,
        "current_step": "",
        "iteration": 0,
        "max_iterations": max_iterations,
        "error": "",
        "_sdk_meta": {},
        "_retries": {},
        "requirements_doc": "",
        "design_doc": "",
        "existing_test_case_inventory": "",
        "behavior_specs": "",
        "test_case_changes": "",
        "verification_plan": "",
        "test_cases": "",
        "user_feedback": "",
        "task_id": "",
        "task_name": "",
        "task_dir": "",
        "dir_name": "",
        "worktree_path": "",
        "repo_path": repo,
        "repo_branch": branch,
    }

    final_state = await graph.ainvoke(initial_state)

    print(f"\n{'='*50}")
    print(f"[Evo] Workflow '{workflow_name}' complete")
    print(f"{'='*50}\n")

    for key, value in final_state.items():
        if key.startswith("_") or not value:
            continue
        val_str = str(value)
        if len(val_str) > 200:
            val_str = val_str[:200] + "..."
        print(f"  {key}: {val_str}")


def _handle_tasks():
    from evo.task import TaskManager
    args = _parse_args(sys.argv[2:])
    show_all = "all" in args

    mgr = TaskManager()
    tasks = mgr.list_tasks(include_archived=show_all)

    if not tasks:
        print("No tasks found.")
        return

    print(f"{'ID':<10} {'Name':<20} {'Branch':<12}")
    print("-" * 46)
    for t in tasks:
        print(f"{t.get('task_id', ''):<10} {t.get('task_name', ''):<20} "
              f"{t.get('repo_branch', ''):<12}")


def _handle_archive():
    if len(sys.argv) < 3:
        print("Usage: evo archive <task_id_or_name>")
        sys.exit(1)

    target = sys.argv[2]
    from evo.task import TaskManager

    mgr = TaskManager()

    # Find the task to get metadata for the archive node
    try:
        task_dir = mgr._find_task_dir(target)
    except FileNotFoundError:
        print(f"Task not found: {target}")
        sys.exit(1)

    meta = mgr._load_meta(task_dir)
    if not meta:
        print(f"No metadata found for task: {target}")
        sys.exit(1)

    print(f"Archiving task: {meta.get('dir_name', target)}")
    task_branch = meta.get("task_branch") or f"evo-task/{meta.get('dir_name', '')}"
    print(f"  Branch to merge: {task_branch} → {meta.get('repo_branch', 'main')}")

    try:
        archived_task_dir = mgr.archive_task(meta.get("task_id", target))
    except Exception as e:
        print(f"Archive failed: {e}")
        sys.exit(1)
    print(f"Archived to: {archived_task_dir}")


def _handle_list():
    from evo.orchestrator.parser import load_config, list_workflows
    args = _parse_args(sys.argv[2:])
    config_path = _get_config_arg(args)
    try:
        config = load_config(config_path or None)
    except FileNotFoundError:
        print("No workflow config found.")
        return
    workflows = list_workflows(config)
    if not workflows:
        print("No workflows defined.")
        return
    print(f"{'Name':<20} {'Nodes':<6} {'Description'}")
    print("-" * 60)
    for wf in workflows:
        print(f"{wf['name']:<20} {wf['nodes']:<6} {wf['description']}")


def _handle_validate():
    from evo.orchestrator.parser import load_config, register_agents_from_config
    from evo.orchestrator.validator import validate_workflow
    from evo.agents.registry import list_agents

    args = _parse_args(sys.argv[2:])
    config_path = _get_config_arg(args)
    try:
        config = load_config(config_path or None)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)

    register_agents_from_config(config)
    available = list_agents()

    target = args.get("_positional") or None
    workflows = config.get("workflows", {})

    if target:
        if target not in workflows:
            print(f"Workflow '{target}' not found.")
            sys.exit(1)
        workflows = {target: workflows[target]}

    all_valid = True
    for name, wf_config in workflows.items():
        errors = validate_workflow(wf_config, available)
        if errors:
            print(f"[FAIL] {name}:")
            for e in errors:
                print(f"  - {e}")
            all_valid = False
        else:
            print(f"[OK] {name}")

    if all_valid:
        print("\nAll workflows valid.")
    else:
        sys.exit(1)


def _handle_trajectories():
    from evo.evolution.tracker import TrajectoryStore
    store = TrajectoryStore()
    sub = sys.argv[2] if len(sys.argv) > 2 else "list"

    if sub == "list":
        rows = store.list_all()
        if not rows:
            print("No trajectories recorded yet.")
            return
        print(f"{'ID':<10} {'Workflow':<16} {'Task':<30} {'Outcome'}")
        print("-" * 70)
        for r in rows:
            task_short = r["task_description"][:28]
            print(f"{r['id']:<10} {r['workflow_name']:<16} {task_short:<30} {r['outcome']}")

    elif sub == "show":
        if len(sys.argv) < 4:
            print("Usage: evo trajectories show <id>")
            sys.exit(1)
        tid = sys.argv[3]
        t = store.get(tid)
        if not t:
            print(f"Trajectory {tid} not found.")
            sys.exit(1)
        print(f"Trajectory: {t.id}")
        print(f"Workflow:   {t.workflow_name}")
        print(f"Task:       {t.task_description}")
        print(f"Outcome:    {t.outcome}")
        print(f"Started:    {t.started_at}")
        print(f"Completed:  {t.completed_at}")
        print(f"\nSteps ({len(t.steps)}):")
        for i, s in enumerate(t.steps, 1):
            status = "OK" if s.success else "FAIL"
            tokens = f" {s.tokens_used}tok" if s.tokens_used else ""
            tools = f" {len(json.loads(s.tool_calls_json))}calls" if s.tool_calls_json != "[]" else ""
            print(f"  {i}. [{s.node_name}] {s.duration_ms}ms {status}{tokens}{tools}")
            print(f"     In:  {s.input_summary[:60]}")
            print(f"     Out: {s.output_summary[:60]}")
    else:
        print(f"Unknown subcommand: trajectories {sub}")
        sys.exit(1)


def _handle_optimize():
    from evo.evolution.optimizer import PromptOptimizer
    from evo.agents.registry import register_builtin_agents, get_agent

    register_builtin_agents()
    optimizer = PromptOptimizer(min_failures=1)

    sub = sys.argv[2] if len(sys.argv) > 2 else "status"

    if sub == "run":
        agent_name = sys.argv[3] if len(sys.argv) > 3 else "coder"
        agent = get_agent(agent_name)
        if not agent:
            print(f"Agent '{agent_name}' not found.")
            sys.exit(1)

        print(f"Analyzing failures for '{agent_name}'...")
        failures = optimizer.analyze_failures(agent_name)
        print(f"Found {len(failures)} failed trajectories.")

        if not failures:
            print("No failures to analyze. Agent is performing well!")
            return

        print("Generating improvement suggestion...")
        patch = asyncio.run(optimizer.generate_improvement(agent_name, agent.config.system_prompt, failures))
        if patch:
            optimizer.save_patch(patch)
            print(f"\nSuggested patch:")
            print(f"  PATCH:  {patch.patch}")
            print(f"  REASON: {patch.reason}")
            print(f"\nSaved. Use 'evo optimize apply <id>' to activate.")
        else:
            print("Could not generate a useful improvement.")

    elif sub == "status":
        for agent_name in ["planner", "coder", "api_tester", "ui_tester", "tester"]:
            patches = optimizer.get_patches(agent_name, only_unapplied=False)
            applied = sum(1 for p in patches if p["applied"])
            pending = len(patches) - applied
            if patches:
                print(f"  {agent_name}: {applied} applied, {pending} pending")
            else:
                print(f"  {agent_name}: no patches")

    elif sub == "apply":
        if len(sys.argv) < 4:
            print("Usage: evo optimize apply <patch_id>")
            sys.exit(1)
        patch_id = int(sys.argv[3])
        optimizer.apply_patch(patch_id)
        print(f"Patch {patch_id} applied. It will take effect on next run.")

    elif sub == "list":
        agent_name = sys.argv[3] if len(sys.argv) > 3 else None
        agents = [agent_name] if agent_name else ["planner", "coder", "api_tester", "ui_tester", "tester"]
        for name in agents:
            patches = optimizer.get_patches(name, only_unapplied=False)
            if patches:
                print(f"\n{name}:")
                for p in patches:
                    status = "APPLIED" if p["applied"] else "PENDING"
                    print(f"  [{p['id']}] [{status}] {p['patch']}")
                    print(f"       Reason: {p['reason']}")

    else:
        print("Usage:")
        print("  evo optimize status          # show patch status per agent")
        print("  evo optimize run [agent]     # analyze failures and suggest improvement")
        print("  evo optimize list [agent]    # list all patches")
        print("  evo optimize apply <id>      # activate a patch")
        sys.exit(1)


def _handle_web():
    args = _parse_args(sys.argv[2:])
    port = int(args.get("port", "8080"))
    host = args.get("host", "0.0.0.0")
    config_path = _get_config_arg(args)
    if config_path:
        import os
        os.environ["EVO_WORKFLOW_CONFIG"] = config_path

    import uvicorn
    from evo.web.app import app

    print(f"[Evo] Starting web dashboard at http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, access_log=False)


if __name__ == "__main__":
    main()
