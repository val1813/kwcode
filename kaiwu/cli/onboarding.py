# kwcode/cli/onboarding.py
"""
首次启动引导流程。
BOOT-RED-1：未完成配置不得进入REPL。
BOOT-RED-2：API连通性验证必须在保存前完成。
FLEX-1：验证失败时允许用户跳过，但明确告知风险。
"""

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.progress import Progress, SpinnerColumn, TextColumn
from pathlib import Path
import httpx
import yaml

console = Console()
CONFIG_PATH = Path.home() / ".kwcode" / "config.yaml"


def is_first_run() -> bool:
    """检查是否首次运行（config.yaml 不存在）"""
    return not CONFIG_PATH.exists()


def load_config() -> dict:
    """读取已有的 config.yaml，返回 config dict"""
    if not CONFIG_PATH.exists():
        return {}
    try:
        return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def run_onboarding() -> dict:
    """
    首次启动引导。返回config dict。
    BOOT-RED-1：未完成配置不得进入REPL。
    """
    _print_welcome()
    net = _detect_network_with_progress()
    config = _configure_api(net)
    config = _ask_telemetry(config)
    _save_config(config)
    _print_ready(config, net)
    return config


def _print_welcome():
    console.print()
    console.print(Panel(
        "[bold cyan]KWCode[/bold cyan]  [dim]天工开物[/dim]\n"
        "[dim]中国开发者的本地 Coding Agent[/dim]",
        border_style="cyan",
        padding=(1, 4),
    ))
    console.print()
    console.print("  欢迎使用 KWCode！首次使用需要完成以下配置。\n")


def _detect_network_with_progress() -> dict:
    """探测网络，显示进度"""
    from kaiwu.core.network import detect_network

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
        console=console,
    ) as progress:
        task = progress.add_task("  检测网络环境...", total=None)
        net = detect_network()
        progress.update(task, description="  网络检测完成")

    # 显示检测结果
    if net["china"]:
        console.print("  [yellow]检测到国内网络[/yellow]")
        console.print("  [dim]· 搜索增强：自动使用 Bing 中文版[/dim]")
        if not net["hf_ok"]:
            console.print("  [dim]· 模型下载：HuggingFace不可达，建议用ModelScope[/dim]")
        if net["proxy"]:
            console.print(f"  [dim]· 代理：{net['proxy']}[/dim]")
    else:
        console.print("  [green]网络正常[/green]，使用 DuckDuckGo 搜索")
    console.print()
    return net


def _configure_api(net: dict) -> dict:
    """API配置引导"""
    console.print("  [bold]配置模型接入[/bold]\n")

    # 提示
    console.print("  [dim]支持任何 OpenAI 兼容接口：[/dim]")
    console.print("  [dim]  本地 Ollama      →  http://localhost:11434[/dim]")
    console.print("  [dim]  本地 llama.cpp   →  http://localhost:8080[/dim]")
    console.print("  [dim]  DeepSeek API    →  https://api.deepseek.com[/dim]")
    console.print("  [dim]  其他兼容服务     →  填入对应地址即可[/dim]")
    console.print()

    while True:
        base_url = Prompt.ask(
            "  API Base URL",
            default="http://localhost:11434",
        ).strip().rstrip("/")

        api_key = Prompt.ask(
            "  API Key [dim](本地模型留空，直接回车)[/dim]",
            default="",
            password=True,
        ).strip()

        model = Prompt.ask(
            "  模型名称",
            default="qwen3:8b",
        ).strip()

        console.print()

        # 连通性验证（BOOT-RED-2）
        ok, err = _verify_api(base_url, api_key, model)

        if ok:
            console.print("  [green]✓ 连接成功[/green]")
            console.print()
            break
        else:
            console.print(f"  [red]✗ 连接失败：{err}[/red]")
            console.print("  [dim]请检查地址和Key是否正确[/dim]")
            console.print()

            # 允许跳过验证（FLEX-1）
            skip = Confirm.ask(
                "  网络可能临时抖动，是否跳过验证直接保存？",
                default=False,
            )
            if skip:
                console.print("  [yellow]⚠ 已跳过验证，请确认配置正确[/yellow]")
                console.print()
                break
            # 否则重新输入
            console.print("  重新输入配置：\n")

    return {
        "default": {
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
        }
    }


def _verify_api(base_url: str, api_key: str, model: str) -> tuple[bool, str]:
    """
    验证API连通性。
    BOOT-RED-2：保存前必须验证。
    尝试 /v1/models 或 /api/tags（Ollama）或 /v1/chat/completions。
    """
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
        console=console,
    ) as progress:
        progress.add_task("  验证连接...", total=None)

        # 尝试OpenAI兼容接口
        for path in ["/v1/models", "/api/tags", "/v1/chat/completions"]:
            try:
                url = base_url + path
                if path == "/v1/chat/completions":
                    # 发一个最小请求验证模型可用
                    resp = httpx.post(
                        url,
                        headers=headers,
                        json={
                            "model": model,
                            "messages": [{"role": "user", "content": "hi"}],
                            "max_tokens": 1,
                        },
                        timeout=10,
                    )
                else:
                    resp = httpx.get(url, headers=headers, timeout=5)

                if resp.status_code == 200:
                    return True, ""
                elif resp.status_code in (401, 403):
                    return False, f"认证失败（{resp.status_code}），请检查 API Key"
                elif resp.status_code == 404:
                    continue  # Try next endpoint
                elif resp.status_code < 500:
                    return True, ""  # Other 2xx/3xx considered OK
            except httpx.ConnectError:
                return False, f"无法连接到 {base_url}，请确认服务已启动"
            except httpx.TimeoutException:
                return False, f"连接超时（{base_url}），请检查地址"
            except Exception:
                continue

    return False, "API验证失败，请检查地址和Key"


def _ask_telemetry(config: dict) -> dict:
    """询问用户是否开启匿名遥测。"""
    console.print("  [bold]匿名统计[/bold]\n")
    console.print("  [dim]是否帮助改善kwcode？[/dim]")
    console.print("  [dim]开启后将匿名上传任务成功率统计（不包含任何代码内容）[/dim]")
    console.print("  [dim]可随时在设置中关闭。[/dim]")
    console.print()

    enabled = Confirm.ask(
        "  开启匿名统计",
        default=False,
    )
    config["telemetry_enabled"] = enabled

    if enabled:
        console.print("  [green]✓ 已开启匿名统计[/green]")
    else:
        console.print("  [dim]已关闭，可随时用 kwcode telemetry enable 开启[/dim]")
    console.print()
    return config


def _save_config(config: dict):
    """保存配置到 ~/.kwcode/config.yaml"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        yaml.dump(config, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )
    console.print(f"  [dim]配置已保存至 {CONFIG_PATH}[/dim]\n")


def _print_ready(config: dict, net: dict):
    """引导完成提示"""
    model = config.get("default", {}).get("model", "未配置")
    search_src = "DuckDuckGo" if not net["china"] else "Bing 中文版"

    console.print(Panel(
        f"  [green]✓ KWCode 已就绪[/green]\n\n"
        f"  模型    {model}\n"
        f"  搜索    {search_src}（自动）\n"
        f"  数据    完全本地，不出网\n\n"
        f"  输入 [cyan]/help[/cyan] 查看所有命令",
        border_style="green",
        padding=(0, 2),
    ))
    console.print()
