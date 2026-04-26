"""
Kaiwu CLI entry point.
- kaiwu              → 进入交互式 REPL
- kaiwu "修复bug"    → 单次执行
- kaiwu init         → 初始化 KAIWU.md
- kaiwu memory       → 查看项目记忆
"""

import logging
import os
import sys
import time

# Windows GBK console encoding fix
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

app = typer.Typer(
    name="kaiwu",
    help="Kaiwu - 本地模型 coding agent",
    add_completion=False,
    no_args_is_help=False,
)
console = Console()

# ── Status display ────────────────────────────────────────────

def _status_callback(stage: str, detail: str):
    """Rich console status callback for orchestrator."""
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
    """Construct the full pipeline. Returns (gate, orchestrator, memory)."""
    from kaiwu.llm.llama_backend import LLMBackend
    from kaiwu.core.gate import Gate
    from kaiwu.core.orchestrator import PipelineOrchestrator
    from kaiwu.experts.locator import LocatorExpert
    from kaiwu.experts.generator import GeneratorExpert
    from kaiwu.experts.verifier import VerifierExpert
    from kaiwu.experts.search_augmentor import SearchAugmentorExpert
    from kaiwu.experts.office_handler import OfficeHandlerExpert
    from kaiwu.tools.executor import ToolExecutor
    from kaiwu.memory.kaiwu_md import KaiwuMemory

    llm = LLMBackend(
        model_path=model_path,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        verbose=verbose,
    )
    tools = ToolExecutor(project_root=project_root)
    memory = KaiwuMemory()
    gate = Gate(llm=llm)

    locator = LocatorExpert(llm=llm, tool_executor=tools)
    generator = GeneratorExpert(llm=llm, tool_executor=tools)
    verifier = VerifierExpert(llm=llm, tool_executor=tools)
    search = SearchAugmentorExpert(llm=llm)
    office = OfficeHandlerExpert()

    orchestrator = PipelineOrchestrator(
        locator=locator, generator=generator, verifier=verifier,
        search_augmentor=search, office_handler=office,
        tool_executor=tools, memory=memory,
    )
    return gate, orchestrator, memory


# ── Single task execution ─────────────────────────────────────

def _run_task(task, gate, orchestrator, memory, project_root, verbose, plan=False):
    """Execute a single task through the pipeline. Returns success bool."""
    from kaiwu.core.orchestrator import EXPERT_SEQUENCES

    # Gate
    console.print(f"\n  [cyan]Gate 分析中...[/cyan]")
    gate_result = gate.classify(task, memory_context=memory.load(project_root))

    if "_parse_error" in gate_result:
        console.print(f"  [yellow]Gate 解析降级: {gate_result['_parse_error']}[/yellow]")

    et = gate_result["expert_type"]
    diff = gate_result["difficulty"]
    summary = gate_result.get("task_summary", "")
    seq = EXPERT_SEQUENCES.get(et, ["generator", "verifier"])
    seq_display = " -> ".join(s.capitalize() for s in seq)

    console.print(f"  [bold]{et}[/bold] | {diff} | {summary}")
    console.print(f"  [dim]{seq_display}[/dim]")

    # Plan mode confirmation
    if plan:
        console.print()
        confirm = Prompt.ask("  确认执行?", choices=["y", "n"], default="y")
        if confirm != "y":
            console.print("  [yellow]已取消[/yellow]")
            return False

    # Execute
    status_fn = _status_callback if verbose else _status_callback  # REPL 模式始终显示进度
    result = orchestrator.run(
        user_input=task,
        gate_result=gate_result,
        project_root=project_root,
        on_status=status_fn,
    )

    # Output
    elapsed = result.get("elapsed", 0)
    if result["success"]:
        ctx = result["context"]
        files = []
        if ctx.locator_output:
            files = ctx.locator_output.get("relevant_files", [])
        elif ctx.generator_output:
            files = [p.get("file", "") for p in ctx.generator_output.get("patches", [])]
        files_str = ", ".join(files[:5]) if files else "N/A"

        console.print(f"\n  [bold green]Done[/bold green] {files_str} ({elapsed:.1f}s)")

        if ctx.generator_output and ctx.generator_output.get("explanation"):
            console.print(f"  [dim]{ctx.generator_output['explanation'][:200]}[/dim]")
        return True
    else:
        error = result.get("error", "Unknown")
        console.print(f"\n  [bold red]Failed[/bold red] {error} ({elapsed:.1f}s)")
        ctx = result.get("context")
        if ctx and ctx.verifier_output:
            detail = ctx.verifier_output.get("error_detail", "")
            if detail:
                console.print(f"  [dim]{detail[:200]}[/dim]")
        return False


# ── REPL ──────────────────────────────────────────────────────

REPL_COMMANDS = {
    "/help":   "显示帮助",
    "/memory": "查看项目记忆 (KAIWU.md)",
    "/init":   "初始化 KAIWU.md",
    "/model":  "切换模型 (用法: /model qwen3-8b)",
    "/cd":     "切换项目目录 (用法: /cd /path/to/project)",
    "/plan":   "下一个任务先显示计划再执行",
    "/exit":   "退出",
}


def _repl(model_path, ollama_url, ollama_model, project_root, verbose):
    """Interactive REPL loop."""
    from kaiwu.memory.kaiwu_md import KaiwuMemory

    console.print(Panel(
        f"[bold]Kaiwu v0.3[/bold]  交互模式\n"
        f"模型: {ollama_model}  项目: {project_root}\n"
        f"输入任务开始，/help 查看命令，/exit 退出",
        border_style="cyan",
    ))

    gate, orchestrator, memory = _build_pipeline(
        model_path=model_path,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        project_root=project_root,
        verbose=verbose,
    )

    plan_next = False
    task_count = 0

    while True:
        try:
            console.print()
            user_input = Prompt.ask("[bold cyan]kaiwu[/bold cyan]").strip()
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
                result = memory.init(project_root)
                console.print(f"  {result}")

            elif cmd == "/model":
                if not arg:
                    console.print(f"  当前模型: {ollama_model}")
                    console.print("  用法: /model qwen3-8b")
                else:
                    ollama_model = arg.strip()
                    console.print(f"  [green]模型切换为: {ollama_model}[/green]")
                    console.print("  [dim]重建流水线...[/dim]")
                    gate, orchestrator, memory = _build_pipeline(
                        model_path=model_path,
                        ollama_url=ollama_url,
                        ollama_model=ollama_model,
                        project_root=project_root,
                        verbose=verbose,
                    )

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
                        gate, orchestrator, memory = _build_pipeline(
                            model_path=model_path,
                            ollama_url=ollama_url,
                            ollama_model=ollama_model,
                            project_root=project_root,
                            verbose=verbose,
                        )
                    else:
                        console.print(f"  [red]目录不存在: {new_root}[/red]")

            elif cmd == "/plan":
                plan_next = True
                console.print("  [dim]下一个任务将先显示计划[/dim]")

            else:
                console.print(f"  [yellow]未知命令: {cmd}[/yellow]  输入 /help 查看帮助")

            continue

        # ── Execute task ──
        task_count += 1
        success = _run_task(
            task=user_input,
            gate=gate,
            orchestrator=orchestrator,
            memory=memory,
            project_root=project_root,
            verbose=verbose,
            plan=plan_next,
        )
        plan_next = False  # Reset plan flag

        if success:
            console.print(f"  [dim]#{task_count} 完成[/dim]")


# ── Typer commands ────────────────────────────────────────────

@app.command()
def main(
    task: str = typer.Argument(None, help="任务描述。不提供则进入交互模式"),
    plan: bool = typer.Option(False, "--plan", "-p", help="先输出计划，确认后执行"),
    model: str = typer.Option(None, "--model", "-m", help="Ollama模型名称 (默认 qwen3-8b)"),
    model_path: str = typer.Option(None, "--model-path", help="本地GGUF模型路径"),
    ollama_url: str = typer.Option("http://localhost:11434", "--ollama-url", help="Ollama服务地址"),
    project_dir: str = typer.Option(".", "--project", "-d", help="项目根目录"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="显示详细日志"),
    do_init: bool = typer.Option(False, "--init", help="初始化KAIWU.md"),
    show_memory: bool = typer.Option(False, "--memory", help="查看项目记忆"),
):
    """Kaiwu - 本地模型 coding agent。无参数进入交互模式。"""

    log_level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(level=log_level, format="%(name)s: %(message)s")

    project_root = os.path.abspath(project_dir)
    ollama_model = model or "qwen3-8b"

    # ── Subcommands ──
    if do_init:
        from kaiwu.memory.kaiwu_md import KaiwuMemory
        mem = KaiwuMemory()
        console.print(mem.init(project_root))
        return

    if show_memory:
        from kaiwu.memory.kaiwu_md import KaiwuMemory
        mem = KaiwuMemory()
        console.print(Panel(mem.show(project_root), title="KAIWU.md", border_style="blue"))
        return

    # ── No task → REPL mode ──
    if not task:
        _repl(
            model_path=model_path,
            ollama_url=ollama_url,
            ollama_model=ollama_model,
            project_root=project_root,
            verbose=verbose,
        )
        return

    # ── Single task mode ──
    console.print(Panel(
        f"[bold]Kaiwu v0.3[/bold] | {ollama_model} | {project_root}",
        border_style="cyan",
    ))

    gate, orchestrator, memory = _build_pipeline(
        model_path=model_path,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        project_root=project_root,
        verbose=verbose,
    )

    success = _run_task(task, gate, orchestrator, memory, project_root, verbose, plan)
    if not success:
        raise typer.Exit(1)


@app.command("init")
def cmd_init(
    project_dir: str = typer.Option(".", "--project", "-d", help="项目根目录"),
):
    """初始化 KAIWU.md 项目记忆文件。"""
    from kaiwu.memory.kaiwu_md import KaiwuMemory
    console.print(KaiwuMemory().init(os.path.abspath(project_dir)))


@app.command("memory")
def cmd_memory(
    project_dir: str = typer.Option(".", "--project", "-d", help="项目根目录"),
):
    """查看当前项目的 KAIWU.md 记忆。"""
    from kaiwu.memory.kaiwu_md import KaiwuMemory
    content = KaiwuMemory().show(os.path.abspath(project_dir))
    Console().print(Panel(content, title="KAIWU.md", border_style="blue"))


if __name__ == "__main__":
    app()
