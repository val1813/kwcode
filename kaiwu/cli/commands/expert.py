"""
Expert management CLI commands: list, info, export, install, remove, create.
"""

import typer
from rich.console import Console
from rich.panel import Panel

from kaiwu.cli.formatters import console

expert_app = typer.Typer(name="expert", help="专家管理")


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
