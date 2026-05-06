"""
Interactive REPL loop for KwCode CLI.
"""

import os
import time

from rich.panel import Panel

from kaiwu.cli.formatters import VERSION, console, escape_html, render_header
from kaiwu.cli.commands.config import handle_api_command, maybe_show_weekly_stats
from kaiwu.cli.commands.task import (
    build_pipeline,
    run_task,
    handle_multi_command,
)


# ── REPL commands ─────────────────────────────────────────────

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


def _resolve_image_path(path: str, project_root: str) -> str:
    """Resolve /image paths relative to the active project directory."""
    expanded = os.path.expanduser(path)
    if not os.path.isabs(expanded):
        expanded = os.path.join(project_root, expanded)
    return os.path.abspath(expanded)


def repl(model_path, ollama_url, ollama_model, project_root, verbose, no_search=False):
    """Interactive REPL loop with prompt_toolkit bottom_toolbar."""
    from kaiwu.memory.kaiwu_md import KaiwuMemory
    from kaiwu.core.sysinfo import get_sysinfo, VRAMWatcher
    from kaiwu.core.context_pruner import ContextPruner
    from kaiwu.cli.status_bar import StatusBar, TokPerSecEstimator
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.completion import Completer, Completion

    gate, orchestrator, memory, registry = build_pipeline(
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
    render_header(ollama_model, project_root, registry)

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
    maybe_show_weekly_stats(console)

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
        bar = escape_html(status.render(width))
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
                    gate, orchestrator, memory, registry = build_pipeline(
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
                        gate, orchestrator, memory, registry = build_pipeline(
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
                    success = run_task(
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
                handle_multi_command(arg, gate, orchestrator, project_root, console)

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
                result = handle_api_command(api_parts, ollama_url, ollama_model)
                if result:
                    # /api temp or /api default changed the URL, rebuild pipeline
                    ollama_url = result.get("url", ollama_url)
                    gate, orchestrator, memory, registry = build_pipeline(
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

        success = run_task(
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
