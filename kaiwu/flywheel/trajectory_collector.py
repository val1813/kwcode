"""
Trajectory collector: records task execution trajectories to ~/.kaiwu/trajectories/.
Each trajectory is a single JSON file named {task_id}.json.
"""

import hashlib
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from kaiwu.core.context import TaskContext

logger = logging.getLogger(__name__)

TRAJECTORIES_DIR = os.path.join(Path.home(), ".kaiwu", "trajectories")


@dataclass
class TaskTrajectory:
    task_id: str = ""
    user_input: str = ""
    gate_result: dict = field(default_factory=dict)
    expert_used: str = ""
    pipeline_steps: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    success: bool = False
    retry_count: int = 0
    latency_s: float = 0.0
    model_used: str = ""
    timestamp: str = ""
    search_triggered: bool = False
    project_hash: str = ""


class TrajectoryCollector:
    """Collects and persists task trajectories to ~/.kaiwu/trajectories/."""

    def __init__(self, trajectories_dir: str = TRAJECTORIES_DIR):
        self._dir = trajectories_dir
        os.makedirs(self._dir, exist_ok=True)

    def record(
        self,
        ctx: TaskContext,
        success: bool,
        elapsed: float,
        model: str,
    ) -> TaskTrajectory:
        """Record a completed task. Saves to ~/.kaiwu/trajectories/{task_id}.json."""
        gate = ctx.gate_result or {}
        expert_type = gate.get("expert_type", "unknown")

        # Extract pipeline from gate or use default
        pipeline = gate.get("pipeline", [])
        if not pipeline:
            from kaiwu.core.orchestrator import EXPERT_SEQUENCES
            pipeline = EXPERT_SEQUENCES.get(expert_type, ["generator", "verifier"])

        # Extract modified files from generator output
        files_modified = []
        if ctx.generator_output and "patches" in ctx.generator_output:
            files_modified = [p.get("file", "") for p in ctx.generator_output["patches"] if p.get("file")]

        traj = TaskTrajectory(
            task_id=str(uuid.uuid4()),
            user_input=ctx.user_input,
            gate_result={
                "expert_name": gate.get("expert_name", ""),
                "expert_type": expert_type,
                "task_summary": gate.get("task_summary", ""),
                "difficulty": gate.get("difficulty", ""),
            },
            expert_used=expert_type,
            pipeline_steps=list(pipeline),
            files_modified=files_modified,
            success=success,
            retry_count=ctx.retry_count,
            latency_s=round(elapsed, 2),
            model_used=model,
            timestamp=datetime.now(timezone.utc).isoformat(),
            search_triggered=ctx.search_triggered,
            project_hash=hashlib.sha256(ctx.project_root.encode()).hexdigest()[:16],
        )

        path = os.path.join(self._dir, f"{traj.task_id}.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(asdict(traj), f, ensure_ascii=False, indent=2)
            logger.debug("Trajectory saved: %s", path)
        except OSError as e:
            logger.warning("Failed to save trajectory: %s", e)

        return traj

    def load_recent(self, limit: int = 100) -> list[TaskTrajectory]:
        """Load recent trajectories, sorted by timestamp desc."""
        trajectories = self._load_all()
        trajectories.sort(key=lambda t: t.timestamp, reverse=True)
        return trajectories[:limit]

    def load_by_type(self, expert_type: str) -> list[TaskTrajectory]:
        """Load all trajectories for a given expert_type."""
        return [t for t in self._load_all() if t.expert_used == expert_type]

    def _load_all(self) -> list[TaskTrajectory]:
        """Load all trajectory JSON files from disk."""
        results = []
        if not os.path.isdir(self._dir):
            return results

        for fname in os.listdir(self._dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self._dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                results.append(TaskTrajectory(**data))
            except Exception as e:
                logger.warning("Failed to load trajectory %s: %s", fname, e)
        return results

    def get_by_expert(self, expert_name: str) -> list[TaskTrajectory]:
        """Load trajectories where the matched expert_name matches."""
        return [
            t for t in self._load_all()
            if t.gate_result.get("expert_name") == expert_name
        ]
