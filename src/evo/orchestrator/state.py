"""Workflow state definition for LangGraph."""

from typing import TypedDict


class WorkflowState(TypedDict):
    task: str
    plan: str
    code: str
    test_result: str
    test_passed: bool
    api_test_result: str
    api_test_passed: bool
    ui_test_result: str
    ui_test_passed: bool
    current_step: str
    iteration: int
    max_iterations: int
    error: str
    _sdk_meta: dict
    # Per-node retry counters (auto-incremented by engine)
    _retries: dict[str, int]
    # Structured planner outputs
    requirements_doc: str
    design_doc: str
    existing_test_case_inventory: str
    behavior_specs: str
    test_case_changes: str
    verification_plan: str
    test_cases: str
    # User feedback on plan (set by confirm node)
    user_feedback: str
    # Task management fields (set by setup node)
    task_id: str
    task_name: str
    task_dir: str
    dir_name: str
    worktree_path: str
    repo_path: str
    repo_branch: str
