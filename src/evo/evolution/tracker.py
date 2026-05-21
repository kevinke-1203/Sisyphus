"""Trajectory tracker - records workflow execution traces for evolution."""

import time
import uuid
import json
import os
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Literal


@dataclass
class TrajectoryStep:
    node_name: str
    input_summary: str
    output_summary: str
    duration_ms: int
    success: bool
    retry_count: int = 0
    tokens_used: int = 0
    cost_usd: float = 0.0
    tool_calls_json: str = "[]"


@dataclass
class Trajectory:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    workflow_name: str = ""
    task_description: str = ""
    started_at: str = ""
    completed_at: str = ""
    outcome: Literal["success", "failure", "partial"] = "partial"
    steps: list[TrajectoryStep] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class TrajectoryTracker:
    """Records execution trajectories for a single workflow run."""

    def __init__(self, workflow_name: str, task: str):
        self._trajectory = Trajectory(
            workflow_name=workflow_name,
            task_description=task,
            started_at=datetime.now().isoformat(),
        )
        self._step_start: float = 0

    @property
    def trajectory(self) -> Trajectory:
        return self._trajectory

    def begin_step(self):
        """Mark the start of a step (call before agent executes)."""
        self._step_start = time.time()

    def end_step(self, node_name: str, input_summary: str, output_summary: str,
                 success: bool = True, tokens_used: int = 0,
                 cost_usd: float = 0.0, tool_calls: list[dict] | None = None):
        """Record a completed step."""
        duration_ms = int((time.time() - self._step_start) * 1000) if self._step_start else 0
        step = TrajectoryStep(
            node_name=node_name,
            input_summary=input_summary[:200],
            output_summary=output_summary[:200],
            duration_ms=duration_ms,
            success=success,
            tokens_used=tokens_used,
            cost_usd=cost_usd,
            tool_calls_json=json.dumps(tool_calls or []),
        )
        self._trajectory.steps.append(step)

    def complete(self, outcome: Literal["success", "failure", "partial"]):
        """Mark the trajectory as complete."""
        self._trajectory.completed_at = datetime.now().isoformat()
        self._trajectory.outcome = outcome


class TrajectoryStore:
    """Persists trajectories to SQLite."""

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            from evo.config import get_data_dir
            data_dir = get_data_dir()
        else:
            data_dir = os.path.dirname(db_path)
        self._db_path = db_path or f"{data_dir}/trajectories.db"
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trajectories (
                    id TEXT PRIMARY KEY,
                    workflow_name TEXT,
                    task_description TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    outcome TEXT,
                    steps_json TEXT,
                    metadata_json TEXT
                )
            """)

    def save(self, trajectory: Trajectory):
        """Save a trajectory to the database."""
        steps_json = json.dumps([asdict(s) for s in trajectory.steps])
        metadata_json = json.dumps(trajectory.metadata)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO trajectories
                   (id, workflow_name, task_description, started_at, completed_at, outcome, steps_json, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (trajectory.id, trajectory.workflow_name, trajectory.task_description,
                 trajectory.started_at, trajectory.completed_at, trajectory.outcome,
                 steps_json, metadata_json),
            )

    def list_all(self, limit: int = 50) -> list[dict]:
        """List all trajectories (summary view)."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, workflow_name, task_description, outcome, started_at FROM trajectories ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get(self, trajectory_id: str) -> Trajectory | None:
        """Get a full trajectory by ID."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM trajectories WHERE id = ?", (trajectory_id,)).fetchone()
        if not row:
            return None
        steps = [TrajectoryStep(**s) for s in json.loads(row["steps_json"])]
        return Trajectory(
            id=row["id"],
            workflow_name=row["workflow_name"],
            task_description=row["task_description"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            outcome=row["outcome"],
            steps=steps,
            metadata=json.loads(row["metadata_json"]),
        )

    def get_by_workflow(self, workflow_name: str, limit: int = 20) -> list[dict]:
        """Get trajectories for a specific workflow."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, task_description, outcome, started_at FROM trajectories WHERE workflow_name = ? ORDER BY started_at DESC LIMIT ?",
                (workflow_name, limit),
            ).fetchall()
        return [dict(r) for r in rows]
