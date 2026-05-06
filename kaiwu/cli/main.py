"""
KwCode CLI entry point.
- kwcode              → 进入交互式 REPL
- kwcode "修复bug"    → 单次执行
- kwcode init         → 初始化 KAIWU.md
- kwcode memory       → 查看项目记忆
"""

import logging
import os
import sys
import time
import warnings

# Windows GBK console encoding fix
if sys.platform == "win32":
    import io
    try:
        if hasattr(sys.stdout, "buffer"):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "buffer"):
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass  # IDE/pipe environments may not support rewrapping

# ── Silence all warnings and logger noise by default ──
warnings.filterwarnings("ignore")
from pathlib import Path
_log_dir = Path.home() / ".kwcode"
_log_dir.mkdir(parents=True, exist_ok=True)
_file_handler = logging.FileHandler(_log_dir / "kwcode.log", encoding="utf-8")
_file_handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s: %(message)s"))
logging.getLogger("kaiwu").addHandler(_file_handler)
logging.getLogger("kaiwu").propagate = False
logging.getLogger("kaiwu").setLevel(logging.DEBUG)

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

app = typer.Typer(
    name="kwcode",
    help="KwCode - 本地模型 coding agent",
    add_completion=False,
    no_args_is_help=False,
    invoke_without_command=True,
)
expert_app = typer.Typer(name="expert", help="专家管理")
app.add_typer(expert_app)
console = Console()

# ── Status display ────────────────────────────────────────────

# EventBus event icons (追加式渲染，替代单行spinner)
EVENT_ICONS = {
    "expert_start":    ("●", "blue"),
    "reading_file":    ("  📄", "dim"),
    "file_written":    ("  ✓", "green"),
    "applying_patch":  ("  →", "yellow"),
    "patch_result":    ("  ✓", "green"),
    "generator_patch": ("  →", "yellow"),
    "test_pass":       ("  ✓", "green"),
    "test_fail":       ("  ✗", "red"),
    "retry":           ("🔄", "yellow"),
    "circuit_break":   ("⛔", "red"),
    "scope_narrow":    ("🎯", "cyan"),
    "search_start":    ("🌐", "blue"),
    "search_solution": ("💡", "cyan"),
    "plan_generated":  ("📋", "blue"),
    "pre_compact":     ("📦", "dim"),
    "wink_intervene":  ("🔧", "yellow"),
}

# 阶段级事件（换行显示）
_PHASE_EVENTS = {"expert_start", "retry", "circuit_break", "plan_generated", "wink_intervene"}


def _eventbus_cli_handler(event: str, payload: dict):
    """EventBus 全局 CLI handler：追加式渲染事件到终端。"""
    icon_info = EVENT_ICONS.get(event)
    if not icon_info:
        return
    icon, color = icon_info
    detail = payload.get("path") or payload.get("msg") or payload.get("cmd", "")
    if not detail:
        return
    if event in _PHASE_EVENTS:
        console.print()
        console.print(f"[bold {color}]{icon} {detail}[/bold {color}]")
    else:
        console.print(f"[{color}]{icon} {detail}[/{color}]")


# Spinner stage mapping (internal stage → user-friendly description)
_SPINNER_STAGES = {
    "gate": "分析任务...",
    "locator": "定位代码...",
    "locator_done": None,  # silent
    "generator": "生成修改...",
    "generator_done": None,
    "verifier": "验证结果...",
    "verifier_done": None,
    "search": "搜索增强中...",
    "search_done": None,
    "chat": "思考中...",
    "vision": "分析图片...",
    "reflection": "分析失败原因...",
    "checkpoint": None,
    "warning": None,
    "suggest": None,
    "retry": None,
}

# Verbose mode: old-style text output (only with --verbose)
def _verbose_callback(stage: str, detail: str):
    """Verbose status callback — only used with --verbose flag."""
    colors = {
        "gate": "cyan", "locator": "blue", "locator_done": "green",
        "generator": "blue", "generator_done": "green",
        "verifier": "blue", "verifier_done": "green",
        "search": "magenta", "search_done": "magenta",
    }
    if "fail" in stage or "retry" in stage:
        console.print(f"  [yellow]> {detail}[/yellow]")
    elif "done" in stage:
        console.print(f"  [green]> {detail}[/green]")
    else:
        color = colors.get(stage, "dim")
        console.print(f"  [{color}]> {detail}[/{color}]")


# ── Pipeline builder ──────────────────────────────────────────

def _build_pipeline(model_path, ollama_url, ollama_model, project_root, verbose):
    """Construct the full pipeline. Returns (gate, orchestrator, memory, registry)."""
    from kaiwu.llm.llama_backend import LLMBackend
    from kaiwu.core.gate import Gate
    from kaiwu.core.orchestrator import PipelineOrchestrator
    from kaiwu.core.network import detect_network
    from kaiwu.experts.locator import LocatorExpert
    from kaiwu.experts.generator import GeneratorExpert
    from kaiwu.experts.verifier import VerifierExpert
    from kaiwu.experts.search_augmentor import SearchAugmentorExpert
    from kaiwu.experts.office_handler import OfficeHandlerExpert
    from kaiwu.tools.executor import ToolExecutor
    from kaiwu.memory.kaiwu_md import KaiwuMemory
    from kaiwu.registry import ExpertRegistry
    from kaiwu.flywheel.trajectory_collector import TrajectoryCollector
    from kaiwu.flywheel.ab_tester import ABTester

    # 网络探测（首次调用，缓存结果）
    net = detect_network()
    if net["china"]:
        proxy_hint = f"代理: {net['proxy']}" if net["proxy"] else "配置代理可加速: export KAIWU_PROXY=http://..."
        console.print(f"  [yellow][网络] 国内网络。{proxy_hint}[/yellow]")

    # SearXNG预检测（不自动拉起Docker，静默降级）
    from kaiwu.search.duckduckgo import _searxng_available, _get_searxng_url, _is_search_enabled
    import kaiwu.search.duckduckgo as _search_mod
    if not _is_search_enabled():
        console.print(f"  [dim][搜索] 已禁用(search_enabled=false)[/dim]")
    elif _search_mod._searxng_ok is None:
        searxng_url = _get_searxng_url()
        if _searxng_available(searxng_url):
            _search_mod._searxng_ok = True
            console.print(f"  [green][搜索] SearXNG 就绪[/green]")
        else:
            _search_mod._searxng_ok = False
            if _search_mod.HAS_DDGS:
                console.print(f"  [dim][搜索] SearXNG 不可用，使用 DuckDuckGo[/dim]")
            else:
                console.print(f"  [dim][搜索] 无可用搜索引擎，搜索增强已禁用[/dim]")

    # Load API key from config
    from kaiwu.cli.onboarding import load_config as _load_cfg
    _cfg = _load_cfg().get("default", {})
    _api_key = _cfg.get("api_key", "")

    llm = LLMBackend(
        model_path=model_path,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        verbose=verbose,
        api_key=_api_key,
    )
    tools = ToolExecutor(project_root=project_root)
    memory = KaiwuMemory()

    registry = ExpertRegistry()
    registry.load_builtin()
    registry.load_user()

    gate = Gate(llm=llm, registry=registry)

    locator = LocatorExpert(llm=llm, tool_executor=tools)
    generator = GeneratorExpert(llm=llm, tool_executor=tools)
    verifier = VerifierExpert(llm=llm, tool_executor=tools)
    search = SearchAugmentorExpert(llm=llm)
    office = OfficeHandlerExpert(llm=llm, tool_executor=tools)

    from kaiwu.experts.chat_expert import ChatExpert
    chat_expert = ChatExpert(llm=llm, search_augmentor=search)

    # Vision Expert (多模态图片处理)
    from kaiwu.experts.vision_expert import VisionExpert
    vision_expert = VisionExpert(llm=llm, tool_executor=tools)

    # Debug Subagent (问题1修复：实例化并注入)
    from kaiwu.experts.debug_subagent import DebugSubagent
    debug_subagent = DebugSubagent(llm, tools)

    trajectory_collector = TrajectoryCollector()

    # ABTester needs orchestrator reference for gate 2 backtest;
    # we create it first with orchestrator=None, then set it after.
    ab_tester = ABTester(
        registry=registry,
        collector=trajectory_collector,
        orchestrator=None,
    )

    orchestrator = PipelineOrchestrator(
        locator=locator, generator=generator, verifier=verifier,
        search_augmentor=search, office_handler=office,
        tool_executor=tools, memory=memory, registry=registry,
        trajectory_collector=trajectory_collector,
        ab_tester=ab_tester,
        chat_expert=chat_expert,
        debug_subagent=debug_subagent,
        vision_expert=vision_expert,
    )

    # Wire EventBus CLI handler
    orchestrator.bus.on("*", _eventbus_cli_handler)

    # Wire circular reference: ABTester needs orchestrator for backtest
    ab_tester.orchestrator = orchestrator

    return gate, orchestrator, memory, registry


# ── Single task execution ─────────────────────────────────────

def _run_task(task, gate, orchestrator, memory, project_root, verbose, plan=False, no_search=False, image_paths=None):
    """Execute a single task through the pipeline. Returns success bool."""
    from kaiwu.core.orchestrator import EXPERT_SEQUENCES
    from rich.progress import Progress, SpinnerColumn, TextColumn

    # Image markers are for Gate classification only; keep the original task text
    # for the expert prompt so paths do not pollute image analysis/codegen intent.
    if image_paths:
        logger.info(f"[main] 任务包含 {len(image_paths)} 张图片")
        image_context = "\n".join([f"[图片: {img}]" for img in image_paths])
        task_with_images = f"{task}\n\n{image_context}"
    else:
        task_with_images = task

    # Gate (with spinner)
    with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                  transient=True, console=console) as progress:
        spin = progress.add_task("分析任务...", total=None)
        try:
            gate_result = gate.classify(task_with_images, memory_context=memory.load(project_root))
        except Exception as e:
            progress.stop()
            console.print(f"\n  [red]❌ 模型调用失败[/red]")
            console.print(f"  [yellow]错误详情：[/yellow]{e}")
            console.print("\n  [cyan]💡 可能的解决方案：[/cyan]")
            console.print("    1. 检查模型是否正常运行：[dim]ollama list[/dim]")
            console.print("    2. 切换到其他模型：[dim]/model qwen3:8b[/dim]")
            console.print("    3. 检查 API 配置：[dim]/api show[/dim]")
            console.print("    4. 如果使用云端 API，检查网络连接和 API key")
            return False

    et = gate_result.get("expert_type", "chat")
    diff = gate_result.get("difficulty", "easy")

    # ── P1-A: hard任务自动拆分 ──
    _SKIP_DECOMPOSE_TYPES = {"chat", "office", "vision"}
    if diff == "hard" and et not in _SKIP_DECOMPOSE_TYPES:
        try:
            from kaiwu.core.planner import Planner
            from kaiwu.memory import pattern_md
            planner = Planner(
                locator=orchestrator.locator,
                pattern_md_module=pattern_md,
                llm=orchestrator.generator.llm,
            )
            auto_tasks = planner.auto_decompose(task, gate_result, project_root)
            if auto_tasks and len(auto_tasks) > 1:
                console.print(f"\n  [dim]检测到复杂任务，自动拆分为 {len(auto_tasks)} 个子任务[/dim]")
                for t in auto_tasks:
                    dep = f" → 依赖{t['depends_on']}" if t["depends_on"] else ""
                    console.print(f"  [dim]  {t['id']}: {t['input'][:50]}{dep}[/dim]")
                console.print()
                _handle_multi_with_tasks(auto_tasks, gate, orchestrator, project_root, console)
                return True
        except Exception as e:
            logger.warning("[main] 自动拆分异常: %s，走单任务", e)

    # Plan mode (only for high-risk tasks)
    _SKIP_PLAN_TYPES = {"chat", "office", "vision"}
    should_plan = plan and et not in _SKIP_PLAN_TYPES
    if should_plan and et == "codegen" and diff == "easy":
        should_plan = False

    if should_plan:
        from kaiwu.core.planner import Planner
        from kaiwu.memory import pattern_md
        from kaiwu.core.context import TaskContext

        plan_ctx = TaskContext(
            user_input=task,
            project_root=project_root,
            gate_result=gate_result,
        )
        planner = Planner(locator=orchestrator.locator, pattern_md_module=pattern_md)
        steps = planner.generate_plan(plan_ctx)
        planner.print_plan(steps, console)

        confirm = Prompt.ask("  确认执行?", choices=["y", "n"], default="y")
        if confirm != "y":
            console.print("  [yellow]已取消，未修改任何文件[/yellow]")
            return False

    # Execute with spinner
    _spinner_state = {"description": "执行中..."}

    def _spinner_callback(stage, detail):
        label = _SPINNER_STAGES.get(stage)
        if label:
            _spinner_state["description"] = label
        # Verbose mode: also print to console
        if verbose:
            _verbose_callback(stage, detail)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                  transient=True, console=console) as progress:
        spin = progress.add_task(_spinner_state["description"], total=None)

        # Wrap callback to update spinner
        def _status_fn(stage, detail):
            _spinner_callback(stage, detail)
            progress.update(spin, description=_spinner_state["description"])

        try:
            # P1-B: 预搜索（Gate判断needs_search时）
            pre_search = ""
            if gate_result.get("needs_search") and not no_search and et not in ("chat", "office", "vision"):
                try:
                    from kaiwu.search.query_generator import QueryGenerator
                    from kaiwu.search.duckduckgo import search as ddg_search
                    qg = QueryGenerator(llm=orchestrator.generator.llm)
                    search_query = qg.generate(task, intent="realtime")
                    if search_query:
                        results = ddg_search(search_query[0], max_results=3)
                        if results:
                            snippets = [f"[{r.get('title','')}] {r.get('snippet','')}" for r in results[:3] if r.get('snippet')]
                            pre_search = "\n".join(snippets)[:2000]
                except Exception as e:
                    logger.debug("[main] 预搜索失败: %s", e)

            result = orchestrator.run(
                user_input=task,
                gate_result=gate_result,
                project_root=project_root,
                on_status=_status_fn,
                no_search=no_search,
                pre_search_results=pre_search,
                image_paths=image_paths,
            )
            # 保存最后结果供conversation_history使用（问题7）
            orchestrator._last_result = result
        except Exception as e:
            progress.stop()
            console.print(f"\n  [red]❌ 执行异常[/red]")
            console.print(f"  [yellow]错误详情：[/yellow]{e}")
            console.print("\n  [cyan]💡 调试建议：[/cyan]")
            console.print("    1. 查看详细日志：[dim]~/.kwcode/kwcode.log[/dim]")
            console.print("    2. 尝试更简单的任务描述")
            console.print("    3. 使用 /plan 模式查看执行步骤：[dim]/plan <任务描述>[/dim]")
            console.print("    4. 如果问题持续，请在 GitHub 提交 issue")
            import traceback
            console.print(f"\n  [dim]堆栈跟踪：\n{traceback.format_exc()}[/dim]")
            return False

    # ── Output: user-friendly result summary ──
    elapsed = result.get("elapsed", 0)
    if result["success"]:
        ctx = result["context"]

        # Chat: print reply directly
        if et == "chat":
            reply = ""
            if ctx.generator_output:
                reply = ctx.generator_output.get("explanation", "")
            console.print(f"\n  {reply}" if reply else
                          "\n  你好！我是KWCode，专注于代码任务。有什么代码问题需要帮忙吗？")
            return True

        # Vision: print the analysis/code result directly. It is not a patch summary.
        if et == "vision":
            output = ""
            if ctx.generator_output:
                output = ctx.generator_output.get("explanation", "")
            console.print(f"\n  {output}" if output else "\n  [yellow]图片处理完成，但没有返回内容[/yellow]")
            return True

        # Collect file info
        files = []
        if ctx.generator_output:
            files = [p.get("file", "") for p in ctx.generator_output.get("patches", [])]
        elif ctx.locator_output:
            files = ctx.locator_output.get("relevant_files", [])

        is_codegen = et == "codegen" and not ctx.locator_output

        # Success header
        if is_codegen and files:
            for f in files:
                full = os.path.join(project_root, f) if not os.path.isabs(f) else f
                console.print(f"\n  [bold green]✓ 已生成 {full}[/bold green] ({elapsed:.1f}s)")
        else:
            files_str = ", ".join(files[:3]) if files else ""
            if files_str:
                console.print(f"\n  [bold green]✓ 完成[/bold green] ({elapsed:.1f}s)")
                for f in files[:3]:
                    console.print(f"  修改了 {f}")
            else:
                console.print(f"\n  [bold green]✓ 完成[/bold green] ({elapsed:.1f}s)")

        # Summary bullets from explanation
        if ctx.generator_output and ctx.generator_output.get("explanation"):
            explanation = ctx.generator_output["explanation"]
            # Show concise summary (first 2-3 lines)
            lines = [l.strip() for l in explanation.split("\n") if l.strip()][:3]
            for line in lines:
                console.print(f"    · {line[:60]}")

        # Test results
        if ctx.verifier_output:
            passed = ctx.verifier_output.get("tests_passed", 0)
            total = ctx.verifier_output.get("tests_total", 0)
            if total > 0:
                console.print(f"  测试通过 ({passed}/{total})")

        return True
    else:
        # Failure output
        console.print(f"\n  [bold red]✗ 失败[/bold red] ({elapsed:.1f}s)")
        ctx = result.get("context")
        error = result.get("error")
        if error:
            console.print(f"  原因：{str(error)[:200]}")
        if ctx and ctx.generator_output and ctx.generator_output.get("explanation"):
            lines = [l.strip() for l in ctx.generator_output["explanation"].split("\n") if l.strip()][:3]
            if lines and not error:
                console.print("  原因：")
            for line in lines:
                console.print(f"    {line[:80]}")
        if ctx and ctx.verifier_output:
            detail = ctx.verifier_output.get("error_detail", "")
            if detail:
                # Show first 3 lines of error
                lines = [l.strip() for l in detail.split("\n") if l.strip()][:3]
                console.print(f"  原因：")
                for line in lines:
                    console.print(f"    {line[:80]}")
        # Show downgrade suggestion if available (from orchestrator)
        return False


# ── REPL ──────────────────────────────────────────────────────

REPL_COMMANDS = {
    "/help":    "显示帮助",
    "/memory":  "查看项目记忆 (KAIWU.md)",
    "/init":    "初始化 KWCODE.md + KAIWU.md",
    "/model":   "切换模型 (用法: /model qwen3-8b)",
    "/cd":      "切换项目目录 (用法: /cd /path/to/project)",
    "/experts": "列出已注册专家",
    "/plan":    "计划模式 (用法: /plan <任务> 或 /plan 后输入任务)",
    "/multi":   "多任务模式 (用法: /multi 后按提示输入多个任务)",
    "/api":     "API配置 (用法: /api show | /api temp <url> | /api default <url>)",
    "/paste":   "从剪贴板粘贴图片",
    "/image":   "添加图片文件 (用法: /image <path>)",
    "/stats":   "查看任务统计和Gate准确率",
    "/exit":    "退出",
}


class SessionState:
    """Tracks session state for multi-turn coherence and System Reminders."""

    def __init__(self):
        self.tasks_this_session: list[dict] = []
        self.files_touched: set = set()
        self.turn_count: int = 0

    def record_task(self, user_input: str, success: bool, files: list[str], elapsed: float):
        """Record a completed task."""
        self.turn_count += 1
        self.tasks_this_session.append({
            "input": user_input[:100],
            "success": success,
            "files": files[:5],
            "elapsed": elapsed,
        })
        self.files_touched.update(files)

    def to_reminder(self) -> str:
        """Generate System Reminder text for injection into Gate memory_context."""
        if not self.tasks_this_session:
            return ""
        recent = self.tasks_this_session[-3:]
        lines = ["[本次会话已完成]"]
        for t in recent:
            status = "OK" if t["success"] else "FAIL"
            files_str = ", ".join(t["files"][:2]) if t["files"] else ""
            line = f"- [{status}] {t['input'][:40]}"
            if files_str:
                line += f" → {files_str}"
            lines.append(line)
        if self.files_touched:
            touched = list(self.files_touched)[:5]
            lines.append(f"[已修改文件] {', '.join(touched)}")
        return "\n".join(lines)


VERSION = "0.9.0"

# ── Shadow/重影大字 KAIWU ──
_KAIWU_SHADOW = [
    "  [bold white]██╗  ██╗ █████╗ ██╗██╗    ██╗██╗   ██╗[/bold white]",
    "  [bold white]██║ ██╔╝██╔══██╗██║██║    ██║██║   ██║[/bold white]",
    "  [bold white]█████╔╝ ███████║██║██║ █╗ ██║██║   ██║[/bold white]",
    "  [bold white]██╔═██╗ ██╔══██║██║██║███╗██║██║   ██║[/bold white]",
    "  [bold white]██║  ██╗██║  ██║██║╚███╔███╔╝╚██████╔╝[/bold white]",
    "  [bold white]╚═╝  ╚═╝╚═╝  ╚═╝╚═╝ ╚══╝╚══╝  ╚═════╝[/bold white]",
]


def _render_header(model: str, project_root: str, registry=None):
    """启动Header：重影大字 KAIWU + 简洁信息行。"""
    short = project_root.replace(os.path.expanduser("~"), "~")
    if len(short) > 35:
        short = "..." + short[-32:]

    expert_count = len(registry.experts) if registry and hasattr(registry, 'experts') else 0

    console.print()
    for line in _KAIWU_SHADOW:
        console.print(line)
    console.print(f"  [dim]天工开物  v{VERSION}[/dim]")
    console.print("  " + "─" * min(console.width - 4, 50))
    console.print(
        f"  [green]{model}[/green]  ·  [cyan]{short}[/cyan]  ·  "
        f"[dim]{expert_count} 专家[/dim]"
    )
    console.print()


def _repl(model_path, ollama_url, ollama_model, project_root, verbose, no_search=False):
    """Interactive REPL loop with prompt_toolkit bottom_toolbar."""
    from kaiwu.memory.kaiwu_md import KaiwuMemory
    from kaiwu.core.sysinfo import get_sysinfo, VRAMWatcher
    from kaiwu.core.context_pruner import ContextPruner
    from kaiwu.cli.status_bar import StatusBar, TokPerSecEstimator
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.completion import Completer, Completion

    gate, orchestrator, memory, registry = _build_pipeline(
        model_path=model_path,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        project_root=project_root,
        verbose=verbose,
    )

    # P2: Model capability detection
    from kaiwu.core.model_capability import detect_model_tier, get_strategy, tier_display_name
    model_tier = detect_model_tier(ollama_model, ollama_url)
    model_strategy = get_strategy(model_tier)
    orchestrator._model_name = ollama_model

    # P2: Flywheel notifier
    from kaiwu.notification.flywheel_notifier import FlywheelNotifier
    notifier = FlywheelNotifier()

    # Hardware info (once at startup)
    sysinfo = get_sysinfo()

    # Render header
    _render_header(ollama_model, project_root, registry)

    # P2: Show model tier info
    tier_name = tier_display_name(model_tier)
    if model_tier.value == "small":
        console.print(
            f"  [yellow][{tier_name}][/yellow] "
            f"已启用计划确认 · 任务范围≤{model_strategy.max_files_per_task}文件 · "
            f"第{model_strategy.search_trigger_after}次失败触发搜索"
        )
    elif model_tier.value == "large":
        console.print(f"  [green][{tier_name}][/green] 允许更大范围任务")

    # P2: Weekly stats hint
    _maybe_show_weekly_stats(console)

    # Init status bar
    status = StatusBar()
    status.model = ollama_model
    status.ctx_max = 8192
    status.vram_used = sysinfo.vram_used_gb
    status.vram_total = sysinfo.vram_total_gb
    status.ram_used = sysinfo.ram_used_gb
    status.ram_total = sysinfo.ram_total_gb

    tps_estimator = TokPerSecEstimator()
    pruner = ContextPruner(max_tokens=status.ctx_max)
    conversation_history: list[dict] = []
    session_state = SessionState()

    # Background VRAM watcher
    vram_watcher = VRAMWatcher(status)
    vram_watcher.start()

    # prompt_toolkit session with bottom_toolbar
    from prompt_toolkit.styles import Style as PTStyle
    _pt_style = PTStyle.from_dict({
        'bottom-toolbar': 'bg:#1a1a1a #666666 noreverse',
    })

    def _toolbar():
        status.refresh_ram()
        width = console.width
        bar = _escape_html(status.render(width))
        return HTML(f'<style bg="#1a1a1a" fg="#666666">{bar}</style>')

    # Slash command completer — 输入/后弹出命令菜单
    class SlashCompleter(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor.lstrip()
            if not text.startswith("/"):
                return
            for cmd, desc in REPL_COMMANDS.items():
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text), display_meta=desc)

    session = PromptSession(completer=SlashCompleter(), style=_pt_style)

    plan_next = False
    task_count = 0

    while True:
        # P2-RED-2: Show pending flywheel notifications before next task
        notifier.flush(console)

        try:
            user_input = session.prompt(
                " > ",
                bottom_toolbar=_toolbar,
            ).strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n  [dim]bye[/dim]")
            break

        if not user_input:
            continue

        # ── Slash commands ──
        if user_input.startswith("/"):
            parts = user_input.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""

            if cmd in ("/exit", "/quit", "/q"):
                console.print("  [dim]bye[/dim]")
                break

            elif cmd == "/help":
                for k, v in REPL_COMMANDS.items():
                    console.print(f"  [cyan]{k:10s}[/cyan] {v}")

            elif cmd == "/memory":
                content = memory.show(project_root)
                console.print(Panel(content, title="KAIWU.md", border_style="blue"))

            elif cmd == "/init":
                # Generate KWCODE.md template
                from kaiwu.core.kwcode_md import generate_kwcode_template
                result = generate_kwcode_template(project_root)
                console.print(f"  {result}")
                # Also init KAIWU.md if needed
                result2 = memory.init(project_root)
                console.print(f"  {result2}")

            elif cmd == "/model":
                if not arg:
                    console.print(f"  当前模型: {ollama_model}")
                    console.print("  用法: /model qwen3-8b")
                else:
                    ollama_model = arg.strip()
                    console.print(f"  [green]模型切换为: {ollama_model}[/green]")
                    console.print("  [dim]重建流水线...[/dim]")
                    gate, orchestrator, memory, registry = _build_pipeline(
                        model_path=model_path,
                        ollama_url=ollama_url,
                        ollama_model=ollama_model,
                        project_root=project_root,
                        verbose=verbose,
                    )
                    status.model = ollama_model
                    # 持久化到config，下次启动自动用新模型
                    from kaiwu.cli.onboarding import load_config, _save_config
                    cfg = load_config()
                    cfg.setdefault("default", {})
                    cfg["default"]["model"] = ollama_model
                    _save_config(cfg)

            elif cmd == "/cd":
                if not arg:
                    console.print(f"  当前项目: {project_root}")
                    console.print("  用法: /cd /path/to/project")
                else:
                    new_root = os.path.abspath(arg.strip())
                    if os.path.isdir(new_root):
                        project_root = new_root
                        console.print(f"  [green]项目切换为: {project_root}[/green]")
                        # 重建 tools（project_root 变了）
                        gate, orchestrator, memory, registry = _build_pipeline(
                            model_path=model_path,
                            ollama_url=ollama_url,
                            ollama_model=ollama_model,
                            project_root=project_root,
                            verbose=verbose,
                        )
                    else:
                        console.print(f"  [red]❌ 目录不存在[/red]")
                        console.print(f"  [yellow]路径：[/yellow]{new_root}")
                        console.print("\n  [cyan]💡 提示：[/cyan]")
                        console.print("    • 检查路径拼写是否正确")
                        console.print("    • 使用绝对路径或相对路径")
                        console.print(f"    • 当前目录：[dim]{os.getcwd()}[/dim]")
                        console.print("    • 示例：[dim]/cd ~/projects/myapp[/dim]")

            elif cmd == "/experts":
                experts = registry.list_experts()
                console.print(f"  [cyan]已注册专家: {len(experts)}[/cyan]")
                for e in experts:
                    lc = e.get("lifecycle", "new")
                    perf = e.get("performance", {})
                    cnt = perf.get("task_count", 0)
                    sr = perf.get("success_rate", 0.0)
                    kws = ", ".join(e["trigger_keywords"][:4])
                    console.print(f"  [bold]{e['name']}[/bold] [{lc}] tasks={cnt} sr={sr:.0%} kw=[{kws}]")

            elif cmd == "/stats":
                from kaiwu.stats.value_tracker import ValueTracker
                tracker = ValueTracker()
                summary = tracker.get_summary(days=30)
                gate_acc = tracker.get_gate_accuracy(days=30)

                console.print("\n  [bold]任务统计（近30天）[/bold]")
                console.print(f"  总任务: {summary['total_tasks']} | 成功: {summary['succeeded_tasks']} | "
                              f"成功率: {summary['succeeded_tasks']/max(summary['total_tasks'],1)*100:.0f}%")
                console.print(f"  节省时间: ~{summary['time_saved_hours']}h | 累计: {summary['total_all_time']} 个任务")

                if gate_acc:
                    console.print("\n  [bold]Gate路由准确率[/bold]")
                    console.print("  [dim]类型          总数  成功率  平均耗时  平均重试[/dim]")
                    for g in gate_acc:
                        sr = f"{g['success_rate']*100:.0f}%"
                        console.print(
                            f"  {g['expert_type']:14s} {g['total']:4d}  {sr:>5s}  "
                            f"{g['avg_elapsed']:5.1f}s  {g['avg_retries']:.1f}次"
                        )
                else:
                    console.print("\n  [dim]暂无Gate路由数据（需要执行几个任务后才有统计）[/dim]")
                console.print()

            elif cmd == "/plan":
                if arg:
                    pending_images = list(getattr(session, '_pending_images', []))
                    if pending_images:
                        console.print(f"  [cyan]图片上下文: {len(pending_images)} 张图片[/cyan]")
                    # /plan <任务描述> → 直接以plan模式执行
                    task_count += 1
                    conversation_history.append({"role": "user", "content": arg})
                    t0 = time.perf_counter()
                    success = _run_task(
                        task=arg, gate=gate, orchestrator=orchestrator,
                        memory=memory, project_root=project_root,
                        verbose=verbose, plan=True, no_search=no_search,
                        image_paths=pending_images if pending_images else None,
                    )
                    if pending_images:
                        session._pending_images = []
                    elapsed = time.perf_counter() - t0
                    tps_estimator.record("x" * int(elapsed * 15), elapsed)
                    status.tok_per_sec = tps_estimator.value
                    conversation_history.append({"role": "assistant", "content": arg[:500]})
                    status.ctx_used = pruner.estimate_total(conversation_history)
                else:
                    plan_next = True
                    console.print("  [dim]下一个任务将先显示计划[/dim]")

            elif cmd == "/multi":
                _handle_multi_command(arg, gate, orchestrator, project_root, console)

            elif cmd == "/paste":
                from kaiwu.experts.vision_expert import save_clipboard_image
                image_path = save_clipboard_image()
                if image_path:
                    console.print(f"  [green]图片已从剪贴板保存: {image_path}[/green]")
                    console.print("  [dim]现在可以输入任务描述，图片将作为上下文[/dim]")
                    # 存储图片路径供后续任务使用
                    if not hasattr(session, '_pending_images'):
                        session._pending_images = []
                    session._pending_images.append(image_path)
                else:
                    console.print("  [yellow]剪贴板中没有图片[/yellow]")

            elif cmd == "/image":
                if not arg:
                    console.print("  [yellow]用法: /image <图片路径>[/yellow]")
                else:
                    from kaiwu.experts.vision_expert import validate_image_path
                    image_path = _resolve_image_path(arg.strip(), project_root)
                    if validate_image_path(image_path):
                        console.print(f"  [green]图片已添加: {image_path}[/green]")
                        console.print("  [dim]现在可以输入任务描述，图片将作为上下文[/dim]")
                        # 存储图片路径供后续任务使用
                        if not hasattr(session, '_pending_images'):
                            session._pending_images = []
                        session._pending_images.append(image_path)
                    else:
                        console.print(f"  [red]图片文件不存在或格式不支持: {image_path}[/red]")

            elif cmd == "/api":
                api_parts = user_input.split()
                result = _handle_api_command(api_parts, ollama_url, ollama_model)
                if result:
                    # /api temp or /api default changed the URL, rebuild pipeline
                    ollama_url = result.get("url", ollama_url)
                    gate, orchestrator, memory, registry = _build_pipeline(
                        model_path=model_path,
                        ollama_url=ollama_url,
                        ollama_model=ollama_model,
                        project_root=project_root,
                        verbose=verbose,
                    )
                    status.model = ollama_model

            else:
                console.print(f"  [yellow]未知命令: {cmd}[/yellow]  输入 /help 查看帮助")

            continue

        # ── Execute task ──
        task_count += 1

        # Track conversation for context pruning
        conversation_history.append({"role": "user", "content": user_input})

        # Check if context needs pruning before task
        if pruner.needs_pruning(conversation_history):
            conversation_history = pruner.prune(conversation_history)
            status.compress_count = pruner.compress_count
            console.print(
                f"  [dim]context已压缩（第{pruner.compress_count}次，"
                f"耗时{pruner._last_compress_ms:.1f}ms）[/dim]"
            )

        # 处理待处理的图片
        pending_images = getattr(session, '_pending_images', [])
        if pending_images:
            console.print(f"  [cyan]图片上下文: {len(pending_images)} 张图片[/cyan]")
            for img_path in pending_images:
                console.print(f"    - {img_path}")

        t0 = time.perf_counter()
        # P2: Small model forces plan mode (问题6修复：用户可通过 no_search 间接控制)
        effective_plan = plan_next or (model_strategy.force_plan_mode and not no_search)

        # Inject session reminder into memory context for Gate
        session_reminder = session_state.to_reminder()
        if session_reminder and session_state.turn_count % 5 == 0:
            # Every 5 turns, also re-inject KWCODE.md core rules (attention decay countermeasure)
            from kaiwu.core.kwcode_md import load_kwcode_md, build_kwcode_system
            kwcode_sections = load_kwcode_md(project_root)
            if kwcode_sections and "all" in kwcode_sections:
                session_reminder += f"\n\n[项目规则提醒]\n{kwcode_sections['all'][:500]}"

        success = _run_task(
            task=user_input,
            gate=gate,
            orchestrator=orchestrator,
            memory=memory,
            project_root=project_root,
            verbose=verbose,
            plan=effective_plan,
            no_search=no_search,
            image_paths=pending_images if pending_images else None,
        )
        
        # 清除已使用的图片
        if pending_images:
            session._pending_images = []
            
        elapsed = time.perf_counter() - t0
        plan_next = False  # Reset plan flag

        # Record task in session state
        task_files = []
        if success:
            try:
                last_result = getattr(orchestrator, '_last_result', None)
                if last_result and last_result.get("context"):
                    ctx = last_result["context"]
                    if ctx.generator_output:
                        task_files = [p.get("file", "") for p in ctx.generator_output.get("patches", [])]
            except Exception:
                pass
        session_state.record_task(user_input, success, task_files, elapsed)

        # Update tok/s estimator (rough: use elapsed as proxy)
        tps_estimator.record("x" * int(elapsed * 15), elapsed)  # ~15 tok/s estimate
        status.tok_per_sec = tps_estimator.value

        # Update ctx usage with actual task result (问题7修复：存真实输出而非user_input)
        assistant_content = ""
        if success:
            # 尝试从orchestrator获取真实输出
            try:
                last_result = getattr(orchestrator, '_last_result', None)
                if last_result and last_result.get("context"):
                    ctx = last_result["context"]
                    if ctx.generator_output and ctx.generator_output.get("explanation"):
                        assistant_content = ctx.generator_output["explanation"][:500]
            except Exception:
                pass
        if not assistant_content:
            assistant_content = f"[任务{'成功' if success else '失败'}] {user_input[:100]}"
        conversation_history.append({"role": "assistant", "content": assistant_content})
        status.ctx_used = pruner.estimate_total(conversation_history)
        status.model = ollama_model

    # Cleanup
    vram_watcher.stop()

    # P4: 会话连续性 — 保存SESSION.md
    try:
        from kaiwu.memory.session_md import save_session
        tasks_done = []
        for msg in conversation_history:
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                success = not content.startswith("[任务失败")
                tasks_done.append({"input": content[:50], "success": success, "files": [], "elapsed": 0})
        if tasks_done:
            save_session(project_root, tasks_done[-10:])
    except Exception:
        pass


def _escape_html(text: str) -> str:
    """Escape HTML special chars for prompt_toolkit HTML."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _resolve_image_path(path: str, project_root: str) -> str:
    """Resolve /image paths relative to the active project directory."""
    expanded = os.path.expanduser(path)
    if not os.path.isabs(expanded):
        expanded = os.path.join(project_root, expanded)
    return os.path.abspath(expanded)


def _handle_multi_with_tasks(tasks, gate, orchestrator, project_root, console):
    """复用TaskCompiler执行已解析的tasks（供auto_decompose调用）。"""
    from kaiwu.core.task_compiler import TaskCompiler
    from rich.progress import Progress, SpinnerColumn, TextColumn

    compiler = TaskCompiler(orchestrator=orchestrator, gate=gate, project_root=project_root)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                  console=console, transient=True) as progress:
        ptask = progress.add_task("执行多任务...", total=None)

        def _on_status(stage, detail):
            progress.update(ptask, description=detail[:60])

        result = compiler.compile_and_run(tasks, on_status=_on_status)

    if result["success"]:
        console.print(f"  [bold green]✓ 完成[/bold green] ({result['elapsed']:.1f}s)")
    else:
        failed = [tid for tid, r in result["results"].items() if not r["success"]]
        console.print(f"  [bold yellow]部分完成[/bold yellow] — 失败: {', '.join(failed)}")


# ── /multi command handler ───────────────────────────────────

def _handle_multi_command(arg: str, gate, orchestrator, project_root: str, console):
    """
    /multi 多任务模式。支持两种输入方式：
    1. /multi 后交互式逐条输入（空行结束）
    2. /multi task1 ; task2 ; task3（分号分隔）

    依赖关系用 -> 表示：task1 -> task2 表示 task2 依赖 task1。
    无 -> 的任务之间并行执行。
    """
    from kaiwu.core.task_compiler import TaskCompiler
    from rich.progress import Progress, SpinnerColumn, TextColumn

    tasks = []

    if arg and ";" in arg:
        # 分号分隔模式
        raw_tasks = [t.strip() for t in arg.split(";") if t.strip()]
        tasks = _parse_multi_tasks(raw_tasks)
    elif arg and "->" in arg:
        # 单行依赖链模式: task1 -> task2 -> task3
        raw_tasks = [t.strip() for t in arg.split("->") if t.strip()]
        tasks = _parse_chain_tasks(raw_tasks)
    elif arg:
        # 单个任务，直接执行（等同于普通输入）
        tasks = [{"id": "t1", "input": arg, "depends_on": []}]
    else:
        # 交互式输入
        console.print("  [dim]输入多个任务（每行一个，空行结束）[/dim]")
        console.print("  [dim]前缀 '>' 表示依赖上一个任务（串行），否则并行[/dim]")
        console.print("  [dim]示例：[/dim]")
        console.print("  [dim]  给函数add加注释[/dim]")
        console.print("  [dim]  给函数sub加注释[/dim]")
        console.print("  [dim]  >给修改后的代码写测试  (依赖前面的任务)[/dim]")
        console.print()

        raw_lines = []
        while True:
            try:
                line = input("  + ").strip()
            except (KeyboardInterrupt, EOFError):
                console.print("  [dim]取消[/dim]")
                return
            if not line:
                break
            raw_lines.append(line)

        if not raw_lines:
            console.print("  [yellow]未输入任何任务[/yellow]")
            return

        tasks = _parse_interactive_tasks(raw_lines)

    if not tasks:
        console.print("  [yellow]未解析到有效任务[/yellow]")
        return

    # 展示任务计划
    parallel_count = sum(1 for t in tasks if not t["depends_on"])
    serial_count = sum(1 for t in tasks if t["depends_on"])
    console.print(f"\n  [bold]多任务计划[/bold]：{len(tasks)} 个任务（{parallel_count} 并行 + {serial_count} 串行）")
    for t in tasks:
        dep_str = f" [dim](依赖 {', '.join(t['depends_on'])})[/dim]" if t["depends_on"] else " [green](并行)[/green]"
        console.print(f"  {t['id']}: {t['input'][:60]}{dep_str}")
    console.print()

    # 执行
    compiler = TaskCompiler(orchestrator=orchestrator, gate=gate, project_root=project_root)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        ptask = progress.add_task("执行多任务...", total=None)

        def _on_status(stage, detail):
            progress.update(ptask, description=f"{detail[:60]}")

        result = compiler.compile_and_run(tasks, on_status=_on_status)

    # 展示结果
    console.print()
    if result["success"]:
        console.print(f"  [bold green]✓ 全部完成[/bold green] ({result['elapsed']:.1f}s)")
    else:
        failed = [tid for tid, r in result["results"].items() if not r["success"]]
        passed = [tid for tid, r in result["results"].items() if r["success"]]
        console.print(f"  [bold yellow]部分完成[/bold yellow] ({result['elapsed']:.1f}s)")
        if passed:
            console.print(f"  成功: {', '.join(passed)}")
        if failed:
            console.print(f"  [red]失败: {', '.join(failed)}[/red]")

    # 逐任务摘要
    for tid, r in result["results"].items():
        ctx = r.get("context")
        if r["success"] and ctx and ctx.generator_output:
            files = [p.get("file", "") for p in ctx.generator_output.get("patches", [])]
            if files:
                console.print(f"  {tid}: 修改了 {', '.join(files[:3])}")
        elif not r["success"]:
            err = r.get("error", "")[:60]
            console.print(f"  {tid}: [red]{err}[/red]")


def _parse_multi_tasks(raw_tasks: list[str]) -> list[dict]:
    """解析分号分隔的任务列表。全部并行（无依赖）。"""
    tasks = []
    for i, task_input in enumerate(raw_tasks, 1):
        tasks.append({
            "id": f"t{i}",
            "input": task_input,
            "depends_on": [],
        })
    return tasks


def _parse_chain_tasks(raw_tasks: list[str]) -> list[dict]:
    """解析 -> 分隔的依赖链。每个任务依赖前一个。"""
    tasks = []
    for i, task_input in enumerate(raw_tasks, 1):
        deps = [f"t{i-1}"] if i > 1 else []
        tasks.append({
            "id": f"t{i}",
            "input": task_input,
            "depends_on": deps,
        })
    return tasks


def _parse_interactive_tasks(raw_lines: list[str]) -> list[dict]:
    """
    解析交互式输入的任务。
    以 '>' 开头的任务依赖前面所有无依赖的任务（串行）。
    不以 '>' 开头的任务之间并行。
    """
    tasks = []
    last_parallel_ids = []

    for i, line in enumerate(raw_lines, 1):
        tid = f"t{i}"
        if line.startswith(">"):
            # 依赖前面所有并行任务
            task_input = line[1:].strip()
            deps = list(last_parallel_ids) if last_parallel_ids else ([f"t{i-1}"] if i > 1 else [])
            tasks.append({
                "id": tid,
                "input": task_input,
                "depends_on": deps,
            })
            last_parallel_ids = [tid]  # 后续的 > 任务依赖这个
        else:
            tasks.append({
                "id": tid,
                "input": line,
                "depends_on": [],
            })
            last_parallel_ids.append(tid)

    return tasks


def _maybe_show_weekly_stats(console):
    """Show weekly stats hint at startup (once per 7 days). P2-FLEX-3: skip if <5 tasks."""
    import time as _time
    from pathlib import Path as _Path
    last_shown_path = _Path.home() / ".kwcode" / "last_stats_shown.txt"
    now = _time.time()

    if last_shown_path.exists():
        try:
            last = float(last_shown_path.read_text().strip())
            if now - last < 7 * 86400:
                return
        except (ValueError, OSError):
            pass

    try:
        from kaiwu.stats.value_tracker import ValueTracker
        tracker = ValueTracker()
        summary = tracker.get_summary(days=7)
        if summary["total_tasks"] >= 5:
            console.print(
                f"  [dim]本周：完成 {summary['total_tasks']} 个任务 · "
                f"节省约 {summary['time_saved_hours']} 小时[/dim]"
            )
            last_shown_path.parent.mkdir(parents=True, exist_ok=True)
            last_shown_path.write_text(str(now))
    except Exception:
        pass


# ── /api command handler ─────────────────────────────────────

def _handle_api_command(parts: list[str], current_url: str, current_model: str):
    """Handle /api show | /api temp <url> [key] | /api default <url> [key].
    Returns {"url": new_url} if pipeline needs rebuild, None otherwise."""
    from kaiwu.cli.onboarding import load_config, _verify_api, _save_config, CONFIG_PATH

    if len(parts) < 2 or parts[1] == "show":
        cfg = load_config().get("default", {})
        console.print(f"  Base URL : {cfg.get('base_url', current_url)}")
        console.print(f"  Model    : {cfg.get('model', current_model)}")
        key = cfg.get("api_key", "")
        console.print(f"  API Key  : {'*' * min(len(key), 8) + '...' if key else '（无）'}")
        return None

    sub = parts[1]
    if sub not in ("temp", "default"):
        console.print("  [red]❌ 未知子命令[/red]")
        console.print("\n  [cyan]用法：[/cyan]")
        console.print("    /api show              - 查看当前 API 配置")
        console.print("    /api temp <url> [key]  - 临时切换 API（本次会话）")
        console.print("    /api default <url> [key] - 永久保存 API 配置")
        console.print("\n  [cyan]示例：[/cyan]")
        console.print("    /api temp http://localhost:11434")
        console.print("    /api default https://api.deepseek.com sk-xxx")
        return None

    if len(parts) < 3:
        console.print("  [red]❌ 缺少 URL 参数[/red]")
        console.print("\n  [cyan]示例：[/cyan]")
        console.print("    /api temp http://localhost:11434")
        console.print("    /api default https://api.deepseek.com your-api-key")
        console.print("\n  [cyan]常用 API 地址：[/cyan]")
        console.print("    • Ollama 本地：http://localhost:11434")
        console.print("    • DeepSeek：https://api.deepseek.com")
        console.print("    • 硅基流动：https://api.siliconflow.cn/v1")
        return None

    new_url = parts[2].rstrip("/")
    new_key = parts[3] if len(parts) > 3 else ""

    # Verify
    ok, err = _verify_api(new_url, new_key, current_model)
    if ok:
        console.print(f"  [green]✓ 已切换到 {new_url}[/green]")
    else:
        console.print(f"  [yellow]⚠ 连接验证失败：{err}[/yellow]")

    if sub == "default":
        config = load_config()
        config.setdefault("default", {})
        config["default"]["base_url"] = new_url
        if new_key:
            config["default"]["api_key"] = new_key
        _save_config(config)
        console.print("  [dim]已写入默认配置并重建流水线[/dim]")
    else:
        console.print("  [dim]临时切换，当前窗口有效[/dim]")

    # Signal caller to rebuild pipeline with new URL
    return {"url": new_url}


# ── Typer commands ────────────────────────────────────────────

@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    task: str = typer.Argument(None, help="任务描述。不提供则进入交互模式"),
    plan: bool = typer.Option(False, "--plan", "-p", help="先输出计划，确认后执行"),
    model: str = typer.Option(None, "--model", "-m", help="Ollama模型名称 (默认 qwen3-8b)"),
    model_path: str = typer.Option(None, "--model-path", help="本地GGUF模型路径"),
    ollama_url: str = typer.Option("http://localhost:11434", "--ollama-url", help="Ollama服务地址"),
    project_dir: str = typer.Option(".", "--project", "-d", help="项目根目录"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="显示详细日志"),
    no_search: bool = typer.Option(False, "--no-search", help="禁用搜索增强"),
    do_init: bool = typer.Option(False, "--init", help="初始化KAIWU.md"),
    show_memory: bool = typer.Option(False, "--memory", help="查看项目记忆"),
):
    """KwCode - 本地模型 coding agent。无参数进入交互模式。"""

    # If a subcommand (init/memory/expert) is being invoked, skip main logic
    if ctx.invoked_subcommand is not None:
        return

    log_level = logging.DEBUG if verbose else logging.WARNING
    if verbose:
        logging.basicConfig(level=log_level, format="%(name)s: %(message)s")
        logging.getLogger("kaiwu").propagate = True

    project_root = os.path.abspath(project_dir)
    ollama_model = model or "qwen3-8b"

    # ── Subcommands ──
    if do_init:
        from kaiwu.core.kwcode_md import generate_kwcode_template
        console.print(f"  {generate_kwcode_template(project_root)}")
        from kaiwu.memory.kaiwu_md import KaiwuMemory
        console.print(KaiwuMemory().init(project_root))
        return

    if show_memory:
        from kaiwu.memory.kaiwu_md import KaiwuMemory
        mem = KaiwuMemory()
        console.print(Panel(mem.show(project_root), title="KAIWU.md", border_style="blue"))
        return

    # ── First-run onboarding (BOOT-RED-1) ──
    from kaiwu.cli.onboarding import is_first_run, run_onboarding, load_config

    config = load_config()
    if is_first_run():
        config = run_onboarding()

    # Use config values as defaults (CLI flags override)
    default_cfg = config.get("default", {})
    if not model and default_cfg.get("model"):
        ollama_model = default_cfg["model"]
    if ollama_url == "http://localhost:11434" and default_cfg.get("base_url"):
        ollama_url = default_cfg["base_url"]

    # ── No task → REPL mode ──
    if not task:
        _repl(
            model_path=model_path,
            ollama_url=ollama_url,
            ollama_model=ollama_model,
            project_root=project_root,
            verbose=verbose,
            no_search=no_search,
        )
        return

    # ── Single task mode ──
    console.print(Panel(
        f"[bold]KW-CODE v{VERSION}[/bold] | {ollama_model} | {project_root}",
        border_style="cyan",
    ))

    gate, orchestrator, memory, registry = _build_pipeline(
        model_path=model_path,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        project_root=project_root,
        verbose=verbose,
    )

    success = _run_task(task, gate, orchestrator, memory, project_root, verbose, plan, no_search)
    if not success:
        raise typer.Exit(1)


@app.command("init")
def cmd_init(
    project_dir: str = typer.Option(".", "--project", "-d", help="项目根目录"),
):
    """初始化 KWCODE.md + KAIWU.md 项目文件。"""
    project_root = os.path.abspath(project_dir)
    from kaiwu.core.kwcode_md import generate_kwcode_template
    console.print(f"  {generate_kwcode_template(project_root)}")
    from kaiwu.memory.kaiwu_md import KaiwuMemory
    console.print(KaiwuMemory().init(project_root))


@app.command("memory")
def cmd_memory(
    project_dir: str = typer.Option(".", "--project", "-d", help="项目根目录"),
    reset: bool = typer.Option(False, "--reset", help="清空项目记忆"),
):
    """查看或重置当前项目的记忆。"""
    from kaiwu.memory.kaiwu_md import KaiwuMemory
    project_root = os.path.abspath(project_dir)
    if reset:
        import shutil
        kaiwu_dir = os.path.join(project_root, ".kaiwu")
        if os.path.isdir(kaiwu_dir):
            shutil.rmtree(kaiwu_dir)
            console.print(f"  [green]已清空 {kaiwu_dir}[/green]")
        else:
            console.print(f"  [dim]没有记忆文件需要清空[/dim]")
        return
    content = KaiwuMemory().show(project_root)
    Console().print(Panel(content, title="KAIWU.md", border_style="blue"))


# ── Expert management subcommands ────────────────────────────

def _get_registry():
    """Build a standalone registry for CLI expert commands."""
    from kaiwu.registry import ExpertRegistry
    reg = ExpertRegistry()
    reg.load_builtin()
    reg.load_user()
    return reg


@expert_app.command("list")
def expert_list(
    expert_type: str = typer.Argument(None, help="按类型过滤 (builtin/custom)"),
):
    """列出已安装专家。"""
    reg = _get_registry()
    experts = reg.list_experts(expert_type=expert_type)
    if not experts:
        console.print("  没有已安装的专家。")
        return
    console.print(f"  [cyan]已安装专家: {len(experts)}[/cyan]")
    for e in experts:
        lc = e.get("lifecycle", "new")
        perf = e.get("performance", {})
        cnt = perf.get("task_count", 0)
        sr = perf.get("success_rate", 0.0)
        kws = ", ".join(e["trigger_keywords"][:4])
        console.print(f"  [bold]{e['name']}[/bold] v{e.get('version','?')} [{lc}] tasks={cnt} sr={sr:.0%} kw=[{kws}]")


@expert_app.command("info")
def expert_info(name: str = typer.Argument(..., help="专家名称")):
    """查看专家详情。"""
    import yaml as _yaml
    reg = _get_registry()
    expert = reg.get(name)
    if not expert:
        console.print(f"  [red]未找到专家: {name}[/red]")
        raise typer.Exit(1)
    data = {k: v for k, v in expert.items() if not k.startswith("_")}
    console.print(Panel(
        _yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False).rstrip(),
        title=name,
        border_style="cyan",
    ))


@expert_app.command("export")
def expert_export(
    name: str = typer.Argument(..., help="专家名称"),
    output: str = typer.Option(".", "--output", "-o", help="输出目录"),
):
    """导出专家为 .kwx 包。"""
    from kaiwu.registry.expert_packager import ExpertPackager
    reg = _get_registry()
    try:
        path = ExpertPackager.export(reg, name, output)
        console.print(f"  [green]已导出: {path}[/green]")
    except Exception as e:
        console.print(f"  [red]{e}[/red]")
        raise typer.Exit(1)


@expert_app.command("install")
def expert_install(path: str = typer.Argument(..., help=".kwx 文件路径或 URL")):
    """安装专家包 (.kwx 文件或 URL)。"""
    from kaiwu.registry.expert_packager import ExpertPackager
    reg = _get_registry()
    try:
        name = ExpertPackager.install(path, reg)
        console.print(f"  [green]已安装专家: {name}[/green]")
    except Exception as e:
        console.print(f"  [red]安装失败: {e}[/red]")
        raise typer.Exit(1)


@expert_app.command("remove")
def expert_remove(name: str = typer.Argument(..., help="专家名称")):
    """删除已安装专家。"""
    from kaiwu.registry.expert_packager import ExpertPackager
    reg = _get_registry()
    try:
        ExpertPackager.remove(name, reg)
        console.print(f"  [green]已删除专家: {name}[/green]")
    except Exception as e:
        console.print(f"  [red]{e}[/red]")
        raise typer.Exit(1)


@expert_app.command("create")
def expert_create(name: str = typer.Argument(..., help="新专家名称")):
    """创建新专家模板。"""
    from kaiwu.registry.expert_packager import ExpertPackager
    try:
        path = ExpertPackager.create_template(name)
        console.print(f"  [green]已创建模板: {path}[/green]")
        console.print("  编辑该文件以自定义你的专家。")
    except FileExistsError as e:
        console.print(f"  [yellow]{e}[/yellow]")
    except Exception as e:
        console.print(f"  [red]{e}[/red]")
        raise typer.Exit(1)


# ── Status command ───────────────────────────────────────────

@app.command("status")
def cmd_status(
    model: str = typer.Option(None, "--model", "-m", help="Ollama模型名称"),
    ollama_url: str = typer.Option("http://localhost:11434", "--ollama-url", help="Ollama服务地址"),
    project_dir: str = typer.Option(".", "--project", "-d", help="项目根目录"),
):
    """显示模型/专家/记忆状态。"""
    from kaiwu.registry import ExpertRegistry
    from kaiwu.memory.kaiwu_md import KaiwuMemory

    project_root = os.path.abspath(project_dir)
    ollama_model = model or "qwen3-8b"

    # Registry
    reg = ExpertRegistry()
    reg.load_builtin()
    reg.load_user()
    experts = reg.list_experts()
    builtin = [e for e in experts if e.get("type") == "builtin"]
    custom = [e for e in experts if e.get("type") != "builtin"]

    # Memory
    mem = KaiwuMemory()
    has_memory = os.path.isfile(os.path.join(project_root, "KAIWU.md"))

    # Ollama connectivity
    ollama_ok = False
    try:
        import httpx
        resp = httpx.get(f"{ollama_url}/api/tags", timeout=5)
        ollama_ok = resp.status_code == 200
    except Exception:
        pass

    console.print(Panel(
        f"模型: {ollama_model}  Ollama: {'[green]连接正常[/green]' if ollama_ok else '[red]无法连接[/red]'} ({ollama_url})\n"
        f"专家: {len(builtin)} builtin + {len(custom)} custom = {len(experts)} total\n"
        f"项目: {project_root}\n"
        f"记忆: {'[green]KAIWU.md 已初始化[/green]' if has_memory else '[yellow]未初始化 (kwcode init)[/yellow]'}",
        title="KwCode Status",
        border_style="cyan",
    ))


# ── Stats command ───────────────────────────────────────────

@app.command("stats")
def cmd_stats(
    days: int = typer.Option(30, help="统计天数"),
):
    """查看KWCode价值报告。"""
    from kaiwu.stats.value_tracker import ValueTracker

    tracker = ValueTracker()
    summary = tracker.get_summary(days=days)

    # P2-FLEX-3: not enough data
    if summary["total_tasks"] < 5:
        console.print(
            f"  [dim]数据积累中（已完成{summary['total_tasks']}个任务），"
            f"积累更多任务后显示报告[/dim]"
        )
        return

    console.print()
    console.print(f"  [bold]KWCode 价值报告[/bold]  过去{days}天")
    console.print("  " + "─" * 45)
    console.print(f"  完成任务        {summary['total_tasks']} 个")
    console.print(f"  成功任务        {summary['succeeded_tasks']} 个")

    if summary["time_saved_hours"] > 0:
        console.print(f"  节省时间        约 {summary['time_saved_hours']} 小时")

    if summary["top_expert_name"]:
        console.print()
        console.print(
            f"  最活跃专家      {summary['top_expert_name']}"
            f"  ·  {summary['top_expert_count']}次"
            f"  ·  成功率 {summary['top_expert_rate']*100:.0f}%"
        )

    console.print()
    console.print("  [dim]数据仅存本地，不上报任何服务器[/dim]")
    console.print()


# ── MCP serve command ────────────────────────────────────────

@app.command("serve-mcp")
def cmd_serve_mcp(
    model: str = typer.Option(None, "--model", "-m", help="Ollama模型名称"),
    model_path: str = typer.Option(None, "--model-path", help="本地GGUF模型路径"),
    ollama_url: str = typer.Option("http://localhost:11434", "--ollama-url", help="Ollama服务地址"),
    project_dir: str = typer.Option(".", "--project", "-d", help="项目根目录"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="详细日志"),
):
    """启动 KaiwuMCP 服务 (stdio)。"""
    import asyncio as _asyncio
    from kaiwu.mcp.router_mcp import KaiwuMCP

    project_root = os.path.abspath(project_dir)
    ollama_model = model or "qwen3-8b"

    log_level = logging.DEBUG if verbose else logging.WARNING
    if verbose:
        logging.basicConfig(level=log_level, format="%(name)s: %(message)s")
        logging.getLogger("kaiwu").propagate = True

    gate, orchestrator, memory, _reg = _build_pipeline(
        model_path=model_path,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        project_root=project_root,
        verbose=verbose,
    )

    mcp = KaiwuMCP(gate=gate, orchestrator=orchestrator, memory=memory, project_root=project_root)
    _asyncio.run(mcp.run_stdio())


# ── Checkpoint commands ─────────────────────────────────────

checkpoint_app = typer.Typer(name="checkpoint", help="文件快照管理")
app.add_typer(checkpoint_app)


@checkpoint_app.command("list")
def checkpoint_list():
    """查看所有快照。"""
    from kaiwu.core.checkpoint import list_checkpoints
    items = list_checkpoints()
    if not items:
        console.print("  没有快照记录")
        return
    console.print(f"  [cyan]快照: {len(items)}[/cyan]")
    for item in items:
        console.print(f"  {item['name']}  {item['created']}  {item['files']}个文件")


@checkpoint_app.command("restore")
def checkpoint_restore():
    """还原到最近快照。"""
    from kaiwu.core.checkpoint import restore_latest
    if restore_latest():
        console.print("  [green]✓ 已还原到最近快照[/green]")
    else:
        console.print("  [red]没有可用的快照[/red]")


# ── Search setup command ────────────────────────────────────

@app.command("setup-search")
def cmd_setup_search():
    """一键安装 SearXNG 搜索引擎（需要 Docker）。"""
    from rich.progress import Progress, SpinnerColumn, TextColumn
    import subprocess

    console.print()
    console.print("  [bold]SearXNG 搜索引擎安装[/bold]")
    console.print("  " + "─" * 40)
    console.print()
    console.print("  SearXNG 是本地多引擎聚合搜索，安装后搜索质量大幅提升。")
    console.print("  需要：Docker Desktop 已安装并运行")
    console.print()

    # Step 1: Check Docker
    console.print("  [cyan]1/4[/cyan] 检查 Docker...")
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=10, text=True)
        if r.returncode != 0:
            console.print("  [red]✗ Docker 未运行[/red]")
            console.print("  请先启动 Docker Desktop，然后重新运行 kwcode setup-search")
            return
        console.print("  [green]✓ Docker 就绪[/green]")
    except FileNotFoundError:
        console.print("  [red]✗ Docker 未安装[/red]")
        console.print()
        console.print("  安装 Docker Desktop：")
        console.print("    Windows: https://docs.docker.com/desktop/install/windows-install/")
        console.print("    Mac:     https://docs.docker.com/desktop/install/mac-install/")
        console.print("    Linux:   sudo apt install docker.io && sudo systemctl start docker")
        console.print()
        console.print("  安装后重新运行 [bold]kwcode setup-search[/bold]")
        return
    except subprocess.TimeoutExpired:
        console.print("  [red]✗ Docker 响应超时[/red]")
        return

    container_name = "kwcode-searxng"

    # Step 2: Check if container already exists
    console.print("  [cyan]2/4[/cyan] 检查现有容器...")
    try:
        r = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name=^{container_name}$", "--format", "{{.Status}}"],
            capture_output=True, timeout=5, text=True,
        )
        status = r.stdout.strip()
        if status and "Up" in status:
            console.print("  [green]✓ SearXNG 已在运行[/green]")
            _verify_searxng_json(container_name)
            console.print()
            console.print("  [bold green]安装完成！[/bold green] 搜索引擎已就绪。")
            return
        elif status:
            console.print("  [yellow]容器已存在但未运行，正在启动...[/yellow]")
            subprocess.run(["docker", "start", container_name], capture_output=True, timeout=15)
        else:
            # Step 3: Pull and run
            console.print("  [cyan]3/4[/cyan] 拉取 SearXNG 镜像（首次约 200MB）...")
            with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                          transient=True, console=console) as progress:
                progress.add_task("拉取镜像中...", total=None)
                r = subprocess.run(
                    ["docker", "pull", "searxng/searxng"],
                    capture_output=True, timeout=300, text=True,
                )
            if r.returncode != 0:
                console.print(f"  [red]✗ 镜像拉取失败[/red]")
                console.print(f"  [dim]{r.stderr[:200]}[/dim]")
                return
            console.print("  [green]✓ 镜像就绪[/green]")

            console.print("  [cyan]4/4[/cyan] 启动容器...")
            r = subprocess.run(
                ["docker", "run", "-d",
                 "--name", container_name,
                 "--restart", "always",
                 "-p", "8080:8080",
                 "searxng/searxng"],
                capture_output=True, timeout=30, text=True,
            )
            if r.returncode != 0:
                console.print(f"  [red]✗ 容器启动失败[/red]")
                console.print(f"  [dim]{r.stderr[:200]}[/dim]")
                return
    except subprocess.TimeoutExpired:
        console.print("  [red]✗ 操作超时[/red]")
        return
    except Exception as e:
        console.print(f"  [red]✗ 错误：{e}[/red]")
        return

    # Wait for ready
    import httpx as _httpx
    console.print("  等待 SearXNG 就绪...")
    for i in range(15):
        import time as _t
        _t.sleep(1)
        try:
            resp = _httpx.get("http://localhost:8080/healthz", timeout=2)
            if resp.status_code == 200:
                break
        except Exception:
            pass
    else:
        console.print("  [yellow]⚠ SearXNG 启动较慢，可能需要等待几秒[/yellow]")

    # Enable JSON format
    _verify_searxng_json(container_name)

    console.print()
    console.print("  [bold green]安装完成！[/bold green]")
    console.print("  SearXNG 运行在 http://localhost:8080")
    console.print("  kwcode 启动时会自动检测并使用。")
    console.print()
    console.print("  [dim]管理命令：[/dim]")
    console.print("  [dim]  停止：docker stop kwcode-searxng[/dim]")
    console.print("  [dim]  启动：docker start kwcode-searxng[/dim]")
    console.print("  [dim]  卸载：docker rm -f kwcode-searxng[/dim]")


def _verify_searxng_json(container_name: str):
    """确保 SearXNG 启用了 JSON 输出格式。"""
    import subprocess
    try:
        r = subprocess.run(
            ["docker", "exec", container_name,
             "grep", "-c", "json", "/etc/searxng/settings.yml"],
            capture_output=True, timeout=5, text=True,
        )
        if r.returncode == 0 and int(r.stdout.strip() or "0") > 0:
            console.print("  [green]✓ JSON 格式已启用[/green]")
            return

        # Add json format
        subprocess.run(
            ["docker", "exec", container_name,
             "sed", "-i", r"s/^    - html$/    - html\n    - json/",
             "/etc/searxng/settings.yml"],
            capture_output=True, timeout=5,
        )
        subprocess.run(
            ["docker", "restart", container_name],
            capture_output=True, timeout=15,
        )
        # Wait for restart
        import time as _t
        for _ in range(8):
            _t.sleep(1)
            try:
                import httpx as _hx
                resp = _hx.get("http://localhost:8080/healthz", timeout=2)
                if resp.status_code == 200:
                    break
            except Exception:
                pass
        console.print("  [green]✓ JSON 格式已启用（已重启容器）[/green]")
    except Exception:
        console.print("  [yellow]⚠ 无法验证 JSON 格式，搜索可能降级到 DDG[/yellow]")


if __name__ == "__main__":
    app()
