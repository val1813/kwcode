"""
Rich output formatters for CLI: spinners, result summaries, headers, event handlers.
"""

import os
import logging

from rich.console import Console

logger = logging.getLogger(__name__)

console = Console()

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


def eventbus_cli_handler(event: str, payload: dict):
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
SPINNER_STAGES = {
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
def verbose_callback(stage: str, detail: str):
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


def render_header(model: str, project_root: str, registry=None):
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


def render_task_success(result: dict, et: str, project_root: str):
    """Render successful task output."""
    elapsed = result.get("elapsed", 0)
    ctx = result["context"]

    # Chat: print reply directly
    if et == "chat":
        reply = ""
        if ctx.generator_output:
            reply = ctx.generator_output.get("explanation", "")
        console.print(f"\n  {reply}" if reply else
                      "\n  你好！我是KWCode，专注于代码任务。有什么代码问题需要帮忙吗？")
        return

    # Vision: print the analysis/code result directly.
    if et == "vision":
        output = ""
        if ctx.generator_output:
            output = ctx.generator_output.get("explanation", "")
        console.print(f"\n  {output}" if output else "\n  [yellow]图片处理完成，但没有返回内容[/yellow]")
        return

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
        if files:
            console.print(f"\n  [bold green]✓ 完成[/bold green] ({elapsed:.1f}s)")
            for f in files[:3]:
                console.print(f"  修改了 {f}")
        else:
            console.print(f"\n  [bold green]✓ 完成[/bold green] ({elapsed:.1f}s)")

    # Summary bullets from explanation
    if ctx.generator_output and ctx.generator_output.get("explanation"):
        explanation = ctx.generator_output["explanation"]
        lines = [l.strip() for l in explanation.split("\n") if l.strip()][:3]
        for line in lines:
            console.print(f"    · {line[:60]}")

    # Test results
    if ctx.verifier_output:
        passed = ctx.verifier_output.get("tests_passed", 0)
        total = ctx.verifier_output.get("tests_total", 0)
        if total > 0:
            console.print(f"  测试通过 ({passed}/{total})")


def render_task_failure(result: dict):
    """Render failed task output."""
    elapsed = result.get("elapsed", 0)
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
            lines = [l.strip() for l in detail.split("\n") if l.strip()][:3]
            console.print(f"  原因：")
            for line in lines:
                console.print(f"    {line[:80]}")


def render_model_error(e: Exception):
    """Render model call failure with suggestions."""
    console.print(f"\n  [red]❌ 模型调用失败[/red]")
    console.print(f"  [yellow]错误详情：[/yellow]{e}")
    console.print("\n  [cyan]💡 可能的解决方案：[/cyan]")
    console.print("    1. 检查模型是否正常运行：[dim]ollama list[/dim]")
    console.print("    2. 切换到其他模型：[dim]/model qwen3:8b[/dim]")
    console.print("    3. 检查 API 配置：[dim]/api show[/dim]")
    console.print("    4. 如果使用云端 API，检查网络连接和 API key")


def render_execution_error(e: Exception):
    """Render execution exception with debug suggestions."""
    import traceback
    console.print(f"\n  [red]❌ 执行异常[/red]")
    console.print(f"  [yellow]错误详情：[/yellow]{e}")
    console.print("\n  [cyan]💡 调试建议：[/cyan]")
    console.print("    1. 查看详细日志：[dim]~/.kwcode/kwcode.log[/dim]")
    console.print("    2. 尝试更简单的任务描述")
    console.print("    3. 使用 /plan 模式查看执行步骤：[dim]/plan <任务描述>[/dim]")
    console.print("    4. 如果问题持续，请在 GitHub 提交 issue")
    console.print(f"\n  [dim]堆栈跟踪：\n{traceback.format_exc()}[/dim]")


def escape_html(text: str) -> str:
    """Escape HTML special chars for prompt_toolkit HTML."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
