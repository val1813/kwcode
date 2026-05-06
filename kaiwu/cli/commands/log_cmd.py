"""
kwcode log — 任务历史审计日志命令。
"""

import typer
from rich.prompt import Confirm

from kaiwu.cli.formatters import console

log_app = typer.Typer(name="log", help="任务历史日志")


@log_app.callback(invoke_without_command=True)
def log_list(
    ctx: typer.Context,
    limit: int = typer.Option(20, "--limit", "-n", help="显示条数"),
):
    """查看最近的任务历史。"""
    if ctx.invoked_subcommand is not None:
        return

    from kaiwu.audit.logger import list_logs

    logs = list_logs(limit=limit)
    if not logs:
        console.print("  [dim]暂无任务记录[/dim]")
        return

    console.print()
    console.print("  [bold]任务历史[/bold]")
    console.print("  " + "─" * 55)

    for entry in logs:
        icon = "[green]✅[/green]" if entry["success"] else "[red]❌[/red]"
        elapsed = f"{entry['elapsed_s']:.0f}s"
        task = entry["task"][:40]
        ts = entry["timestamp"][:16].replace("T", " ") if entry["timestamp"] else ""

        console.print(
            f"  #{entry['id']:<3} {icon} {elapsed:>4}  {task:<40}  [dim]{ts}[/dim]"
        )

    console.print()
    console.print("  [dim]查看详情：kwcode log show <编号>[/dim]")
    console.print()


@log_app.command("show")
def log_show(
    log_id: int = typer.Argument(..., help="日志编号"),
):
    """查看某次任务的详细执行过程。"""
    from kaiwu.audit.logger import show_log

    data = show_log(log_id)
    if not data:
        console.print(f"  [red]未找到日志 #{log_id}[/red]")
        return

    icon = "✅ 成功" if data.get("success") else "❌ 失败"

    console.print()
    console.print(f"  [bold]任务 #{log_id} 详情[/bold]")
    console.print("  " + "─" * 45)
    console.print(f"  任务：{data.get('task', '')[:80]}")
    console.print(f"  时间：{data.get('timestamp', '')[:19]}")
    console.print(f"  模型：{data.get('model', '')}")
    console.print(f"  耗时：{data.get('elapsed_s', 0):.1f}秒")
    console.print(f"  结果：{icon}")
    console.print()

    # 执行过程
    events = data.get("events", [])
    if events:
        console.print("  [bold cyan]执行过程[/bold cyan]")
        for ev in events:
            stage = ev.get("stage", "")
            detail = ev.get("detail", "")[:80]
            t = ev.get("time", "")
            console.print(f"    {t}  [{stage:<15}] {detail}")
        console.print()

    # 修改文件
    files = data.get("files_modified", [])
    if files:
        console.print(f"  修改文件：{', '.join(files)}")
        console.print(f"  改动规模：+{data.get('lines_added', 0)}行 -{data.get('lines_removed', 0)}行")

    tp = data.get("tests_passed", 0)
    tt = data.get("tests_total", 0)
    if tt > 0:
        console.print(f"  测试结果：{tp}/{tt}")

    console.print(f"  重试次数：{data.get('retry_count', 0)}")
    console.print()


@log_app.command("clear")
def log_clear():
    """清除所有任务日志。"""
    confirm = Confirm.ask("  确认清除所有任务日志？", default=False)
    if not confirm:
        console.print("  [dim]已取消[/dim]")
        return

    from kaiwu.audit.logger import clear_logs
    count = clear_logs()
    console.print(f"  [green]已清除 {count} 条日志[/green]")
