"""
Configuration CLI commands: init, memory, status, stats, setup-search, serve, serve-mcp, checkpoint, api.
"""

import logging
import os
import subprocess

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from kaiwu.cli.formatters import VERSION, console

logger = logging.getLogger(__name__)


def cmd_init(
    project_dir: str = typer.Option(".", "--project", "-d", help="项目根目录"),
):
    """初始化 KWCODE.md + KAIWU.md 项目文件。"""
    project_root = os.path.abspath(project_dir)
    from kaiwu.core.kwcode_md import generate_kwcode_template
    console.print(f"  {generate_kwcode_template(project_root)}")
    from kaiwu.memory.kaiwu_md import KaiwuMemory
    console.print(KaiwuMemory().init(project_root))


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

    # ── 飞轮统计 ──
    console.print()
    console.print("  " + "─" * 45)

    try:
        from kaiwu.flywheel.strategy_stats import StrategyStats
        ss = StrategyStats().get_summary()
        if ss:
            console.print()
            console.print("  [bold cyan]错误策略飞轮[/bold cyan]")
            for et, info in ss.items():
                console.print(
                    f"    {et}: 最优 {info['best_sequence']} "
                    f"({info['best_success_rate']}，{info['total_attempts']}次)"
                )
        else:
            console.print("  [dim]错误策略飞轮：数据积累中...[/dim]")
    except Exception:
        pass

    try:
        from kaiwu.flywheel.user_pattern_memory import UserPatternMemory
        p = UserPatternMemory().get_summary()
        console.print()
        console.print("  [bold cyan]用户错误模式[/bold cyan]")
        console.print(f"    累计任务：{p['total_tasks']} 次")
        console.print(f"    总体成功率：{p['success_rate']}")
        if p['top_errors']:
            console.print(f"    高频错误：{', '.join(p['top_errors'])}")
    except Exception:
        pass

    from pathlib import Path
    draft_path = Path(".kaiwu/skill_draft.md")
    if draft_path.exists():
        console.print()
        console.print("  [bold yellow]有待审核的 SKILL.md 草稿[/bold yellow]")
        console.print("    运行 [bold]kwcode skill review[/bold] 查看")

    console.print()
    try:
        from kaiwu.cli.onboarding import load_config
        if load_config().get("telemetry_enabled"):
            console.print("  [dim]匿名统计：已开启（仅上传成功率元数据）[/dim]")
        else:
            console.print("  [dim]数据仅存本地，不上报任何服务器[/dim]")
    except Exception:
        console.print("  [dim]数据仅存本地，不上报任何服务器[/dim]")
    console.print()


def cmd_serve(
    model: str = typer.Option(None, "--model", "-m", help="Ollama模型名称"),
    model_path: str = typer.Option(None, "--model-path", help="本地GGUF模型路径"),
    ollama_url: str = typer.Option("http://localhost:11434", "--ollama-url", help="Ollama服务地址"),
    project_dir: str = typer.Option(".", "--project", "-d", help="项目根目录"),
    port: int = typer.Option(7355, "--port", help="HTTP服务端口"),
    host: str = typer.Option("127.0.0.1", "--host", help="HTTP服务地址"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="详细日志"),
):
    """启动 kwcode HTTP server (FastAPI + SSE, 端口7355)。"""
    import uvicorn
    from kaiwu.server.app import create_app
    from kaiwu.cli.onboarding import load_config as _load_cfg

    project_root = os.path.abspath(project_dir)
    ollama_model = model or "qwen3-8b"

    log_level = logging.DEBUG if verbose else logging.WARNING
    if verbose:
        logging.basicConfig(level=log_level, format="%(name)s: %(message)s")
        logging.getLogger("kaiwu").propagate = True

    # Load API key from config
    _cfg = _load_cfg().get("default", {})
    _api_key = _cfg.get("api_key", "")
    if not model and _cfg.get("model"):
        ollama_model = _cfg["model"]
    if ollama_url == "http://localhost:11434" and _cfg.get("base_url"):
        ollama_url = _cfg["base_url"]

    console.print(f"  [bold]KwCode Server[/bold] 启动中...")
    console.print(f"  模型: {ollama_model}")
    console.print(f"  项目: {project_root}")
    console.print(f"  地址: http://{host}:{port}")
    console.print()

    server_app = create_app(
        model_path=model_path,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        project_root=project_root,
        verbose=verbose,
        api_key=_api_key,
    )

    uvicorn.run(server_app, host=host, port=port, log_level="info" if verbose else "warning")


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
    from kaiwu.cli.commands.task import build_pipeline

    project_root = os.path.abspath(project_dir)
    ollama_model = model or "qwen3-8b"

    log_level = logging.DEBUG if verbose else logging.WARNING
    if verbose:
        logging.basicConfig(level=log_level, format="%(name)s: %(message)s")
        logging.getLogger("kaiwu").propagate = True

    gate, orchestrator, memory, _reg = build_pipeline(
        model_path=model_path,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        project_root=project_root,
        verbose=verbose,
    )

    mcp = KaiwuMCP(gate=gate, orchestrator=orchestrator, memory=memory, project_root=project_root)
    _asyncio.run(mcp.run_stdio())


def cmd_setup_search():
    """一键安装 SearXNG 搜索引擎（需要 Docker）。"""
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


# ── Checkpoint commands ─────────────────────────────────────

checkpoint_app = typer.Typer(name="checkpoint", help="文件快照管理")


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


# ── /api command handler ─────────────────────────────────────

def handle_api_command(parts: list[str], current_url: str, current_model: str):
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


# ── Telemetry commands ──────────────────────────────────────

telemetry_app = typer.Typer(name="telemetry", help="匿名遥测管理")


@telemetry_app.command("status")
def telemetry_status():
    """查看匿名遥测状态。"""
    from kaiwu.cli.onboarding import load_config
    config = load_config()
    enabled = config.get("telemetry_enabled", False)
    if enabled:
        console.print("  [green]匿名统计：已开启[/green]")
        console.print("  [dim]上传内容：error_type, retry_count, success, model[/dim]")
        console.print("  [dim]不上传：代码内容、文件路径、任务描述、用户信息[/dim]")
    else:
        console.print("  [dim]匿名统计：已关闭[/dim]")
    console.print("  [dim]运行 kwcode telemetry enable/disable 切换[/dim]")


@telemetry_app.command("enable")
def telemetry_enable():
    """开启匿名遥测。"""
    from kaiwu.cli.onboarding import load_config, _save_config
    config = load_config()
    config["telemetry_enabled"] = True
    _save_config(config)
    console.print("  [green]✓ 匿名统计已开启[/green]")


@telemetry_app.command("disable")
def telemetry_disable():
    """关闭匿名遥测。"""
    from kaiwu.cli.onboarding import load_config, _save_config
    config = load_config()
    config["telemetry_enabled"] = False
    _save_config(config)
    console.print("  [dim]匿名统计已关闭[/dim]")


# ── Skill commands ─────────────────────────────────────────

skill_app = typer.Typer(name="skill", help="SKILL.md 管理")


@skill_app.command("review")
def skill_review():
    """查看自动生成的 SKILL.md 草稿。"""
    from pathlib import Path
    draft_path = Path(".kaiwu/skill_draft.md")
    if not draft_path.exists():
        console.print("  [dim]暂无待审核的 SKILL.md 草稿[/dim]")
        console.print("  继续使用 kwcode，积累更多数据后会自动生成。")
        return
    console.print(draft_path.read_text(encoding="utf-8"))


@skill_app.command("accept")
def skill_accept():
    """将草稿合并到正式 SKILL.md。"""
    from pathlib import Path
    draft_path = Path(".kaiwu/skill_draft.md")
    if not draft_path.exists():
        console.print("  [dim]暂无待审核的草稿[/dim]")
        return
    skill_path = Path("SKILL.md")
    draft_content = draft_path.read_text(encoding="utf-8")
    with open(skill_path, "a", encoding="utf-8") as f:
        f.write("\n\n" + draft_content)
    draft_path.unlink()
    console.print("  [green]✓ 草稿已合并到 SKILL.md[/green]")


@skill_app.command("discard")
def skill_discard():
    """丢弃草稿。"""
    from pathlib import Path
    draft_path = Path(".kaiwu/skill_draft.md")
    if draft_path.exists():
        draft_path.unlink()
        console.print("  [dim]草稿已丢弃[/dim]")
    else:
        console.print("  [dim]暂无待丢弃的草稿[/dim]")


def maybe_show_weekly_stats(console):
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
