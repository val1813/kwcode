"""
AB tester: three-gate expert validation system (spec §5.1).

Gate 1: Quantity check (handled by PatternDetector — >=5 successful same-type tasks)
Gate 2: Backtest — replay source trajectories' tasks through new expert pipeline,
        new expert success_rate must >= baseline (source trajectories' rate).
Gate 3: Production AB test (10 real tasks: 5 new vs 5 baseline, new must beat by >10%)
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from kaiwu.flywheel.trajectory_collector import TrajectoryCollector, TaskTrajectory
from kaiwu.registry.expert_registry import ExpertRegistry
from kaiwu.registry.expert_loader import ExpertLoader

if TYPE_CHECKING:
    from kaiwu.core.orchestrator import PipelineOrchestrator

logger = logging.getLogger(__name__)

CANDIDATES_DIR = os.path.join(Path.home(), ".kaiwu", "candidates")


class ABTester:
    """Three-gate expert validation system."""

    def __init__(
        self,
        registry: ExpertRegistry,
        collector: TrajectoryCollector,
        orchestrator: "PipelineOrchestrator | None" = None,
    ):
        self.registry = registry
        self.collector = collector
        self.orchestrator = orchestrator  # needed for gate 2 backtest
        self._candidates: dict[str, dict] = {}  # expert_name -> candidate info
        self._load_candidates()

    def submit_candidate(self, expert_def: dict, source_trajectories: list[TaskTrajectory]):
        """
        Submit a generated expert for gate 2 (backtest).
        Gate 1 (quantity) was already passed by PatternDetector.

        Gate 2: Real backtest validation
        - Validate YAML structure
        - Replay each source trajectory's task through the new expert's pipeline
        - Compare: new expert success_rate must >= baseline success_rate
        - Only then enters candidate pool for gate 3
        """
        name = expert_def["name"]

        # Gate 2a: Validate expert definition structure
        valid, err = ExpertLoader.validate(expert_def)
        if not valid:
            logger.warning("Gate 2 failed for %s: validation error: %s", name, err)
            return

        # Gate 2b: Compute baseline stats from source trajectories
        baseline_successes = sum(1 for t in source_trajectories if t.success)
        baseline_total = len(source_trajectories)
        baseline_sr = baseline_successes / max(baseline_total, 1)
        baseline_latency = (
            sum(t.latency_s for t in source_trajectories) / baseline_total
            if baseline_total > 0 else 0.0
        )

        # Gate 2c: Real backtest — replay source tasks through new expert pipeline
        backtest_results = self._run_backtest(expert_def, source_trajectories)
        backtest_successes = sum(1 for r in backtest_results if r["success"])
        backtest_total = len(backtest_results)
        backtest_sr = backtest_successes / max(backtest_total, 1)

        logger.info(
            "Gate 2 backtest for %s: new_sr=%.0f%% (%d/%d) vs baseline_sr=%.0f%% (%d/%d)",
            name, backtest_sr * 100, backtest_successes, backtest_total,
            baseline_sr * 100, baseline_successes, baseline_total,
        )

        # Gate 2 pass condition: new expert >= baseline
        if backtest_sr < baseline_sr:
            logger.warning(
                "Gate 2 FAILED for %s: backtest %.0f%% < baseline %.0f%%",
                name, backtest_sr * 100, baseline_sr * 100,
            )
            # Save as failed candidate for diagnostics
            self._candidates[name] = {
                "expert_def": expert_def,
                "gate2_passed": False,
                "gate2_backtest": backtest_results,
                "backtest_success_rate": round(backtest_sr, 4),
                "baseline_success_rate": round(baseline_sr, 4),
                "baseline_avg_latency": round(baseline_latency, 2),
                "ab_results": [],
                "status": "gate2_failed",
            }
            self._save_candidates()
            return

        # Gate 2 passed — enter AB testing pool for gate 3
        candidate = {
            "expert_def": expert_def,
            "gate2_passed": True,
            "gate2_backtest": backtest_results,
            "backtest_success_rate": round(backtest_sr, 4),
            "baseline_success_rate": round(baseline_sr, 4),
            "baseline_avg_latency": round(baseline_latency, 2),
            "ab_results": [],  # gate 3 results filled by real tasks
            "status": "ab_testing",
        }
        self._candidates[name] = candidate
        self._save_candidates()

        logger.info("Gate 2 PASSED for %s (backtest %.0f%% >= baseline %.0f%%). Entering AB test pool.",
                     name, backtest_sr * 100, baseline_sr * 100)

    def _run_backtest(self, expert_def: dict, source_trajectories: list[TaskTrajectory]) -> list[dict]:
        """
        Replay source trajectories' tasks through the new expert's pipeline.
        Returns list of {"task": str, "success": bool, "latency": float, "error": str|None}.

        If orchestrator is not available (e.g. unit test), returns empty list
        which causes gate 2 to fail (backtest_sr=0 < baseline_sr>0).
        """
        if not self.orchestrator:
            logger.warning("Gate 2 backtest skipped: no orchestrator available. Gate 2 will fail.")
            return []

        results = []
        for traj in source_trajectories:
            # Build a gate_result that forces the new expert's pipeline
            gate_result = {
                "expert_type": expert_def.get("type", traj.expert_used),
                "expert_name": expert_def["name"],
                "task_summary": traj.gate_result.get("task_summary", ""),
                "difficulty": traj.gate_result.get("difficulty", "easy"),
                "route_type": "expert_registry",
                "pipeline": expert_def.get("pipeline", traj.pipeline_steps),
                "system_prompt": expert_def.get("system_prompt", ""),
            }

            # Use the original project (from trajectory's project_hash we can't recover
            # the path, so we use the orchestrator's current project or a temp dir)
            project_root = getattr(self.orchestrator, '_backtest_project_root', None)
            if not project_root:
                # Fallback: use a temp dir (backtest won't have real files,
                # but verifier can still check syntax)
                project_root = tempfile.mkdtemp(prefix="kwcode_backtest_")

            try:
                result = self.orchestrator.run(
                    user_input=traj.user_input,
                    gate_result=gate_result,
                    project_root=project_root,
                    on_status=None,  # silent
                    no_search=True,  # backtest doesn't need search
                )
                results.append({
                    "task": traj.user_input[:200],
                    "success": result["success"],
                    "latency": round(result.get("elapsed", 0), 2),
                    "error": result.get("error"),
                })
            except Exception as e:
                logger.warning("Backtest task failed with exception: %s", e)
                results.append({
                    "task": traj.user_input[:200],
                    "success": False,
                    "latency": 0.0,
                    "error": str(e),
                })

        return results

    def get_candidate_status(self, expert_name: str) -> dict | None:
        """Get current status of a candidate expert."""
        return self._candidates.get(expert_name)

    def should_use_candidate(self, expert_type: str) -> dict | None:
        """
        Check if there's a candidate in AB testing for this expert_type.
        Returns the candidate expert_def if the next task should use it, None otherwise.
        Alternates: odd-numbered tasks use candidate, even use baseline.
        """
        for name, info in self._candidates.items():
            if info["status"] != "ab_testing":
                continue
            if info["expert_def"].get("type") != expert_type:
                continue

            total = len(info["ab_results"])
            if total >= 10:
                continue  # Already has enough data, pending graduation check

            # Alternate: use candidate on odd tasks (0-indexed: 0,2,4 = baseline; 1,3,5 = candidate)
            use_new = total % 2 == 1
            if use_new:
                return info["expert_def"]

        return None

    def record_ab_result(self, expert_name: str, used_new: bool, success: bool, latency: float):
        """
        Record an AB test result for gate 3.

        Gate 3: Production validation (AB test)
        - Next 10 same-type tasks: 5 use new expert, 5 use baseline
        - New expert success_rate > baseline + 10%
        - Pass -> register as lifecycle=new
        - Fail -> archive
        """
        candidate = self._candidates.get(expert_name)
        if not candidate or candidate["status"] != "ab_testing":
            return

        candidate["ab_results"].append({
            "used_new": used_new,
            "success": success,
            "latency": round(latency, 2),
        })
        self._save_candidates()

        total = len(candidate["ab_results"])
        logger.info(
            "AB result for %s: used_new=%s success=%s (%d/10)",
            expert_name, used_new, success, total,
        )

        # Auto-check graduation when we have 10 results
        if total >= 10:
            self.check_graduation(expert_name)

    def check_graduation(self, expert_name: str) -> str:
        """
        Check if candidate should graduate or be archived.
        Returns 'pending' | 'graduated' | 'archived'.
        """
        candidate = self._candidates.get(expert_name)
        if not candidate:
            return "pending"

        results = candidate["ab_results"]
        if len(results) < 10:
            return "pending"

        # Split results
        new_results = [r for r in results if r["used_new"]]
        baseline_results = [r for r in results if not r["used_new"]]

        if not new_results or not baseline_results:
            return "pending"

        new_sr = sum(1 for r in new_results if r["success"]) / len(new_results)
        baseline_sr = sum(1 for r in baseline_results if r["success"]) / len(baseline_results)

        # Gate 3: new must beat baseline by >10%
        if new_sr > baseline_sr + 0.10:
            # Graduate: register as lifecycle=new
            expert_def = candidate["expert_def"]
            expert_def["lifecycle"] = "new"
            self.registry.register(expert_def)
            self.registry.save_to_disk(expert_def["name"])
            candidate["status"] = "graduated"
            self._save_candidates()
            logger.info(
                "Gate 3 PASSED — Expert %s graduated! new_sr=%.0f%% baseline_sr=%.0f%%",
                expert_name, new_sr * 100, baseline_sr * 100,
            )
            # P2: Queue flywheel notification for expert graduation
            try:
                from kaiwu.notification.flywheel_notifier import FlywheelNotifier
                notifier = FlywheelNotifier()
                new_latencies = [r["latency"] for r in new_results if r["success"]]
                baseline_latencies = [r["latency"] for r in baseline_results if r["success"]]
                notifier.queue_expert_born(
                    expert_def=expert_def,
                    metrics={
                        "task_count": len(results),
                        "success_rate_new": new_sr,
                        "success_rate_baseline": baseline_sr,
                        "avg_latency_new": sum(new_latencies) / len(new_latencies) if new_latencies else 0,
                        "avg_latency_baseline": sum(baseline_latencies) / len(baseline_latencies) if baseline_latencies else 0,
                    },
                )
            except Exception as e:
                logger.debug("Flywheel notification failed (non-blocking): %s", e)

            # 问题2修复：投产后触发 Prompt Optimization
            try:
                from kaiwu.cli.onboarding import load_config
                cfg = load_config().get("default", {})
                anthropic_key = cfg.get("anthropic_api_key", "")
                if anthropic_key:
                    trajectories = self.collector.get_by_expert(expert_name) if self.collector else []
                    self.run_prompt_optimization(expert_name, trajectories, anthropic_key)
            except Exception as e:
                logger.debug("Post-graduation prompt optimization failed (non-blocking): %s", e)

            return "graduated"

        # Failed gate 3 -> archive
        candidate["status"] = "archived"
        self._save_candidates()
        logger.info(
            "Gate 3 FAILED — Expert %s archived. new_sr=%.0f%% baseline_sr=%.0f%% (needed +10%%)",
            expert_name, new_sr * 100, baseline_sr * 100,
        )
        return "archived"

    # ── Persistence ──

    def _save_candidates(self):
        """Persist candidate state to disk."""
        os.makedirs(CANDIDATES_DIR, exist_ok=True)
        path = os.path.join(CANDIDATES_DIR, "candidates.json")
        data = {}
        for name, info in self._candidates.items():
            # Strip non-serializable fields from expert_def
            expert_def = {
                k: v for k, v in info["expert_def"].items()
                if k != "_source"
            }
            data[name] = {
                "expert_def": expert_def,
                "gate2_passed": info["gate2_passed"],
                "gate2_backtest": info.get("gate2_backtest", []),
                "backtest_success_rate": info.get("backtest_success_rate", 0.0),
                "baseline_success_rate": info["baseline_success_rate"],
                "baseline_avg_latency": info["baseline_avg_latency"],
                "ab_results": info["ab_results"],
                "status": info["status"],
            }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning("Failed to save candidates: %s", e)

    def _load_candidates(self):
        """Load candidate state from disk."""
        path = os.path.join(CANDIDATES_DIR, "candidates.json")
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                self._candidates = json.load(f)
        except Exception as e:
            logger.warning("Failed to load candidates: %s", e)
            self._candidates = {}

    # ── Prompt Optimization (SE-RED-4: 离线执行，使用外部API) ──

    def run_prompt_optimization(
        self,
        expert_name: str,
        trajectories: list[TaskTrajectory],
        api_key: str,
    ) -> bool:
        """
        AB测试通过后，分析成功轨迹优化YAML专家的system_prompt。
        SE-RED-4：使用外部API，用户需要提供API key。
        FLEX-1：无API key时跳过。
        """
        if not api_key:
            logger.info("[ab_tester] 无API key，跳过prompt优化")
            return False

        try:
            from kaiwu.flywheel.prompt_optimizer import PromptOptimizer
            optimizer = PromptOptimizer(api_key=api_key)
            return optimizer.optimize_expert(expert_name, trajectories, self.registry)
        except Exception as e:
            logger.warning("[ab_tester] prompt优化失败: %s", e)
            return False
