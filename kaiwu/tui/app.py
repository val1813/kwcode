"""
KwCode TUI: Textual-based terminal interface.
Connects to kwcode server (localhost:7355) via SSE.
Auto-starts server if not running.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy import check
try:
    from textual.app import App, ComposeResult
    from textual.widgets import (
        Header, Footer, DirectoryTree, RichLog, Input, Static
    )
    from textual.containers import Horizontal, Vertical
    from textual.binding import Binding
    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False


# Event icons (mirrors CLI EVENT_ICONS)
EVENT_ICONS = {
    "expert_start":    "●",
    "reading_file":    "  📄",
    "file_written":    "  ✓",
    "applying_patch":  "  →",
    "patch_result":    "  ✓",
    "generator_patch": "  →",
    "test_pass":       "  [green]✓[/green]",
    "test_fail":       "  [red]✗[/red]",
    "retry":           "🔄",
    "circuit_break":   "⛔",
    "scope_narrow":    "🎯",
    "search_start":    "🌐",
    "search_solution": "💡",
    "plan_generated":  "📋",
    "pre_compact":     "📦",
    "wink_intervene":  "🔧",
    "gate_start":      "⚡",
    "gate_done":       "✓",
    "task_completed":  "✅",
    "task_error":      "❌",
    "keepalive":       "",
}

DEFAULT_SERVER_URL = "http://127.0.0.1:7355"


def _check_server(server_url: str = DEFAULT_SERVER_URL, timeout: float = 2.0) -> bool:
    """Check if kwcode server is reachable."""
    try:
        import httpx
        resp = httpx.get(f"{server_url}/api/health", timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


def _start_server(
    server_url: str = DEFAULT_SERVER_URL,
    model: Optional[str] = None,
    project_root: str = ".",
    wait_seconds: float = 10.0,
) -> Optional[subprocess.Popen]:
    """Start kwcode server as a subprocess. Returns process or None."""
    cmd = [sys.executable, "-m", "kaiwu.cli.main", "serve"]
    if model:
        cmd.extend(["--model", model])
    cmd.extend(["--project", project_root])

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except Exception as e:
        logger.error("Failed to start server: %s", e)
        return None

    # Wait for server to be ready
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if _check_server(server_url, timeout=1.0):
            return proc
        time.sleep(0.5)

    # Server didn't start in time
    proc.terminate()
    return None


if TEXTUAL_AVAILABLE:

    class KwcodeTUI(App):
        """
        KwCode Terminal UI.
        Left: file tree + preview
        Right: event log + task input
        """

        CSS = """
        #main-container { height: 100%; }
        #file-panel { width: 35%; border-right: solid $primary; }
        #chat-panel { width: 65%; }
        #event-log { height: 1fr; }
        #file-preview { height: 30%; border-top: solid $surface; overflow-y: auto; }
        #input-box { height: 3; dock: bottom; }
        """

        BINDINGS = [
            Binding("ctrl+c", "quit", "退出"),
            Binding("ctrl+l", "clear_log", "清屏"),
        ]

        TITLE = "KwCode"
        SUB_TITLE = "Local Model Coding Agent"

        def __init__(
            self,
            server_url: str = DEFAULT_SERVER_URL,
            project_root: str = ".",
            **kwargs,
        ):
            super().__init__(**kwargs)
            self.server_url = server_url
            self.project_root = os.path.abspath(project_root)
            self._server_proc: Optional[subprocess.Popen] = None

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal(id="main-container"):
                with Vertical(id="file-panel"):
                    yield DirectoryTree(self.project_root, id="file-tree")
                    yield Static("", id="file-preview")
                with Vertical(id="chat-panel"):
                    yield RichLog(id="event-log", highlight=True, markup=True, wrap=True)
                    yield Input(
                        placeholder="输入任务... (Ctrl+C 退出)",
                        id="input-box",
                    )
            yield Footer()

        async def on_mount(self) -> None:
            """Check server connection on startup."""
            log = self.query_one("#event-log", RichLog)
            log.write("[bold]KwCode TUI[/bold] 启动中...")

            # Check if server is running
            if not _check_server(self.server_url):
                log.write("[yellow]Server 未运行，正在启动...[/yellow]")
                self._server_proc = await asyncio.to_thread(
                    _start_server,
                    self.server_url,
                    None,
                    self.project_root,
                )
                if self._server_proc:
                    log.write("[green]✓ Server 已启动[/green]")
                else:
                    log.write("[red]✗ Server 启动失败，请手动运行: kwcode serve[/red]")
                    return

            log.write(f"[green]✓ 已连接 {self.server_url}[/green]")
            log.write("[dim]输入任务描述开始工作...[/dim]\n")

        async def on_input_submitted(self, event: Input.Submitted) -> None:
            """Submit task to server and stream events."""
            task_text = event.value.strip()
            if not task_text:
                return

            # Clear input
            input_widget = self.query_one("#input-box", Input)
            input_widget.value = ""

            log = self.query_one("#event-log", RichLog)
            log.write(f"\n[bold blue]▶ {task_text}[/bold blue]")

            # Submit task
            try:
                import httpx
                async with httpx.AsyncClient(timeout=None) as client:
                    # Submit task
                    resp = await client.post(
                        f"{self.server_url}/api/task",
                        json={"input": task_text, "project_root": self.project_root},
                    )
                    if resp.status_code != 200:
                        log.write(f"[red]错误: {resp.text}[/red]")
                        return

                    data = resp.json()
                    task_id = data["task_id"]

                    # Stream events
                    async with client.stream(
                        "GET", f"{self.server_url}/api/task/{task_id}/events"
                    ) as stream:
                        async for line in stream.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            try:
                                event_data = json.loads(line[6:])
                            except json.JSONDecodeError:
                                continue
                            self._render_event(log, event_data)
                            if event_data.get("event") in ("task_completed", "task_error"):
                                break

            except httpx.ConnectError:
                log.write("[red]连接失败: server 不可达[/red]")
            except Exception as e:
                log.write(f"[red]错误: {e}[/red]")

        def _render_event(self, log: RichLog, event: dict) -> None:
            """Render an SSE event to the log."""
            event_type = event.get("event", "")
            icon = EVENT_ICONS.get(event_type, "·")

            if not icon:  # keepalive etc
                return

            if event_type == "task_completed":
                success = event.get("success", False)
                elapsed = event.get("elapsed", 0)
                files = event.get("files_modified", [])
                if success:
                    log.write(f"\n[bold green]{icon} 任务完成[/bold green] ({elapsed:.1f}s)")
                    for f in files:
                        log.write(f"  [green]✓[/green] {f}")
                else:
                    error = event.get("error", "未知错误")
                    log.write(f"\n[bold red]❌ 任务失败[/bold red]: {error}")
            elif event_type == "task_error":
                log.write(f"\n[bold red]{icon} {event.get('error', '未知错误')}[/bold red]")
            else:
                msg = event.get("path") or event.get("msg") or event.get("cmd", "")
                if msg:
                    log.write(f"{icon} {msg}")

        async def on_directory_tree_file_selected(self, event) -> None:
            """Show file preview when a file is selected in the tree."""
            preview = self.query_one("#file-preview", Static)
            try:
                path = str(event.path)
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(2000)  # First 2000 chars
                # Truncate for display
                lines = content.split("\n")[:30]
                display = "\n".join(lines)
                if len(lines) >= 30:
                    display += "\n[dim]... (truncated)[/dim]"
                preview.update(f"[bold]{os.path.basename(path)}[/bold]\n{display}")
            except Exception as e:
                preview.update(f"[red]无法读取: {e}[/red]")

        def action_clear_log(self) -> None:
            """Clear the event log."""
            log = self.query_one("#event-log", RichLog)
            log.clear()

        async def action_quit(self) -> None:
            """Quit and cleanup."""
            if self._server_proc:
                self._server_proc.terminate()
            self.exit()


def run_tui(
    server_url: str = DEFAULT_SERVER_URL,
    project_root: str = ".",
):
    """Entry point to launch the TUI."""
    if not TEXTUAL_AVAILABLE:
        print("错误: textual 未安装。请运行: pip install textual")
        print("或: pip install kwcode[tui]")
        sys.exit(1)

    app = KwcodeTUI(server_url=server_url, project_root=project_root)
    app.run()
