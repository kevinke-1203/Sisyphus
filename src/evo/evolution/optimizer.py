"""Auto prompt optimizer - analyzes failed trajectories and improves agent prompts."""

import asyncio
import os
import json
import sqlite3
from datetime import datetime
from dataclasses import dataclass

from evo.agents.base import AgentConfig, BaseAgent
from evo.evolution.tracker import TrajectoryStore, Trajectory


@dataclass
class PromptPatch:
    agent_name: str
    original_prompt: str
    patch: str
    reason: str
    created_at: str
    based_on_trajectories: list[str]


class PromptOptimizer:
    """Analyzes failed trajectories and suggests prompt improvements."""

    def __init__(self, min_failures: int = 3):
        self.min_failures = min_failures
        self._store = TrajectoryStore()
        from evo.config import _default_base_dir
        self._patches_db = os.path.join(os.getenv("EVO_DATA_DIR", _default_base_dir()), "prompt_patches.db")
        self._init_db()

    def _init_db(self):
        os.makedirs(os.path.dirname(self._patches_db), exist_ok=True)
        with sqlite3.connect(self._patches_db) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS prompt_patches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_name TEXT,
                    patch TEXT,
                    reason TEXT,
                    created_at TEXT,
                    trajectory_ids TEXT,
                    applied INTEGER DEFAULT 0
                )
            """)

    def analyze_failures(self, agent_name: str) -> list[Trajectory]:
        """Get failed trajectories where a specific agent's step failed.
        Matches by node_name (graph node) or agent name."""
        all_trajs = self._store.list_all(limit=100)
        failures = []
        match_names = {agent_name, agent_name.rstrip("r"), agent_name + "r"}
        for row in all_trajs:
            if row["outcome"] != "failure":
                continue
            traj = self._store.get(row["id"])
            if traj and any(s.node_name in match_names and not s.success for s in traj.steps):
                failures.append(traj)
        return failures

    async def generate_improvement(self, agent_name: str, current_prompt: str, failures: list[Trajectory]) -> PromptPatch | None:
        """Use Claude Code CLI to analyze failures and suggest a prompt improvement."""
        if len(failures) < self.min_failures:
            return None

        failure_summaries = []
        for t in failures[:10]:
            failed_steps = [s for s in t.steps if s.node_name == agent_name and not s.success]
            for s in failed_steps:
                failure_summaries.append(
                    f"Task: {t.task_description}\n"
                    f"Input: {s.input_summary}\n"
                    f"Output: {s.output_summary}"
                )

        failures_text = "\n---\n".join(failure_summaries[:5])

        analysis_prompt = (
            f"You are a prompt engineering expert. Analyze these failures from an AI agent "
            f"named '{agent_name}' and suggest ONE concise addition to its system prompt "
            f"that would prevent these failures.\n\n"
            f"Current system prompt:\n{current_prompt}\n\n"
            f"Failure examples:\n{failures_text}\n\n"
            f"Respond with EXACTLY this format:\n"
            f"PATCH: <one sentence to append to the system prompt>\n"
            f"REASON: <why this would help>"
        )

        analysis_agent = DynamicOptimizerAgent(
            AgentConfig(
                name="prompt_optimizer",
                role="Prompt optimizer",
                system_prompt="You are a prompt engineering expert.",
                allowed_tools=[],
                permission_mode="bypassPermissions",
            )
        )
        result = (await analysis_agent.invoke_agent(analysis_prompt)).text

        patch_line = ""
        reason_line = ""
        for line in result.split("\n"):
            if line.startswith("PATCH:"):
                patch_line = line[6:].strip()
            elif line.startswith("REASON:"):
                reason_line = line[7:].strip()

        if not patch_line:
            return None

        return PromptPatch(
            agent_name=agent_name,
            original_prompt=current_prompt,
            patch=patch_line,
            reason=reason_line,
            created_at=datetime.now().isoformat(),
            based_on_trajectories=[t.id for t in failures[:5]],
        )
    def save_patch(self, patch: PromptPatch):
        """Save a prompt patch to the database."""
        with sqlite3.connect(self._patches_db) as conn:
            conn.execute(
                "INSERT INTO prompt_patches (agent_name, patch, reason, created_at, trajectory_ids) VALUES (?, ?, ?, ?, ?)",
                (patch.agent_name, patch.patch, patch.reason, patch.created_at,
                 json.dumps(patch.based_on_trajectories)),
            )

    def get_patches(self, agent_name: str, only_unapplied: bool = True) -> list[dict]:
        """Get saved patches for an agent."""
        with sqlite3.connect(self._patches_db) as conn:
            conn.row_factory = sqlite3.Row
            query = "SELECT * FROM prompt_patches WHERE agent_name = ?"
            if only_unapplied:
                query += " AND applied = 0"
            query += " ORDER BY created_at DESC"
            rows = conn.execute(query, (agent_name,)).fetchall()
        return [dict(r) for r in rows]

    def apply_patch(self, patch_id: int):
        """Mark a patch as applied."""
        with sqlite3.connect(self._patches_db) as conn:
            conn.execute("UPDATE prompt_patches SET applied = 1 WHERE id = ?", (patch_id,))

    def get_enhanced_prompt(self, agent_name: str, base_prompt: str) -> str:
        """Get the base prompt enhanced with all applied patches."""
        with sqlite3.connect(self._patches_db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT patch FROM prompt_patches WHERE agent_name = ? AND applied = 1 ORDER BY created_at",
                (agent_name,),
            ).fetchall()
        if not rows:
            return base_prompt
        patches = [r["patch"] for r in rows]
        return base_prompt + "\n\nLearned rules:\n" + "\n".join(f"- {p}" for p in patches)


class DynamicOptimizerAgent(BaseAgent):
    """Small adapter so the optimizer can reuse BaseAgent's Claude CLI transport."""

    async def execute(self, state: dict) -> dict:
        return {}


def optimize_agent(agent_name: str, current_prompt: str, min_failures: int = 3) -> PromptPatch | None:
    """Convenience function: analyze failures and generate improvement for an agent."""
    optimizer = PromptOptimizer(min_failures=min_failures)
    failures = optimizer.analyze_failures(agent_name)
    if len(failures) < min_failures:
        return None
    patch = asyncio.run(optimizer.generate_improvement(agent_name, current_prompt, failures))
    if patch:
        optimizer.save_patch(patch)
    return patch
