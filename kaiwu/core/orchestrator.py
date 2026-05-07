"""
Pipeline Orchestrator: routes Gate output to expert sequences.
RED-2: Deterministic pipeline, fixed sequence per expert_type.
RED-5: Max 3 retries, hardcoded.
"""

import logging
import time
import threading
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from kaiwu.experts.debug_subagent import DebugSubagent
    from kaiwu.experts.vision_expert import VisionExpert

from kaiwu.core.context import TaskContext
from kaiwu.core.event_bus import EventBus
from kaiwu.core.cognitive_gate import CognitiveGate
from kaiwu.core.wink import WinkMonitor
from kaiwu.core.gap_detector import GapDetector, GapType, Gap, GAP_TO_EXPERT_TYPE
from kaiwu.core.execution_state import ExecutionStateTracker
from kaiwu.core.env_prober import EnvProber
from kaiwu.core.test_parser import extract_failing_tests, extract_passing_tests
from kaiwu.experts.locator import LocatorExpert
from kaiwu.experts.generator import GeneratorExpert
from kaiwu.experts.verifier import VerifierExpert
from kaiwu.experts.search_augmentor import SearchAugmentorExpert
from kaiwu.experts.search_subagent import SearchSubagent
from kaiwu.experts.office_handler import OfficeHandlerExpert
from kaiwu.experts.chat_expert import ChatExpert
from kaiwu.memory.kaiwu_md import KaiwuMemory
from kaiwu.registry.expert_registry import ExpertRegistry
from kaiwu.tools.executor import ToolExecutor
from kaiwu.flywheel.trajectory_collector import TrajectoryCollector

__all__ = ["PipelineOrchestrator"]
from kaiwu.flywheel.pattern_detector import PatternDetector
from kaiwu.flywheel.ab_tester import ABTester
from kaiwu.core.checkpoint import Checkpoint
from kaiwu.core.kwcode_md import load_kwcode_md, build_kwcode_system
from kaiwu.core.upstream_manifest import UpstreamManifest
from kaiwu.stats.value_tracker import ValueTracker
from kaiwu.notification.flywheel_notifier import FlywheelNotifier
from kaiwu.flywheel.strategy_stats import StrategyStats
from kaiwu.flywheel.user_pattern_memory import UserPatternMemory
from kaiwu.telemetry.client import TelemetryClient
from kaiwu.audit.logger import AuditLogger
from kaiwu.core.model_capability import detect_model_tier, STRATEGIES, ModelTier

logger = logging.getLogger(__name__)

# RED-2: Fixed sequences per expert_type.
EXPERT_SEQUENCES = {
    "locator_repair": ["locator", "generator", "verifier"],
    "codegen":        ["generator", "verifier"],
    "refactor":       ["locator", "generator", "verifier"],
    "doc":            ["locator", "generator"],
    "office":         ["office"],
    "chat":           ["chat"],
    "vision":         ["vision"],
}

# 错误策略路由：按 error_type 切换重试序列
# 理论来源：Turn-Control Strategies（arXiv:2510.16786）；Wink（arXiv:2602.17037）
RETRY_STRATEGIES = {
    "syntax": {
        "sequence": ["generator", "verifier"],
        "hint": "只修 {error_file}:{error_line} 的语法错误，修改≤5行，不触碰其他函数",
        "search": False,
    },
    "assertion": {
        "sequence": ["generator", "verifier"],
        "hint": "测试期望：{error_message}。只改1个函数使断言通过，修改≤10行",
        "search": False,
    },
    "import": {
        "sequence": ["import_fixer", "verifier"],
        "hint": "",
        "search": True,
    },
    "patch_apply": {
        "sequence": ["locator", "generator", "verifier"],
        "hint": "必须先read_file读取文件最新内容，禁止使用缓存的original",
        "search": False,
    },
    "runtime": {
        "sequence": ["debugger", "generator", "verifier"],
        "hint": "",
        "search": False,
    },
    "unknown": {
        "sequence": ["generator", "verifier"],
        "hint": "只修改1个函数，修改≤15行，不触碰报错位置±20行外的代码",
        "search": False,
        "scope_narrow": True,
    },
}


class PipelineOrchestrator:
    """Deterministic expert pipeline orchestrator."""

    MAX_RETRIES = 3  # 默认值，被 _get_max_retries() 覆盖
    _RETRY_BY_DIFFICULTY = {"easy": 2, "hard": 4}  # 动态重试预算

    def __init__(
        self,
        locator: LocatorExpert,
        generator: GeneratorExpert,
        verifier: VerifierExpert,
        search_augmentor: SearchAugmentorExpert,
        office_handler: OfficeHandlerExpert,
        tool_executor: ToolExecutor,
        memory: KaiwuMemory,
        registry: Optional[ExpertRegistry] = None,
        trajectory_collector: Optional[TrajectoryCollector] = None,
        ab_tester: Optional[ABTester] = None,
        chat_expert: Optional[ChatExpert] = None,
        debug_subagent: Optional["DebugSubagent"] = None,
        vision_expert: Optional["VisionExpert"] = None,
        bus: Optional[EventBus] = None,
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
        self._strategy_stats = StrategyStats()
        self._user_patterns = UserPatternMemory()
        self._telemetry = TelemetryClient()
        self._audit = AuditLogger()
        # 模型能力检测（从LLM后端取模型名，失败默认MEDIUM）
        try:
            model_name = getattr(self.generator.llm, 'ollama_model', '') or ''
            ollama_url = getattr(self.generator.llm, 'ollama_url', 'http://localhost:11434')
            self._model_tier = detect_model_tier(model_name, ollama_url)
            self._model_strategy = STRATEGIES[self._model_tier]
        except Exception:
            self._model_tier = ModelTier.MEDIUM
            self._model_strategy = STRATEGIES[ModelTier.MEDIUM]
        self.bus = bus or EventBus()
        self._wink = WinkMonitor()
        self._cognitive_gate = CognitiveGate()
        self._search_subagent = SearchSubagent(locator, tool_executor)
        self._manifest = UpstreamManifest()
        # MoE新增：确定性基础设施
        self._gap_detector = GapDetector()
        self._state_tracker = ExecutionStateTracker()
        self._env_prober = EnvProber()

    def run(
        self,
        user_input: str,
        gate_result: dict,
        project_root: str,
        on_status: "Optional[Callable[[str, str], None]]" = None,
        no_search: bool = False,
        skip_checkpoint: bool = False,
        pre_search_results: str = "",
        image_paths: Optional[list[str]] = None,
    ) -> dict:
        """
        Execute the expert pipeline.
        on_status: optional callback(stage: str, detail: str) for CLI progress display.
        Returns {"success": bool, "context": TaskContext, "error": str|None, "elapsed": float}.
        """
        start_time = time.time()
        self._audit.start()

        # 任务级超时看门狗
        TASK_TIMEOUT_S = 300  # 单任务最长5分钟
        _watchdog_triggered = threading.Event()

        def _watchdog_timer():
            _watchdog_triggered.set()

        _watchdog = threading.Timer(TASK_TIMEOUT_S, _watchdog_timer)
        _watchdog.daemon = True
        _watchdog.start()

        # 保存 project_root 供 Gate 2 回测使用
        self._backtest_project_root = project_root

        ctx = TaskContext(
            user_input=user_input,
            project_root=project_root,
            gate_result=gate_result,
            kaiwu_memory=self.memory.load(project_root),
            expert_system_prompt=gate_result.get("system_prompt", ""),
        )

        # 模型能力等级注入ctx
        ctx.model_tier = self._model_tier.value
        try:
            ctx.effective_ctx = getattr(self.generator.llm, '_effective_ctx', 32768)
        except AttributeError:
            ctx.effective_ctx = 32768

        # 用户错误模式提示注入
        warning = self._user_patterns.get_warning_hint()
        if warning:
            ctx.kaiwu_memory = (ctx.kaiwu_memory + "\n\n" + warning).strip()

        # 错误类型追踪
        ctx._errors_encountered = []

        # 每次顶层任务重置manifest
        self._manifest.clear()

        # 处理图片路径
        if image_paths:
            ctx.image_paths = list(image_paths)
            logger.info(f"[orchestrator] 任务包含 {len(image_paths)} 张图片")

        expert_type = gate_result.get("expert_type", "locator_repair")
        difficulty = gate_result.get("difficulty", "medium")

        # AdaptThink: 根据任务类型×难度设置think预算
        from kaiwu.core.think_config import get_think_config
        ctx.think_config = get_think_config(expert_type, difficulty)

        # 预搜索结果注入
        if pre_search_results:
            ctx.search_results = pre_search_results
            ctx.search_triggered = True
            self._emit(on_status, "search", "已预加载实时数据")

        # KWCODE.md 规则注入
        kwcode_sections = load_kwcode_md(project_root)
        if kwcode_sections:
            kwcode_rules = build_kwcode_system(expert_type, kwcode_sections)
            if kwcode_rules:
                ctx.kwcode_rules = kwcode_rules
                # 追加到 expert_system_prompt，使规则流向所有专家
                if ctx.expert_system_prompt:
                    ctx.expert_system_prompt = f"{kwcode_rules}\n\n{ctx.expert_system_prompt}"
                else:
                    ctx.expert_system_prompt = kwcode_rules

        # chat/vision类型：早期返回
        simple_result = self._handle_simple_type(ctx, expert_type, start_time, on_status)
        if simple_result is not None:
            return simple_result

        # ══════════════════════════════════════
        # Phase 0：环境探针（确定性，不走LLM）
        # ══════════════════════════════════════
        self._emit(on_status, "env_probe", "检测项目环境...")
        try:
            env_result = self._env_prober.probe_and_fix(project_root, self.tools)
            if env_result.get("installed"):
                self._emit(on_status, "env_setup",
                           f"已安装：{', '.join(env_result['installed'])}")
            if env_result.get("test_cmd"):
                ctx.confirmed_test_cmd = env_result["test_cmd"]
        except Exception as e:
            logger.debug("EnvProber failed (non-blocking): %s", e)

        # ══════════════════════════════════════
        # Phase 1：前置测试 + GapDetector（无条件，非chat/vision/office）
        # ══════════════════════════════════════
        self._state_tracker.reset()
        if expert_type not in ("chat", "vision", "office"):
            try:
                self._emit(on_status, "pre_test", "运行初始测试获取基线...")
                pre_result = self.verifier.run_tests_only(ctx)
                pre_output = pre_result.get("output", "")
                if pre_output:
                    ctx.initial_test_failure = pre_output

                # GapDetector计算初始Gap（驱动后续所有决策）
                ctx.gap = self._gap_detector.compute(pre_output, project_root)

                # 状态追踪器设置基线
                failing_tests = extract_failing_tests(pre_output)
                self._state_tracker.set_baseline(failing_tests)

                self._emit(on_status, "pre_test_done",
                           f"初始Gap：{ctx.gap.gap_type.value}（置信度{ctx.gap.confidence:.0%}）")

                # Gap驱动的expert_type覆盖（确定性优先于LLM分类）
                if ctx.gap.gap_type != GapType.UNKNOWN and ctx.gap.confidence >= 0.7:
                    gap_expert = GAP_TO_EXPERT_TYPE.get(ctx.gap.gap_type)
                    if gap_expert and gap_expert != expert_type:
                        self._emit(on_status, "gap_override",
                                   f"Gap路由覆盖：{expert_type} → {gap_expert}")
                        expert_type = gap_expert
                        gate_result = {**gate_result, "expert_type": expert_type}
                        ctx.routing_source = "gap_detector"
                    else:
                        ctx.routing_source = gate_result.get("routing_source", "keyword")
                else:
                    ctx.routing_source = gate_result.get("routing_source", "keyword")

            except Exception as e:
                logger.debug("Pre-test/GapDetector failed (non-blocking): %s", e)
                ctx.routing_source = gate_result.get("routing_source", "keyword")

        # Gate 3: AB测试
        ab_candidate_name, ab_used_new, gate_result = self._setup_ab_test(gate_result, expert_type, on_status)

        # MISSING_TOOLCHAIN快速熔断：工具链缺失时不进retry循环，直接告知用户
        if ctx.gap and ctx.gap.gap_type == GapType.MISSING_TOOLCHAIN:
            self._emit(on_status, "circuit_break",
                       f"工具链缺失：{ctx.gap.error_msg[:100]}，请手动安装后重试")
            self.bus.emit("circuit_break", {"msg": "missing_toolchain"})
            elapsed = time.time() - start_time
            _watchdog.cancel()
            checkpoint = Checkpoint(project_root)
            return self._record_failure_result(ctx, project_root, gate_result,
                                               None, False, 0,
                                               elapsed, checkpoint, False, on_status)

        # 优先使用专家注册表的自定义pipeline，否则用默认
        if gate_result.get("route_type") == "expert_registry" and "pipeline" in gate_result:
            sequence = gate_result["pipeline"]
        else:
            sequence = EXPERT_SEQUENCES.get(expert_type, ["generator", "verifier"])

        self._emit(on_status, "gate", f"任务类型：{expert_type} | 难度：{gate_result.get('difficulty', '?')} | 路由：{ctx.routing_source}")

        # ── MoE专家选择（已移除独立Expert类，统一走pipeline） ──
        moe_expert = self._select_moe_expert(ctx, expert_type)

        # 经验回放 + 预搜索 + 计划生成（跳过已被MoE专家处理的场景）
        if not moe_expert:
            self._prepare_context(ctx, gate_result, expert_type, user_input, project_root, no_search, on_status)

        # 检查点：执行前快照（多任务时跳过，避免竞态）
        checkpoint = Checkpoint(project_root)
        checkpoint_saved = False
        if not skip_checkpoint:
            checkpoint_saved = checkpoint.save()
            if not checkpoint_saved:
                self._emit(on_status, "warning", "无法创建文件快照，任务失败时需手动还原")

        # 按任务难度动态调整重试预算
        max_retries = self._get_max_retries(gate_result)

        # 低置信度：减少重试预算（不值得多次尝试）
        confidence = gate_result.get("confidence", 1.0)
        if confidence < 0.6 and expert_type not in ("chat", "office", "vision"):
            max_retries = min(max_retries, 2)
            self._emit(on_status, "low_confidence",
                       f"任务分类置信度较低({confidence:.0%})，减少重试次数")

        # CognitiveGate 重置
        self._cognitive_gate.reset()

        while ctx.retry_count < max_retries:
            # 看门狗检查：超时则中止
            if _watchdog_triggered.is_set():
                self._emit(on_status, "watchdog", f"任务超时({TASK_TIMEOUT_S}s)，强制终止")
                self.bus.emit("circuit_break", {"msg": f"任务超时({TASK_TIMEOUT_S}s)"})
                break

            success = self._run_sequence(sequence, ctx, on_status)

            # 通知 locator 任务结果（图统计 + 增量更新）
            self._notify_locator(ctx, success)

            if success:
                elapsed = time.time() - start_time
                result = self._record_success(ctx, project_root, gate_result,
                                              ab_candidate_name, ab_used_new, elapsed,
                                              checkpoint, on_status)
                if result is not None:
                    return result
                # Reviewer审查不通过 → 当作失败，继续重试
                self._emit(on_status, "review_retry", "审查不通过，重试修改...")
                # Reset for retry（保留locator_output，只重新生成）
                ctx.generator_output = None
                ctx.verifier_output = None

            ctx.retry_count += 1
            error_detail = ""
            if ctx.verifier_output:
                error_detail = ctx.verifier_output.get("error_detail", "")

            # 保存失败信息用于重试策略
            ctx.previous_failure = error_detail

            # CognitiveGate: 检测边际收益递减
            if ctx.generator_output:
                self._cognitive_gate.record(ctx.generator_output.get("patches", []))
            cg_stop, cg_reason = self._cognitive_gate.should_stop()
            if cg_stop:
                self._emit(on_status, "circuit_break", cg_reason)
                self.bus.emit("circuit_break", {"msg": cg_reason})
                break

            # Circuit breaker: same error_type streak
            current_error_type = ""
            if ctx.verifier_output:
                current_error_type = ctx.verifier_output.get("error_type", "unknown")

            # 追踪错误类型用于飞轮统计
            if current_error_type:
                ctx._errors_encountered.append(current_error_type)

            if not hasattr(ctx, '_error_type_streak'):
                ctx._error_type_streak = {"type": "", "count": 0}

            if current_error_type and current_error_type == ctx._error_type_streak["type"]:
                ctx._error_type_streak["count"] += 1
            else:
                ctx._error_type_streak = {"type": current_error_type, "count": 1}

            # ── TraceCoder: 累积历史教训（不重置） ──
            attempt_record = {
                "attempt": ctx.retry_count,
                "error_type": current_error_type,
                "passed_tests": extract_passing_tests(error_detail) if error_detail else [],
                "failed_tests": (ctx.verifier_output or {}).get("failed_tests", []),
                "patch_files": [p.get("file", "") for p in (ctx.generator_output or {}).get("patches", [])],
                "error_message": (ctx.verifier_output or {}).get("error_message", "")[:200],
            }
            ctx.attempt_history.append(attempt_record)

            # 快速熔断：语法错误重试无效
            # syntax熔断按tier区分：SMALL立刻熔断，MEDIUM/LARGE多给一次
            _syntax_max = 1 if self._model_tier == ModelTier.SMALL else 2
            if current_error_type == "syntax" and ctx.retry_count >= _syntax_max:
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
                # import修复成功，继续重试
            # 硬熔断：同类错误连续3次
            if ctx._error_type_streak["count"] >= 3:
                self._emit(on_status, "circuit_break",
                           f"同类错误({current_error_type})连续{ctx._error_type_streak['count']}次，停止重试")
                self.bus.emit("circuit_break", {"msg": f"{current_error_type} x{ctx._error_type_streak['count']}"})
                break

            # Wink 自修复：检测偏离并注入纠正
            wink_hint = self._wink.check(ctx, self.bus)

            # 错误策略路由：按 error_type 切换重试序列
            # contract_violation走patch_apply策略（重新定位+重新生成）
            if current_error_type == "contract_violation":
                current_error_type = "patch_apply"  # Re-locate to get fresh context
            retry_strategy = RETRY_STRATEGIES.get(current_error_type, RETRY_STRATEGIES["unknown"])
            sequence = retry_strategy["sequence"]
            ctx.retry_hint = self._build_retry_hint(ctx, current_error_type)
            if wink_hint:
                ctx.retry_hint = (ctx.retry_hint + "\n" + wink_hint).strip() if ctx.retry_hint else wink_hint

            self._emit(on_status, "retry", f"第{ctx.retry_count}次尝试失败：{error_detail[:100]}")
            self.bus.emit("retry", {"count": ctx.retry_count, "error": error_detail[:100]})

            # 第2次重试前反思：让LLM分析失败原因
            if ctx.retry_count == 1 and ctx.verifier_output and ctx.generator_output:
                self._do_reflection(ctx, on_status)

            # Debug子代理：捕获运行时信息（仅测试失败时）
            if ctx.retry_count >= 1 and ctx.verifier_output:
                self._do_debug(ctx, on_status)

            # 设置重试策略：每次重试用不同方法
            ctx.retry_strategy = ctx.retry_count  # 0→1→2

            # Think escalation：只在assertion错误时升级（逻辑错误需要深度推理，其他错误想更久没用）
            if current_error_type == "assertion":
                if ctx.retry_count == 1 and not ctx.think_config.get("think"):
                    ctx.think_config = {"think": True, "budget": 2048}
                    self._emit(on_status, "think_escalate", "逻辑错误，升级到深度推理")
                elif ctx.retry_count >= 2 and ctx.think_config.get("budget", 0) < 4096:
                    ctx.think_config = {"think": True, "budget": 4096}
                    self._emit(on_status, "think_escalate", "最大推理预算")

            # 错误驱动搜索：按失败类型决定是否搜索（网络保护：异常不阻塞）
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

            # ── ExecutionStateTracker：记录测试状态变化 ──
            if ctx.verifier_output:
                test_output = ctx.verifier_output.get("error_detail", "")
                current_failing = extract_failing_tests(test_output)
                current_passing = extract_passing_tests(test_output)
                new_gap = self._gap_detector.compute(test_output, project_root)
                self._state_tracker.record(
                    attempt=ctx.retry_count,
                    current_failing=current_failing,
                    current_passing=current_passing,
                    gap_type=new_gap.gap_type.value,
                )

                # 回归检测：新引入的失败 → 回滚
                if self._state_tracker.has_regression():
                    self._emit(on_status, "regression_detected",
                               f"第{ctx.retry_count}次修改引入了回归，回滚...")
                    if checkpoint_saved:
                        checkpoint.restore()
                    # TraceCoder: 回归时携带具体信息，不是空白重试
                    regressed_tests = self._state_tracker.get_new_failures() if hasattr(self._state_tracker, 'get_new_failures') else []
                    ctx.retry_hint = (
                        f"上次修改引入了回归（新增失败：{', '.join(regressed_tests[:3]) if regressed_tests else '未知'}）。\n"
                        f"请保留已通过测试的修复，只修改导致回归的部分。"
                    )

                # 更新gap驱动下一轮
                ctx.gap = new_gap

            # env_changed处理（EnvProber安装依赖后返回）
            if ctx.generator_output and ctx.generator_output.get("env_changed"):
                ctx.gap = self._recompute_gap(ctx, project_root)
                self._emit(on_status, "env_changed", "环境已变化，重新分析...")

            # Reset expert outputs for retry (RED-3: fresh context each attempt)
            ctx.locator_output = None
            ctx.generator_output = None
            ctx.verifier_output = None
            ctx.relevant_code_snippets = {}
            # 清除临时调试信息，防止context污染
            ctx.debug_info = ""

        _watchdog.cancel()  # Clean up watchdog timer
        elapsed = time.time() - start_time

        return self._record_failure_result(ctx, project_root, gate_result,
                                           ab_candidate_name, ab_used_new, max_retries,
                                           elapsed, checkpoint, checkpoint_saved, on_status)

    def _handle_simple_type(self, ctx: TaskContext, expert_type: str, start_time: float, on_status) -> Optional[dict]:
        """Handle chat and vision early returns. Returns result dict or None to continue."""
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

        return None

    def _setup_ab_test(self, gate_result: dict, expert_type: str, on_status) -> tuple:
        """Setup AB test. Returns (ab_candidate_name, ab_used_new, gate_result)."""
        ab_candidate_name = None
        ab_used_new = False
        if self.ab_tester and expert_type != "chat":
            candidate_def = self.ab_tester.should_use_candidate(expert_type)
            if candidate_def:
                ab_candidate_name = candidate_def["name"]
                ab_used_new = True
                # 覆盖gate_result使用候选专家流水线
                gate_result = {
                    **gate_result,
                    "expert_name": ab_candidate_name,
                    "route_type": "expert_registry",
                    "pipeline": candidate_def.get("pipeline", []),
                    "system_prompt": candidate_def.get("system_prompt", ""),
                }
                self._emit(on_status, "ab_test", f"AB测试：使用候选专家 {ab_candidate_name}")
            else:
                # 检查是否有候选专家在AB测试中（基线对照）
                for name, info in self.ab_tester._candidates.items():
                    if (info["status"] == "ab_testing"
                            and info["expert_def"].get("type") == expert_type
                            and len(info["ab_results"]) < 10):
                        ab_candidate_name = name
                        ab_used_new = False
                        self._emit(on_status, "ab_test", f"AB测试：基线对照（候选 {name}）")
                        break
        return (ab_candidate_name, ab_used_new, gate_result)

    def _prepare_context(self, ctx: TaskContext, gate_result: dict, expert_type: str,
                         user_input: str, project_root: str, no_search: bool, on_status) -> None:
        """Experience replay + pre-search + plan generation."""
        # Experience Replay: find similar successful trajectories
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

        # Plan 自动触发：hard 任务自动生成计划（不打断用户）
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

    def _record_success(self, ctx: TaskContext, project_root: str, gate_result: dict,
                        ab_candidate_name, ab_used_new: bool, elapsed: float,
                        checkpoint, on_status) -> dict:
        """Record success: memory, registry, trajectory, AB, value, milestone, reflection."""
        # Reviewer: 需求对齐审查 — 测试全部通过或无测试结果时跳过（无证据时Reviewer会幻觉）
        v = ctx.verifier_output or {}
        tests_passed = v.get("tests_passed", 0)
        tests_total = v.get("tests_total", 0)
        skip_review = (tests_total > 0 and tests_passed == tests_total) or tests_total == 0

        if not skip_review:
            # 测试没全通过才需要Reviewer审查
            review_result = self._do_review(ctx, on_status)
            if review_result and not review_result.get("aligned") and review_result.get("confidence", 0) >= 0.7:
                gap = review_result.get("gap", "")
                self._emit(on_status, "review_reject", f"审查不通过：{gap}")
                ctx.retry_hint = f"改错了：{gap}"
                ctx.locator_output = None  # 强制重新定位修复
                return None  # 返回None信号给retry loop

        checkpoint.discard()  # 审查通过才清理快照

        # 成功时保存记忆（含耗时，用于专家/模式追踪）
        self.memory.save(project_root, ctx, elapsed=elapsed)
        # 更新专家注册表统计
        expert_name = gate_result.get("expert_name")
        if expert_name and self.registry:
            self.registry.update_stats(expert_name, success=True, latency=elapsed)
        # 飞轮：记录轨迹+检测模式（非阻塞）
        self._record_trajectory(ctx, True, elapsed, on_status)
        # 记录AB测试结果
        self._record_ab_result(ab_candidate_name, ab_used_new, True, elapsed, on_status)
        # 价值追踪（本地SQLite）
        self._record_value(project_root, gate_result, True, elapsed, ctx)
        # 里程碑检查
        self._check_milestone(on_status)
        # Reflexion持久化：成功时也记录注意事项
        self._persist_reflection(project_root, ctx, gate_result, success=True)
        # 飞轮：策略统计 + 用户模式 + 遥测
        self._record_flywheel(ctx, gate_result, True)
        # 审计日志
        self._audit.write(ctx, elapsed, True, getattr(self, '_model_name', 'unknown'))
        return {
            "success": True,
            "context": ctx,
            "error": None,
            "elapsed": elapsed,
        }

    def _record_failure_result(self, ctx: TaskContext, project_root: str, gate_result: dict,
                               ab_candidate_name, ab_used_new: bool, max_retries: int,
                               elapsed: float, checkpoint, checkpoint_saved: bool,
                               on_status) -> dict:
        """Record failure: checkpoint restore, memory, registry, trajectory, AB, value, reflection."""
        # Checkpoint: restore on failure
        if checkpoint_saved:
            restored = checkpoint.restore()
            if restored:
                self._emit(on_status, "checkpoint", "已还原到任务执行前的状态")
            else:
                self._emit(on_status, "warning", "还原失败，请手动检查文件")
        # 降级建议
        self._suggest_downgrade(ctx, on_status)

        # 记录失败到模式记忆
        self.memory.save_failure(project_root, ctx, elapsed=elapsed)
        # 失败时更新专家统计
        expert_name = gate_result.get("expert_name")
        if expert_name and self.registry:
            self.registry.update_stats(expert_name, success=False, latency=elapsed)
        # 飞轮：记录失败轨迹
        self._record_trajectory(ctx, False, elapsed, on_status)
        # 记录AB测试失败
        self._record_ab_result(ab_candidate_name, ab_used_new, False, elapsed, on_status)
        # P2: Value tracking (local SQLite)
        self._record_value(project_root, gate_result, False, elapsed, ctx)
        # Reflexion持久化：失败时记录根因
        self._persist_reflection(project_root, ctx, gate_result, success=False)
        # 飞轮：策略统计 + 用户模式 + 遥测
        self._record_flywheel(ctx, gate_result, False)
        # 审计日志
        self._audit.write(ctx, elapsed, False, getattr(self, '_model_name', 'unknown'))
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
                self._emit(on_status, "locator", "定位中（隔离搜索）...")
                # 使用SearchSubagent：隔离context，并行读取
                search_result = self._search_subagent.search(ctx, self._manifest)
                if not search_result or not search_result.get("relevant_files"):
                    self._emit(on_status, "locator_fail", "定位失败")
                    return False
                # 将干净结果传给ctx（Generator只看到这些）
                ctx.locator_output = {
                    "relevant_files": search_result["relevant_files"],
                    "relevant_functions": search_result["relevant_functions"],
                    "edit_locations": search_result["edit_locations"],
                    "method": search_result["method"],
                }
                ctx.relevant_code_snippets = search_result["code_snippets"]
                # 注入跨文件契约给Generator
                if search_result.get("upstream_constraints"):
                    ctx.upstream_constraints = search_result["upstream_constraints"]
                files = search_result["relevant_files"]
                funcs = search_result["relevant_functions"]
                func_str = ', '.join(funcs[:3]) if funcs else "（文件级修改）"
                self._emit(on_status, "locator_done", f"文件：{', '.join(files[:3])} | 函数：{func_str}")

            elif step == "generator":
                self._emit(on_status, "generator", "生成patch...")
                result = self.generator.run(ctx)
                if not result:
                    self._emit(on_status, "generator_fail", "生成失败")
                    return False
                n_patches = len(result.get("patches", []))
                self._emit(on_status, "generator_done", f"生成{n_patches}个patch")
                # 用新patch更新manifest（跨文件追踪）
                self._manifest.update(result.get("patches", []))

            elif step == "verifier":
                self._emit(on_status, "verifier", "验证中...")
                # 运行测试前做跨文件一致性检查
                contract_violations = self._check_contracts(ctx)
                if contract_violations:
                    detail = "; ".join(contract_violations[:3])
                    self._emit(on_status, "contract_violation", f"跨文件契约冲突：{detail[:100]}")
                    ctx.verifier_output = {
                        "passed": False,
                        "syntax_ok": True,
                        "tests_passed": 0,
                        "tests_total": 0,
                        "error_detail": f"Contract violations: {detail}",
                        "error_type": "contract_violation",
                        "error_file": "",
                        "error_line": 0,
                        "error_message": detail[:200],
                        "failed_tests": [],
                    }
                    return False
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

    def _check_contracts(self, ctx: TaskContext) -> list[str]:
        """Check cross-file contract consistency using UpstreamManifest. Zero LLM."""
        if not ctx.generator_output:
            return []
        patches = ctx.generator_output.get("patches", [])
        violations = []
        for patch in patches:
            file_path = patch.get("file", "")
            modified = patch.get("modified", "")
            if file_path and modified:
                v = self._manifest.check_consistency(file_path, modified)
                violations.extend(v)
        return violations

    def _record_trajectory(self, ctx: TaskContext, success: bool, elapsed: float, on_status):
        """Record trajectory and run pattern detection (non-blocking, never raises)."""
        if not self.trajectory_collector:
            return
        try:
            model = getattr(self, '_model_name', 'unknown')
            self.trajectory_collector.record(ctx, success, elapsed, model)
            # 成功时检查飞轮候选
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
            # 自动毕业在record_ab_result内处理（总数>=10时）
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

    def _emit(self, callback, stage: str, detail: str):
        """Emit status update if callback provided. Also logs to audit."""
        if callback:
            callback(stage, detail)
        logger.info("[%s] %s", stage, detail)
        self._audit.log(stage, detail)

    def _get_max_retries(self, gate_result: dict) -> int:
        """Dynamic retry budget based on task difficulty and model strategy."""
        difficulty = gate_result.get("difficulty", "easy")
        base = self._RETRY_BY_DIFFICULTY.get(difficulty, self.MAX_RETRIES)
        # 模型策略可以覆盖（小模型限制更严）
        strategy_max = self._model_strategy.max_retries
        return min(base, strategy_max)

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

    def _record_flywheel(self, ctx: TaskContext, gate_result: dict, success: bool):
        """记录策略统计 + 用户错误模式 + 匿名遥测（全部非阻塞）。"""
        errors = getattr(ctx, '_errors_encountered', [])
        error_type = errors[-1] if errors else "unknown"
        try:
            sequence = EXPERT_SEQUENCES.get(
                gate_result.get("expert_type", ""), ["generator", "verifier"]
            )
            self._strategy_stats.record(
                error_type=error_type, sequence=sequence,
                success=success, retries_used=ctx.retry_count,
            )
        except Exception as e:
            logger.debug("Strategy stats failed (non-blocking): %s", e)
        try:
            self._user_patterns.record_task(errors, success)
        except Exception as e:
            logger.debug("User patterns failed (non-blocking): %s", e)
        try:
            self._telemetry.report(
                error_type=error_type, retry_count=ctx.retry_count,
                success=success, model=getattr(self, '_model_name', 'unknown'),
            )
        except Exception as e:
            logger.debug("Telemetry failed (non-blocking): %s", e)

    def _build_retry_hint(self, ctx: TaskContext, error_type: str) -> str:
        """按错误类型生成重试提示，注入 Generator prompt。携带上次生成的代码。"""
        strategy = RETRY_STRATEGIES.get(error_type, RETRY_STRATEGIES["unknown"])
        template = strategy.get("hint", "")
        if not template:
            hint = ""
        else:
            v = ctx.verifier_output or {}
            try:
                hint = template.format(
                    error_file=v.get("error_file", ""),
                    error_line=v.get("error_line", 0),
                    error_message=v.get("error_message", ""),
                )
            except (KeyError, ValueError):
                hint = template

        # 携带上次生成的代码，让LLM看到自己的错误
        last_code = ""
        if ctx.generator_output:
            patches = ctx.generator_output.get("patches", [])
            if patches:
                last_code = patches[0].get("modified", "") or patches[0].get("content", "")
                last_code = last_code[:300]

        if last_code:
            hint += f"\n\n上次生成的代码（有问题）：\n{last_code}\n\n请不要重复同样的错误。"

        # 携带具体失败的测试名，让LLM精确定位
        failed_tests = (ctx.verifier_output or {}).get("failed_tests", [])
        if failed_tests:
            hint += "\n\n仍然失败的测试：\n" + "\n".join(f"  - {t}" for t in failed_tests[:5])

        # TraceCoder: 携带历史教训摘要（20+20=40效果）
        if ctx.attempt_history:
            lessons = []
            for rec in ctx.attempt_history[-3:]:  # 最近3次
                passed = rec.get("passed_tests", [])
                failed = rec.get("failed_tests", [])
                err = rec.get("error_message", "")
                if passed or failed or err:
                    lessons.append(
                        f"第{rec['attempt']}次：通过{len(passed)}个/失败{len(failed)}个"
                        f"{' | 错误：' + err[:80] if err else ''}"
                    )
            if lessons:
                hint += "\n\n## 历史尝试记录（避免重复错误）\n" + "\n".join(lessons)

        return hint

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

    def _select_moe_expert(self, ctx: TaskContext, expert_type: str):
        """
        MoE专家选择：已移除独立Expert类。
        依赖安装由EnvProber在Phase0处理，存根实现由Generator通过scope处理。
        始终返回None，走默认pipeline。
        """
        return None

    def _recompute_gap(self, ctx: TaskContext, project_root: str) -> Gap:
        """重新运行测试并计算当前gap，用于环境变化后更新状态。"""
        try:
            test_result = self.verifier.run_tests_only(ctx)
            return self._gap_detector.compute(
                test_result.get("output", ""),
                project_root
            )
        except Exception:
            return ctx.gap or Gap(GapType.UNKNOWN, 0.3, [], [], "", "")
