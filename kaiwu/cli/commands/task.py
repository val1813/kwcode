"""
Task execution commands: run_task, build_pipeline, multi-task handling.
"""

import logging
import os
import time

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt

from kaiwu.cli.formatters import (
    SPINNER_STAGES,
    console,
    eventbus_cli_handler,
    render_execution_error,
    render_model_error,
    render_task_failure,
    render_task_success,
    verbose_callback,
)

logger = logging.getLogger(__name__)


# ── Pipeline builder ──────────────────────────────────────────

def build_pipeline(model_path, ollama_url, ollama_model, project_root, verbose):
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

    # Ctx自适应：检测模型可用上下文窗口
    from kaiwu.core.model_capability import get_effective_ctx
    effective_ctx = get_effective_ctx(ollama_model, ollama_url)

    llm = LLMBackend(
        model_path=model_path,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        n_ctx=effective_ctx,
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
    orchestrator.bus.on("*", eventbus_cli_handler)

    # Wire circular reference: ABTester needs orchestrator for backtest
    ab_tester.orchestrator = orchestrator

    return gate, orchestrator, memory, registry


# ── Single task execution ─────────────────────────────────────

def run_task(task, gate, orchestrator, memory, project_root, verbose, plan=False, no_search=False, image_paths=None):
    """Execute a single task through the pipeline. Returns success bool."""
    from kaiwu.core.orchestrator import EXPERT_SEQUENCES

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
            render_model_error(e)
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
                handle_multi_with_tasks(auto_tasks, gate, orchestrator, project_root, console)
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
        label = SPINNER_STAGES.get(stage)
        if label:
            _spinner_state["description"] = label
        # Verbose mode: also print to console
        if verbose:
            verbose_callback(stage, detail)

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
            render_execution_error(e)
            return False

    # ── Output: user-friendly result summary ──
    if result["success"]:
        render_task_success(result, et, project_root)
        return True
    else:
        render_task_failure(result)
        return False


# ── Multi-task helpers ────────────────────────────────────────

def handle_multi_with_tasks(tasks, gate, orchestrator, project_root, console):
    """复用TaskCompiler执行已解析的tasks（供auto_decompose调用）。"""
    from kaiwu.core.task_compiler import TaskCompiler

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


def handle_multi_command(arg: str, gate, orchestrator, project_root: str, console):
    """
    /multi 多任务模式。支持两种输入方式：
    1. /multi 后交互式逐条输入（空行结束）
    2. /multi task1 ; task2 ; task3（分号分隔）

    依赖关系用 -> 表示：task1 -> task2 表示 task2 依赖 task1。
    无 -> 的任务之间并行执行。
    """
    from kaiwu.core.task_compiler import TaskCompiler

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
