"""
Pipeline Orchestrator: routes Gate output to expert sequences.
RED-2: Deterministic pipeline, fixed sequence per expert_type.
RED-5: Max 3 retries, hardcoded.
"""

import logging
import time
import threading
from typing import Optional

from kaiwu.core.context import TaskContext
from kaiwu.core.event_bus import EventBus
from kaiwu.core.cognitive_gate import CognitiveGate
from kaiwu.core.wink import WinkMonitor
from kaiwu.experts.locator import LocatorExpert
from kaiwu.experts.generator import GeneratorExpert
from kaiwu.experts.verifier import VerifierExpert
from kaiwu.experts.search_augmentor import SearchAugmentorExpert
from kaiwu.experts.office_handler import OfficeHandlerExpert
from kaiwu.experts.chat_expert import ChatExpert
from kaiwu.memory.kaiwu_md import KaiwuMemory
from kaiwu.registry.expert_registry import ExpertRegistry
from kaiwu.tools.executor import ToolExecutor
from kaiwu.flywheel.trajectory_collector import TrajectoryCollector
from kaiwu.flywheel.pattern_detector import PatternDetector
from kaiwu.flywheel.ab_tester import ABTester
from kaiwu.core.checkpoint import Checkpoint
from kaiwu.core.kwcode_md import load_kwcode_md, build_kwcode_system
from kaiwu.stats.value_tracker import ValueTracker
from kaiwu.notification.flywheel_notifier import FlywheelNotifier

logger = logging.getLogger(__name__)

# RED-2: Fixed sequences per expert_type. No LLM decides next step.
EXPERT_SEQUENCES = {
    "locator_repair": ["locator", "generator", "verifier"],
    "codegen":        ["generator", "verifier"],
    "refactor":       ["locator", "generator", "verifier"],
    "doc":            ["locator", "generator"],
    "office":         ["office"],
    "chat":           ["chat"],
    "vision":         ["vision"],
}

# ── 错误策略路由：按 error_type 切换重试序列 ──
# 理论来源：Turn-Control Strategies（arXiv:2510.16786）；Wink（arXiv:2602.17037）
RETRY_STRATEGIES = {
    "syntax": {
        "sequence": ["generator", "verifier"],
        "hint": "只修复语法错误，错误在 {error_file}:{error_line}，不改其他逻辑",
        "search": False,
    },
    "assertion": {
        "sequence": ["generator", "verifier"],
        "hint": "测试期望：{error_message}，只改让测试通过的最小代码",
        "search": False,
    },
    "import": {
        "sequence": ["import_fixer", "verifier"],
        "hint": "",
        "search": True,
    },
    "patch_apply": {
        "sequence": ["locator", "generator", "verifier"],
        "hint": "重新读取文件最新内容，不要使用缓存的 original",
        "search": False,
    },
    "runtime": {
        "sequence": ["debugger", "generator", "verifier"],
        "hint": "",
        "search": False,
    },
    "unknown": {
        "sequence": ["generator", "verifier"],
        "hint": "缩小修改范围，只改最小可疑函数",
        "search": False,
        "scope_narrow": True,
    },
}


class PipelineOrchestrator:
    """Deterministic expert pipeline orchestrator."""

    MAX_RETRIES = 3  # Default, overridden by _get_max_retries()
    _RETRY_BY_DIFFICULTY = {"easy": 2, "hard": 4}  # Dynamic budget

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
        ab_tester: ABTester | None = None,
        chat_expert: ChatExpert | None = None,
        debug_subagent=None,
        vision_expert=None,
        bus: EventBus | None = None,
    ):
        self.locator = locator
        self.generator = generator
        self.verifier = verifier
        self.search_augmentor = search_augmentor
        self.office_handler = office_handler
        self.chat_expert = chat_expert
        self.vision_expert = vision_expert
        self.tools = tool_executor
        self.memory = memory
        self.registry = registry
        self.trajectory_collector = trajectory_collector
        self._pattern_detector = PatternDetector(trajectory_collector) if trajectory_collector else None
        self.ab_tester = ab_tester
        self.debug_subagent = debug_subagent
        self._value_tracker = ValueTracker()
        self._notifier = FlywheelNotifier()
        self.bus = bus or EventBus()
        self._wink = WinkMonitor()
        self._cognitive_gate = CognitiveGate()

    def run(
        self,
        user_input: str,
        gate_result: dict,
        project_root: str,
        on_status=None,
        no_search: bool = False,
        skip_checkpoint: bool = False,
        pre_search_results: str = "",
        image_paths: list = None,
    ) -> dict:
        """
        Execute the expert pipeline.
        on_status: optional callback(stage: str, detail: str) for CLI progress display.
        Returns {"success": bool, "context": TaskContext, "error": str|None, "elapsed": float}.
        """
        start_time = time.time()

        # ── Watchdog: task-level timeout (P2) ──
        TASK_TIMEOUT_S = 300  # 5 minutes max per task
        _watchdog_triggered = threading.Event()

        def _watchdog_timer():
            _watchdog_triggered.set()

        _watchdog = threading.Timer(TASK_TIMEOUT_S, _watchdog_timer)
        _watchdog.daemon = True
        _watchdog.start()

        # Store project_root for Gate 2 backtest use
        self._backtest_project_root = project_root

        ctx = TaskContext(
            user_input=user_input,
            project_root=project_root,
            gate_result=gate_result,
            kaiwu_memory=self.memory.load(project_root),
            expert_system_prompt=gate_result.get("system_prompt", ""),
        )

        # 处理图片路径
        if image_paths:
            ctx.image_paths = list(image_paths)
            logger.info(f"[orchestrator] 任务包含 {len(image_paths)} 张图片")

        expert_type = gate_result.get("expert_type", "locator_repair")

        # ── Pre-search results injection (P1-B) ──
        if pre_search_results:
            ctx.search_results = pre_search_results
            ctx.search_triggered = True
            self._emit(on_status, "search", "已预加载实时数据")

        # ── KWCODE.md rules injection ──
        kwcode_sections = load_kwcode_md(project_root)
        if kwcode_sections:
            kwcode_rules = build_kwcode_system(expert_type, kwcode_sections)
            if kwcode_rules:
                ctx.kwcode_rules = kwcode_rules
                # Prepend to expert_system_prompt so it flows to all experts
                if ctx.expert_system_prompt:
                    ctx.expert_system_prompt = f"{kwcode_rules}\n\n{ctx.expert_system_prompt}"
                else:
                    ctx.expert_system_prompt = kwcode_rules

        # chat类型：直接回复，不走AB测试/搜索/重试
        if expert_type == "chat":
            self._emit(on_status, "chat", "聊天模式")
            if self.chat_expert:
                result = self.chat_expert.run(ctx)
            else:
                ctx.generator_output = {"explanation": "我是KWCode，专注于代码任务。", "patches": []}
                result = {"passed": True}
            elapsed = time.time() - start_time
            return {
                "success": True,
                "context": ctx,
                "error": None,
                "elapsed": elapsed,
            }

        # vision类型：图片处理任务
        if expert_type == "vision":
            self._emit(on_status, "vision", "图片处理模式")
            if self.vision_expert and ctx.image_paths:
                result = self.vision_expert.run(ctx)
                explanation = result.get("output", "").strip()
                ctx.generator_output = {
                    "explanation": explanation,
                    "patches": [],
                    "metadata": {"vision": result.get("metadata", {})},
                }
                elapsed = time.time() - start_time
                success = result.get("success", False)
                return {
                    "success": success,
                    "context": ctx,
                    "error": None if success else explanation or "图片处理失败",
                    "elapsed": elapsed,
                }
            else:
                ctx.generator_output = {"explanation": "图片处理功能需要配置Vision专家", "patches": []}
                elapsed = time.time() - start_time
                return {
                    "success": False,
                    "context": ctx,
                    "error": "Vision专家未配置或未提供图片",
                    "elapsed": elapsed,
                }

        # Gate 3: AB test — check if a candidate expert should be used for this task
        ab_candidate_name = None
        ab_used_new = False
        if self.ab_tester and expert_type != "chat":
            candidate_def = self.ab_tester.should_use_candidate(expert_type)
            if candidate_def:
                ab_candidate_name = candidate_def["name"]
                ab_used_new = True
                # Override gate_result to use the candidate expert's pipeline
                gate_result = {
                    **gate_result,
                    "expert_name": ab_candidate_name,
                    "route_type": "expert_registry",
                    "pipeline": candidate_def.get("pipeline", []),
                    "system_prompt": candidate_def.get("system_prompt", ""),
                }
                self._emit(on_status, "ab_test", f"AB测试：使用候选专家 {ab_candidate_name}")
            else:
                # Check if any candidate is in AB testing for this type (baseline run)
                for name, info in self.ab_tester._candidates.items():
                    if (info["status"] == "ab_testing"
                            and info["expert_def"].get("type") == expert_type
                            and len(info["ab_results"]) < 10):
                        ab_candidate_name = name
                        ab_used_new = False
                        self._emit(on_status, "ab_test", f"AB测试：基线对照（候选 {name}）")
                        break

        # Use custom pipeline from expert registry if available, else default
        if gate_result.get("route_type") == "expert_registry" and "pipeline" in gate_result:
            sequence = gate_result["pipeline"]
        else:
            sequence = EXPERT_SEQUENCES.get(expert_type, ["generator", "verifier"])

        self._emit(on_status, "gate", f"任务类型：{expert_type} | 难度：{gate_result.get('difficulty', '?')}")

        # ── Experience Replay: find similar successful trajectories ──
        if self.trajectory_collector and expert_type not in ("chat", "office", "vision"):
            try:
                similar = self.trajectory_collector.find_similar(user_input, expert_type, k=3)
                if similar:
                    ctx.similar_trajectories = similar
                    best = similar[0]
                    self._emit(on_status, "replay",
                               f"发现相似成功案例：{best.get('user_input', '')[:40]}")
            except Exception as e:
                logger.debug("Experience replay failed (non-blocking): %s", e)

        # codegen任务如果涉及实时数据，首次就触发搜索（不等失败重试）
        if expert_type == "codegen" and not no_search and self._needs_realtime_data(user_input):
            try:
                self._emit(on_status, "search", "检测到实时数据需求，预搜索...")
                results = self.search_augmentor.search(ctx)
                if results:
                    ctx.search_results = results
                    ctx.search_triggered = True
                    self._emit(on_status, "search_done", f"搜索完成，注入{len(results)}字参考信息")
            except Exception as e:
                logger.debug("Pre-search failed (网络保护，不阻塞): %s", e)

        # ── Plan 自动触发：hard 任务自动生成计划（不打断用户）──
        if (gate_result.get("difficulty") == "hard"
                and expert_type not in ("chat", "office", "vision")
                and not ctx.subtask_results):
            try:
                from kaiwu.core.planner import Planner
                from kaiwu.memory import pattern_md
                planner = Planner(
                    locator=self.locator,
                    pattern_md_module=pattern_md,
                    llm=self.generator.llm,
                )
                plan = planner.generate_plan_steps(user_input, gate_result, project_root)
                if plan and len(plan) > 1:
                    ctx.execution_plan = plan
                    self._emit(on_status, "plan_generated", f"自动生成 {len(plan)} 步计划")
                    self.bus.emit("plan_generated", {"steps": len(plan), "msg": f"生成 {len(plan)} 步计划"})
            except Exception as e:
                logger.debug("Auto-plan failed (non-blocking): %s", e)

        # ── Checkpoint: snapshot before execution (skip in multi-task to avoid race) ──
        checkpoint = Checkpoint(project_root)
        checkpoint_saved = False
        if not skip_checkpoint:
            checkpoint_saved = checkpoint.save()
            if not checkpoint_saved:
                self._emit(on_status, "warning", "无法创建文件快照，任务失败时需手动还原")

        # Dynamic retry budget based on task difficulty
        max_retries = self._get_max_retries(gate_result)

        # Low confidence: reduce retry budget (not worth many attempts)
        confidence = gate_result.get("confidence", 1.0)
        if confidence < 0.6 and expert_type not in ("chat", "office", "vision"):
            max_retries = min(max_retries, 2)
            self._emit(on_status, "low_confidence",
                       f"任务分类置信度较低({confidence:.0%})，减少重试次数")

        # ── CognitiveGate reset for this task ──
        self._cognitive_gate.reset()

        while ctx.retry_count < max_retries:
            # Watchdog check: abort if task exceeded timeout
            if _watchdog_triggered.is_set():
                self._emit(on_status, "watchdog", f"任务超时({TASK_TIMEOUT_S}s)，强制终止")
                self.bus.emit("circuit_break", {"msg": f"任务超时({TASK_TIMEOUT_S}s)"})
                break

            success = self._run_sequence(sequence, ctx, on_status)

            # Notify locator of task result (graph stats + incremental update)
            self._notify_locator(ctx, success)

            if success:
                elapsed = time.time() - start_time
                checkpoint.discard()  # Clean up snapshot on success

                # Reviewer: 需求对齐审查（非阻塞，不影响成功判定）
                review_result = self._do_review(ctx, on_status)

                # Save to memory on success (with elapsed for expert/pattern tracking)
                self.memory.save(project_root, ctx, elapsed=elapsed)
                # Update expert registry stats
                expert_name = gate_result.get("expert_name")
                if expert_name and self.registry:
                    self.registry.update_stats(expert_name, success=True, latency=elapsed)
                # Flywheel: record trajectory and detect patterns (non-blocking)
                self._record_trajectory(ctx, True, elapsed, on_status)
                # Gate 3: record AB test result if this task is part of an AB test
                self._record_ab_result(ab_candidate_name, ab_used_new, True, elapsed, on_status)
                # P2: Value tracking (local SQLite)
                self._record_value(project_root, gate_result, True, elapsed, ctx)
                # P2: Milestone check
                self._check_milestone(on_status)
                # Reflexion持久化：成功时也记录注意事项
                self._persist_reflection(project_root, ctx, gate_result, success=True)
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

            # Save failure info for retry strategy
            ctx.previous_failure = error_detail

            # ── CognitiveGate: 检测边际收益递减 ──
            if ctx.generator_output:
                self._cognitive_gate.record(ctx.generator_output.get("patches", []))
            cg_stop, cg_reason = self._cognitive_gate.should_stop()
            if cg_stop:
                self._emit(on_status, "circuit_break", cg_reason)
                self.bus.emit("circuit_break", {"msg": cg_reason})
                break

            # ── Circuit breaker: same error_type streak ──
            current_error_type = ""
            if ctx.verifier_output:
                current_error_type = ctx.verifier_output.get("error_type", "unknown")

            if not hasattr(ctx, '_error_type_streak'):
                ctx._error_type_streak = {"type": "", "count": 0}

            if current_error_type and current_error_type == ctx._error_type_streak["type"]:
                ctx._error_type_streak["count"] += 1
            else:
                ctx._error_type_streak = {"type": current_error_type, "count": 1}

            # Fast circuit break: syntax errors don't improve with retries
            if current_error_type == "syntax" and ctx.retry_count >= 1:
                self._emit(on_status, "circuit_break", "语法错误重试无效，模型能力不足以完成此任务")
                self.bus.emit("circuit_break", {"msg": "syntax error"})
                break
            # Fast circuit break: missing imports — try import_fixer first
            if current_error_type == "import":
                fixed = self._try_import_fix(ctx, on_status)
                if not fixed:
                    missing = ctx.verifier_output.get("error_message", "") if ctx.verifier_output else ""
                    self._emit(on_status, "circuit_break", f"缺少依赖：{missing}，请先安装")
                    self.bus.emit("circuit_break", {"msg": f"import: {missing}"})
                    break
                # import_fixer succeeded, continue retry loop
            # Hard circuit break: same error type 3 times in a row
            if ctx._error_type_streak["count"] >= 3:
                self._emit(on_status, "circuit_break",
                           f"同类错误({current_error_type})连续{ctx._error_type_streak['count']}次，停止重试")
                self.bus.emit("circuit_break", {"msg": f"{current_error_type} x{ctx._error_type_streak['count']}"})
                break

            # ── Wink 自修复：检测偏离并注入纠正 ──
            wink_hint = self._wink.check(ctx, self.bus)

            # ── 错误策略路由：按 error_type 切换重试序列 ──
            retry_strategy = RETRY_STRATEGIES.get(current_error_type, RETRY_STRATEGIES["unknown"])
            sequence = retry_strategy["sequence"]
            ctx.retry_hint = self._build_retry_hint(ctx, current_error_type)
            if wink_hint:
                ctx.retry_hint = (ctx.retry_hint + "\n" + wink_hint).strip() if ctx.retry_hint else wink_hint

            # ── Scope narrowing: on 2nd failure, reduce to first file+function ──
            if ctx.retry_count == 2 and ctx.locator_output:
                files = ctx.locator_output.get("relevant_files", [])
                funcs = ctx.locator_output.get("relevant_functions", [])
                if len(files) > 1 and funcs:
                    ctx.locator_output = {
                        "relevant_files": [files[0]],
                        "relevant_functions": [funcs[0]],
                        "edit_locations": ctx.locator_output.get("edit_locations", [])[:1],
                        "method": "scope_narrowed",
                    }
                    if ctx.relevant_code_snippets:
                        ctx.relevant_code_snippets = {
                            files[0]: ctx.relevant_code_snippets.get(files[0], "")
                        }
                    self._emit(on_status, "scope_narrow", f"缩小范围：只修 {funcs[0]}()")
                    self.bus.emit("scope_narrow", {"msg": f"只修 {funcs[0]}()"})

            self._emit(on_status, "retry", f"第{ctx.retry_count}次尝试失败：{error_detail[:100]}")
            self.bus.emit("retry", {"count": ctx.retry_count, "error": error_detail[:100]})

            # Reflection before 2nd retry: ask LLM why the patch failed
            if ctx.retry_count == 1 and ctx.verifier_output and ctx.generator_output:
                self._do_reflection(ctx, on_status)

            # Debug Subagent: capture runtime info on failure (test failures only)
            if ctx.retry_count >= 1 and ctx.verifier_output:
                self._do_debug(ctx, on_status)

            # Set retry strategy: each retry uses a different approach
            ctx.retry_strategy = ctx.retry_count  # 0→1→2

            # ── 错误驱动搜索：按失败类型决定是否搜索（网络保护：异常不阻塞）──
            if self._should_search(current_error_type, ctx.retry_count) and not ctx.search_triggered and not no_search:
                try:
                    self._emit(on_status, "search", f"搜索 {current_error_type} 解法...")
                    self.bus.emit("search_start", {"msg": f"搜索 {current_error_type} 解法"})
                    results = self.search_augmentor.search(ctx)
                    if results:  # 搜到才用，搜不到继续原流程
                        ctx.search_results = results
                        ctx.search_triggered = True
                        self._emit(on_status, "search_done", f"搜索完成，注入{len(results)}字参考信息")
                        self.bus.emit("search_solution", {"msg": "找到参考方案"})
                    else:
                        ctx.search_triggered = True  # 标记已尝试，不重复触发
                except Exception as e:
                    logger.debug("Search failed (网络保护，不阻塞): %s", e)
                    ctx.search_triggered = True  # 失败也标记，避免循环重试搜索

            # Reset expert outputs for retry (RED-3: fresh context each attempt)
            ctx.locator_output = None
            ctx.generator_output = None
            ctx.verifier_output = None
            ctx.relevant_code_snippets = {}
            # Clear ephemeral debug info to prevent context pollution
            ctx.debug_info = ""

        _watchdog.cancel()  # Clean up watchdog timer
        elapsed = time.time() - start_time

        # ── Checkpoint: restore on failure ──
        if checkpoint_saved:
            restored = checkpoint.restore()
            if restored:
                self._emit(on_status, "checkpoint", "已还原到任务执行前的状态")
            else:
                self._emit(on_status, "warning", "还原失败，请手动检查文件")
        # Downgrade suggestion
        self._suggest_downgrade(ctx, on_status)

        # Record failure in pattern memory
        self.memory.save_failure(project_root, ctx, elapsed=elapsed)
        # Update expert registry stats on failure
        expert_name = gate_result.get("expert_name")
        if expert_name and self.registry:
            self.registry.update_stats(expert_name, success=False, latency=elapsed)
        # Flywheel: record failure trajectory
        self._record_trajectory(ctx, False, elapsed, on_status)
        # Gate 3: record AB test failure if this task is part of an AB test
        self._record_ab_result(ab_candidate_name, ab_used_new, False, elapsed, on_status)
        # P2: Value tracking (local SQLite)
        self._record_value(project_root, gate_result, False, elapsed, ctx)
        # Reflexion持久化：失败时记录根因
        self._persist_reflection(project_root, ctx, gate_result, success=False)
        return {
            "success": False,
            "context": ctx,
            "error": f"Max retries ({max_retries}) exceeded",
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
                self._emit(on_status, "office", "生成Office文档...")
                result = self.office_handler.run(ctx)
                if not result.get("passed", False):
                    self._emit(on_status, "office_fail", result.get("error", "生成失败"))
                    return False
                self._emit(on_status, "office_done", result.get("output", "完成"))

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

    def _record_ab_result(self, candidate_name, used_new, success, elapsed, on_status):
        """Record AB test result for gate 3 (non-blocking, never raises)."""
        if not self.ab_tester or not candidate_name:
            return
        try:
            self.ab_tester.record_ab_result(candidate_name, used_new, success, elapsed)
            total = len(self.ab_tester._candidates.get(candidate_name, {}).get("ab_results", []))
            self._emit(on_status, "ab_test_record",
                       f"AB结果已记录：{'候选' if used_new else '基线'} "
                       f"{'成功' if success else '失败'} ({total}/10)")
            # Auto-graduation is handled inside record_ab_result when total >= 10
            status = self.ab_tester._candidates.get(candidate_name, {}).get("status", "")
            if status == "graduated":
                self._emit(on_status, "ab_graduated",
                           f"专家 {candidate_name} 通过Gate 3，已注册投产！")
            elif status == "archived":
                self._emit(on_status, "ab_archived",
                           f"专家 {candidate_name} 未通过Gate 3，已归档")
        except Exception as e:
            logger.debug("AB result recording failed (non-blocking): %s", e)

    def _notify_locator(self, ctx: TaskContext, success: bool):
        """Notify locator of task result for graph stats + incremental update (non-blocking)."""
        try:
            if hasattr(self.locator, 'notify_task_result'):
                self.locator.notify_task_result(ctx, success)
        except Exception as e:
            logger.debug("Locator notify failed (non-blocking): %s", e)

    def _suggest_downgrade(self, ctx: TaskContext, on_status):
        """Post-failure: suggest narrowing scope (small model enhancement)."""
        files = ctx.locator_output.get("relevant_files", []) if ctx.locator_output else []
        functions = ctx.locator_output.get("relevant_functions", []) if ctx.locator_output else []

        if len(files) > 1 and functions:
            first_func = functions[0]
            self._emit(on_status, "suggest",
                       f"建议缩小范围重试：只修复 {first_func}() 函数")
        elif len(files) == 1 and ctx.gate_result.get("difficulty") == "hard":
            self._emit(on_status, "suggest", "任务较复杂，建议拆分后分步执行")

    def _do_reflection(self, ctx: TaskContext, on_status):
        """Ask LLM to analyze why the previous patch failed. One sentence, ≤50字."""
        try:
            error = ctx.verifier_output.get("error_detail", "") if ctx.verifier_output else ""
            patches = ctx.generator_output.get("patches", []) if ctx.generator_output else []
            modified_snippet = patches[0].get("modified", "")[:500] if patches else ""

            reflection_prompt = (
                f"你刚才生成的patch失败了。\n"
                f"失败原因：{error[:300]}\n"
                f"你修改的代码片段：\n{modified_snippet}\n\n"
                f"分析：这个patch为什么会失败？根本原因是什么？\n"
                f"用一句话回答，不超过50字。"
            )
            reflection = self.generator.llm.generate(
                prompt=reflection_prompt,
                system="你是代码审查专家，只做错误分析，不生成代码。",
                max_tokens=100,
                temperature=0.0,
            )
            ctx.reflection = reflection.strip()
            logger.info("[orchestrator] reflection: %s", ctx.reflection)
            self._emit(on_status, "reflection", f"反思：{ctx.reflection[:80]}")
        except Exception as e:
            logger.debug("Reflection failed (non-blocking): %s", e)

    def _do_debug(self, ctx: TaskContext, on_status):
        """Debug Subagent: capture runtime info after test failure (non-blocking)."""
        if not self.debug_subagent:
            return
        try:
            self._emit(on_status, "debug", "调试子代理：采集运行时信息...")
            debug_info = self.debug_subagent.investigate(ctx)
            if debug_info:
                ctx.debug_info = debug_info
                self._emit(on_status, "debug_done", f"调试信息：{debug_info[:80]}")
            else:
                self._emit(on_status, "debug_done", "未获取到额外调试信息")
        except Exception as e:
            logger.debug("Debug subagent failed (non-blocking): %s", e)

    def _do_review(self, ctx: TaskContext, on_status) -> dict:
        """Reviewer: 需求对齐审查（非阻塞）。成功后检查代码是否真正满足用户意图。"""
        try:
            from kaiwu.experts.reviewer import ReviewerExpert
            reviewer = ReviewerExpert(llm=self.generator.llm)
            self._emit(on_status, "review", "审查需求对齐...")
            result = reviewer.review(ctx)
            if result.get("aligned"):
                self._emit(on_status, "review_done", "需求对齐确认")
            else:
                gap = result.get("gap", "")
                self._emit(on_status, "review_gap", f"注意：{gap}")
            return result
        except Exception as e:
            logger.debug("Reviewer failed (non-blocking): %s", e)
            return {"aligned": True, "confidence": 0.0, "gap": ""}

    @staticmethod
    def _emit(callback, stage: str, detail: str):
        """Emit status update if callback provided."""
        if callback:
            callback(stage, detail)
        logger.info("[%s] %s", stage, detail)

    def _get_max_retries(self, gate_result: dict) -> int:
        """Dynamic retry budget based on task difficulty."""
        difficulty = gate_result.get("difficulty", "easy")
        return self._RETRY_BY_DIFFICULTY.get(difficulty, self.MAX_RETRIES)

    @staticmethod
    def _needs_realtime_data(user_input: str) -> bool:
        """检测用户输入是否需要实时数据（天气、股价、新闻等）。"""
        keywords = [
            "天气", "气温", "温度", "weather", "forecast",
            "股价", "股票", "汇率", "价格", "price",
            "新闻", "最新", "最近", "今天", "今日", "本周", "这周", "一周",
            "news", "latest", "today", "recent",
        ]
        lower = user_input.lower()
        return any(kw in lower for kw in keywords)

    def _record_value(self, project_root, gate_result, success, elapsed, ctx):
        """P2: Record task to local SQLite for value dashboard (non-blocking)."""
        try:
            self._value_tracker.record(
                project_root=project_root,
                expert_type=gate_result.get("expert_type", ""),
                expert_name=gate_result.get("expert_name", "") or "",
                success=success,
                elapsed_s=elapsed,
                retry_count=ctx.retry_count,
                model=getattr(self, '_model_name', 'unknown'),
            )
        except Exception as e:
            logger.debug("Value tracking failed (non-blocking): %s", e)

    def _check_milestone(self, on_status):
        """P2: Check if total task count hits a milestone (50/100/200/500)."""
        MILESTONES = {50, 100, 200, 500}
        try:
            total = self._value_tracker.get_total_task_count()
            if total in MILESTONES:
                expert_count = len(self.registry.list_experts(expert_type="generated")) if self.registry else 0
                self._notifier.queue_milestone(total, expert_count, 0.0)
        except Exception as e:
            logger.debug("Milestone check failed (non-blocking): %s", e)

    def _persist_reflection(self, project_root, ctx, gate_result, success):
        """Reflexion持久化：任务完成后写入REFLECTION.md（非阻塞）。"""
        try:
            if not ctx.reflection:
                return
            from kaiwu.memory.pattern_md import save_reflection
            save_reflection(
                project_root=project_root,
                expert_type=gate_result.get("expert_type", "unknown"),
                task_summary=ctx.user_input[:30],
                reflection=ctx.reflection,
                success=success,
            )
        except Exception as e:
            logger.debug("Reflection persistence failed (non-blocking): %s", e)

    def _build_retry_hint(self, ctx: TaskContext, error_type: str) -> str:
        """按错误类型生成重试提示，注入 Generator prompt。"""
        strategy = RETRY_STRATEGIES.get(error_type, RETRY_STRATEGIES["unknown"])
        template = strategy.get("hint", "")
        if not template:
            return ""
        v = ctx.verifier_output or {}
        try:
            return template.format(
                error_file=v.get("error_file", ""),
                error_line=v.get("error_line", 0),
                error_message=v.get("error_message", ""),
            )
        except (KeyError, ValueError):
            return template

    def _should_search(self, error_type: str, retry_count: int) -> bool:
        """按失败类型决定是否搜网络，不是统一在 retry>=2 时搜。"""
        strategy = RETRY_STRATEGIES.get(error_type, RETRY_STRATEGIES["unknown"])
        # import 错误：立刻搜
        if strategy.get("search") and retry_count >= 1:
            return True
        # runtime 错误：debug 一次后仍失败才搜
        if error_type == "runtime" and retry_count >= 2:
            return True
        # assertion 连续 2 次同样错误：搜最优解法
        if error_type == "assertion" and retry_count >= 2:
            return True
        # 通用 fallback：第3次失败搜
        if retry_count >= 3:
            return True
        return False

    def _try_import_fix(self, ctx: TaskContext, on_status) -> bool:
        """尝试用 import_fixer 确定性修复缺失 import（不调 LLM）。"""
        try:
            from kaiwu.tools.import_fixer import fix_missing_import
            v = ctx.verifier_output or {}
            error_msg = v.get("error_message", "")
            error_file = v.get("error_file", "")
            if not error_file or not error_msg:
                return False
            content = self.tools.read_file(error_file)
            if content.startswith("[ERROR]"):
                return False
            fixed = fix_missing_import(content, error_msg)
            if fixed and fixed != content:
                self.tools.write_file(error_file, fixed)
                self._emit(on_status, "import_fix", f"自动修复 import: {error_file}")
                self.bus.emit("file_written", {"path": error_file})
                return True
            return False
        except Exception as e:
            logger.debug("Import fixer failed (non-blocking): %s", e)
            return False
