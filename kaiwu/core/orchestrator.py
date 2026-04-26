"""
Pipeline Orchestrator: routes Gate output to expert sequences.
RED-2: Deterministic pipeline, fixed sequence per expert_type.
RED-5: Max 3 retries, hardcoded.
"""

import logging
import time
from typing import Optional

from kaiwu.core.context import TaskContext
from kaiwu.experts.locator import LocatorExpert
from kaiwu.experts.generator import GeneratorExpert
from kaiwu.experts.verifier import VerifierExpert
from kaiwu.experts.search_augmentor import SearchAugmentorExpert
from kaiwu.experts.office_handler import OfficeHandlerExpert
from kaiwu.memory.kaiwu_md import KaiwuMemory
from kaiwu.registry.expert_registry import ExpertRegistry
from kaiwu.tools.executor import ToolExecutor
from kaiwu.flywheel.trajectory_collector import TrajectoryCollector
from kaiwu.flywheel.pattern_detector import PatternDetector

logger = logging.getLogger(__name__)

# RED-2: Fixed sequences per expert_type. No LLM decides next step.
EXPERT_SEQUENCES = {
    "locator_repair": ["locator", "generator", "verifier"],
    "codegen":        ["generator", "verifier"],
    "refactor":       ["locator", "generator", "verifier"],
    "doc":            ["generator"],
    "office":         ["office"],
}


class PipelineOrchestrator:
    """Deterministic expert pipeline orchestrator."""

    MAX_RETRIES = 3  # RED-5: hardcoded, do not change

    def __init__(
        self,
        locator: LocatorExpert,
        generator: GeneratorExpert,
        verifier: VerifierExpert,
        search_augmentor: SearchAugmentorExpert,
        office_handler: OfficeHandlerExpert,
        tool_executor: ToolExecutor,
        memory: KaiwuMemory,
        registry: ExpertRegistry | None = None,
        trajectory_collector: TrajectoryCollector | None = None,
    ):
        self.locator = locator
        self.generator = generator
        self.verifier = verifier
        self.search_augmentor = search_augmentor
        self.office_handler = office_handler
        self.tools = tool_executor
        self.memory = memory
        self.registry = registry
        self.trajectory_collector = trajectory_collector
        self._pattern_detector = PatternDetector(trajectory_collector) if trajectory_collector else None

    def run(
        self,
        user_input: str,
        gate_result: dict,
        project_root: str,
        on_status=None,
        no_search: bool = False,
    ) -> dict:
        """
        Execute the expert pipeline.
        on_status: optional callback(stage: str, detail: str) for CLI progress display.
        Returns {"success": bool, "context": TaskContext, "error": str|None, "elapsed": float}.
        """
        start_time = time.time()

        ctx = TaskContext(
            user_input=user_input,
            project_root=project_root,
            gate_result=gate_result,
            kaiwu_memory=self.memory.load(project_root),
            expert_system_prompt=gate_result.get("system_prompt", ""),
        )

        expert_type = gate_result.get("expert_type", "locator_repair")
        # Use custom pipeline from expert registry if available, else default
        if gate_result.get("route_type") == "expert_registry" and "pipeline" in gate_result:
            sequence = gate_result["pipeline"]
        else:
            sequence = EXPERT_SEQUENCES.get(expert_type, ["generator", "verifier"])

        self._emit(on_status, "gate", f"任务类型：{expert_type} | 难度：{gate_result.get('difficulty', '?')}")

        while ctx.retry_count < self.MAX_RETRIES:
            success = self._run_sequence(sequence, ctx, on_status)

            if success:
                elapsed = time.time() - start_time
                # Save to memory on success (with elapsed for expert/pattern tracking)
                self.memory.save(project_root, ctx, elapsed=elapsed)
                # Update expert registry stats
                expert_name = gate_result.get("expert_name")
                if expert_name and self.registry:
                    self.registry.update_stats(expert_name, success=True, latency=elapsed)
                # Flywheel: record trajectory and detect patterns (non-blocking)
                self._record_trajectory(ctx, True, elapsed, on_status)
                return {
                    "success": True,
                    "context": ctx,
                    "error": None,
                    "elapsed": elapsed,
                }

            ctx.retry_count += 1
            error_detail = ""
            if ctx.verifier_output:
                error_detail = ctx.verifier_output.get("error_detail", "")

            self._emit(on_status, "retry", f"第{ctx.retry_count}次尝试失败：{error_detail[:100]}")

            # Trigger SearchAugmentor: failed 2x OR hard task failed 1x
            should_search = (
                ctx.retry_count >= 2
                or (gate_result.get("difficulty") == "hard" and ctx.retry_count >= 1)
            )
            if should_search and not ctx.search_triggered and not no_search:
                self._emit(on_status, "search", "触发搜索增强...")
                ctx.search_results = self.search_augmentor.search(ctx)
                ctx.search_triggered = True
                self._emit(on_status, "search_done", f"搜索完成，注入{len(ctx.search_results)}字参考信息")

            # Reset expert outputs for retry (RED-3: fresh context each attempt)
            ctx.locator_output = None
            ctx.generator_output = None
            ctx.verifier_output = None
            ctx.relevant_code_snippets = {}

        elapsed = time.time() - start_time
        # Record failure in pattern memory
        self.memory.save_failure(project_root, ctx, elapsed=elapsed)
        # Update expert registry stats on failure
        expert_name = gate_result.get("expert_name")
        if expert_name and self.registry:
            self.registry.update_stats(expert_name, success=False, latency=elapsed)
        # Flywheel: record failure trajectory
        self._record_trajectory(ctx, False, elapsed, on_status)
        return {
            "success": False,
            "context": ctx,
            "error": f"Max retries ({self.MAX_RETRIES}) exceeded",
            "elapsed": elapsed,
        }

    def _run_sequence(self, sequence: list[str], ctx: TaskContext, on_status) -> bool:
        """Execute a fixed expert sequence. Returns True if all steps pass."""
        for step in sequence:
            if step == "locator":
                self._emit(on_status, "locator", "定位中...")
                result = self.locator.run(ctx)
                if not result:
                    self._emit(on_status, "locator_fail", "定位失败")
                    return False
                files = result.get("relevant_files", [])
                funcs = result.get("relevant_functions", [])
                self._emit(on_status, "locator_done", f"文件：{', '.join(files[:3])} | 函数：{', '.join(funcs[:3])}")

            elif step == "generator":
                self._emit(on_status, "generator", "生成patch...")
                result = self.generator.run(ctx)
                if not result:
                    self._emit(on_status, "generator_fail", "生成失败")
                    return False
                n_patches = len(result.get("patches", []))
                self._emit(on_status, "generator_done", f"生成{n_patches}个patch")

            elif step == "verifier":
                self._emit(on_status, "verifier", "验证中...")
                result = self.verifier.run(ctx)
                if not result or not result.get("passed"):
                    detail = result.get("error_detail", "unknown") if result else "no result"
                    self._emit(on_status, "verifier_fail", f"验证失败：{detail[:80]}")
                    return False
                tp = result.get("tests_passed", 0)
                tt = result.get("tests_total", 0)
                self._emit(on_status, "verifier_done", f"语法OK | 测试：{tp}/{tt}")

            elif step == "office":
                result = self.office_handler.run(ctx)
                if not result.get("passed", False):
                    self._emit(on_status, "office_fail", result.get("error", ""))
                    return False

        return True

    def _record_trajectory(self, ctx: TaskContext, success: bool, elapsed: float, on_status):
        """Record trajectory and run pattern detection (non-blocking, never raises)."""
        if not self.trajectory_collector:
            return
        try:
            model = getattr(self, '_model_name', 'unknown')
            self.trajectory_collector.record(ctx, success, elapsed, model)
            # On success, check for flywheel candidates
            if success and self._pattern_detector:
                candidates = self._pattern_detector.detect()
                if candidates:
                    names = [c["expert_type"] for c in candidates]
                    self._emit(on_status, "flywheel", f"发现{len(candidates)}个专家候选：{names}")
        except Exception as e:
            logger.debug("Flywheel recording failed (non-blocking): %s", e)

    @staticmethod
    def _emit(callback, stage: str, detail: str):
        """Emit status update if callback provided."""
        if callback:
            callback(stage, detail)
        logger.info("[%s] %s", stage, detail)
