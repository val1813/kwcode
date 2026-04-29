"""
Expert Registry: in-memory + disk. Loads from builtin_experts/ and ~/.kaiwu/experts/.
Keyword matching is pure string, no LLM call — millisecond-level.
"""

import logging
import os
from pathlib import Path
from typing import Optional

from kaiwu.registry.expert_loader import ExpertLoader

logger = logging.getLogger(__name__)

# Lifecycle → confidence penalty (higher = harder to trigger)
_LIFECYCLE_PENALTY = {
    "new": 0.1,
    "mature": 0.0,
    "declining": 0.2,
    "archived": None,  # skip entirely
}


class ExpertRegistry:
    """Expert registry: in-memory + disk. Loads from builtin_experts/ and ~/.kaiwu/experts/."""

    def __init__(self):
        self.experts: dict[str, dict] = {}  # name -> expert definition

    def load_builtin(self):
        """Load all YAML files from kaiwu/builtin_experts/."""
        builtin_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "builtin_experts")
        for expert in ExpertLoader.load_directory(builtin_dir):
            self.register(expert)
        logger.info("Loaded %d builtin experts", len(self.experts))

    def load_user(self, user_dir: str = None):
        """Load from ~/.kaiwu/experts/."""
        if user_dir is None:
            user_dir = os.path.join(Path.home(), ".kaiwu", "experts")
        for expert in ExpertLoader.load_directory(user_dir):
            self.register(expert)

    def match(self, user_input: str) -> Optional[dict]:
        """
        Match user input against registered experts.
        Returns {"name": str, "confidence": float, "expert": dict} or None.

        Confidence uses saturating formula: 1 - 0.5^matched_count.
        This means: 1 match=0.50, 2 matches=0.75, 3 matches=0.875, 4+=0.93+.
        Realistic for short user inputs that won't contain all keywords.

        Multiple matches -> pick highest confidence.
        Same confidence -> pick higher success_rate.
        Below trigger_min_confidence (adjusted by lifecycle) -> skip.

        Lifecycle filtering:
        - new: confidence threshold +0.1 (observation period)
        - mature: normal threshold
        - declining: confidence threshold +0.2
        - archived: skip entirely
        """
        input_lower = user_input.lower()
        best = None

        for name, expert in self.experts.items():
            lifecycle = expert.get("lifecycle", "new")
            penalty = _LIFECYCLE_PENALTY.get(lifecycle, 0.0)
            if penalty is None:  # archived
                continue

            keywords = expert["trigger_keywords"]
            matched = sum(1 for kw in keywords if kw.lower() in input_lower)
            if matched == 0:
                continue

            # Saturating confidence: 1 - 0.5^matched
            confidence = 1.0 - (0.5 ** matched)
            threshold = min(1.0, expert["trigger_min_confidence"] + penalty)

            if confidence < threshold:
                continue

            perf = expert.get("performance", {})
            success_rate = perf.get("success_rate", 0.0)

            if best is None:
                best = {"name": name, "confidence": confidence, "expert": expert, "_sr": success_rate}
            elif confidence > best["confidence"]:
                best = {"name": name, "confidence": confidence, "expert": expert, "_sr": success_rate}
            elif confidence == best["confidence"] and success_rate > best["_sr"]:
                best = {"name": name, "confidence": confidence, "expert": expert, "_sr": success_rate}

        if best is None:
            return None

        # Clean internal field
        best.pop("_sr", None)
        return best

    def register(self, expert_def: dict):
        """Register a new expert."""
        name = expert_def["name"]
        if name in self.experts:
            logger.debug("Overwriting expert: %s", name)
        self.experts[name] = expert_def

    def update_stats(self, expert_name: str, success: bool, latency: float):
        """Update performance stats after task completion."""
        expert = self.experts.get(expert_name)
        if not expert:
            return

        perf = expert.setdefault("performance", {"success_rate": 0.0, "avg_latency_s": 0, "task_count": 0})
        count = perf["task_count"]
        # Incremental average
        perf["success_rate"] = (perf["success_rate"] * count + (1.0 if success else 0.0)) / (count + 1)
        perf["avg_latency_s"] = (perf["avg_latency_s"] * count + latency) / (count + 1)
        perf["task_count"] = count + 1

    def list_experts(self, expert_type: str = None) -> list[dict]:
        """List all registered experts, optionally filtered by type."""
        experts = list(self.experts.values())
        if expert_type:
            experts = [e for e in experts if e.get("type") == expert_type]
        return experts

    def get(self, name: str) -> Optional[dict]:
        """Get expert by name."""
        return self.experts.get(name)

    def get_instructions(self, name: str) -> str:
        """
        Get Level 2 instructions for an expert (progressive disclosure).
        For SKILL.md experts: returns the markdown body.
        For YAML experts: returns system_prompt (backward compat).
        """
        expert = self.experts.get(name)
        if not expert:
            return ""
        # SKILL.md format has 'instructions' field
        if expert.get("_format") == "skill":
            return expert.get("instructions", "")
        # YAML format fallback
        return expert.get("system_prompt", "")

    def get_scripts(self, name: str) -> list[dict]:
        """
        Get Level 3 scripts for an expert (progressive disclosure).
        Returns list of {"name": str, "path": str} or empty list.
        """
        expert = self.experts.get(name)
        if not expert:
            return []
        return expert.get("scripts", [])

    def save_to_disk(self, expert_name: str, target_dir: str = None):
        """Save expert YAML to disk."""
        import yaml

        expert = self.experts.get(expert_name)
        if not expert:
            raise ValueError(f"Expert not found: {expert_name}")

        if target_dir is None:
            target_dir = os.path.join(Path.home(), ".kaiwu", "experts")
        os.makedirs(target_dir, exist_ok=True)

        # Strip internal fields
        data = {k: v for k, v in expert.items() if not k.startswith("_")}
        fname = expert_name.lower().replace(" ", "_") + ".yaml"
        path = os.path.join(target_dir, fname)

        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        logger.info("Saved expert %s to %s", expert_name, path)
        return path
