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
from rich.panel import Panel

from kaiwu.cli.formatters import VERSION, console
from kaiwu.cli.commands.expert import expert_app
from kaiwu.cli.commands.config import (
    cmd_init,
    cmd_memory,
    cmd_status,
    cmd_stats,
    cmd_serve,
    cmd_serve_mcp,
    cmd_setup_search,
    checkpoint_app,
    telemetry_app,
    skill_app,
)
from kaiwu.cli.commands.task import (
    build_pipeline,
    run_task,
)
from kaiwu.cli.commands.log_cmd import log_app
from kaiwu.cli.commands.model_cmd import model_app
from kaiwu.cli.repl import repl

app = typer.Typer(
    name="kwcode",
    help="KwCode - 本地模型 coding agent",
    add_completion=False,
    no_args_is_help=False,
    invoke_without_command=True,
)
app.add_typer(expert_app)
app.add_typer(checkpoint_app)
app.add_typer(telemetry_app)
app.add_typer(skill_app)
app.add_typer(log_app)
app.add_typer(model_app)
app.command("init")(cmd_init)
app.command("memory")(cmd_memory)
app.command("status")(cmd_status)
app.command("stats")(cmd_stats)
app.command("serve")(cmd_serve)
app.command("serve-mcp")(cmd_serve_mcp)
app.command("setup-search")(cmd_setup_search)


# ── Typer main callback ──────────────────────────────────────

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
    tui: bool = typer.Option(False, "--tui", help="启动 TUI 界面"),
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

    # ── TUI mode ──
    if tui:
        from kaiwu.tui.app import run_tui
        run_tui(project_root=project_root)
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
        repl(
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

    gate, orchestrator, memory, registry = build_pipeline(
        model_path=model_path,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        project_root=project_root,
        verbose=verbose,
    )

    success = run_task(task, gate, orchestrator, memory, project_root, verbose, plan, no_search)
    if not success:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
