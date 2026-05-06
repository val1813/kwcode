"""
kwcode model — 模型查看/切换/探测命令。
"""

import typer

from kaiwu.cli.formatters import console

model_app = typer.Typer(name="model", help="模型管理")


@model_app.callback(invoke_without_command=True)
def model_show(ctx: typer.Context):
    """查看当前模型配置。"""
    if ctx.invoked_subcommand is not None:
        return

    from kaiwu.cli.onboarding import load_config

    config = load_config()
    cfg = config.get("default", {})
    model = cfg.get("model", "未配置")
    base_url = cfg.get("base_url", "http://localhost:11434")
    has_key = bool(cfg.get("api_key"))

    console.print()
    console.print(f"  [bold]当前模型配置[/bold]")
    console.print("  " + "─" * 40)
    console.print(f"  模型：{model}")
    console.print(f"  API ：{base_url}")
    console.print(f"  Key ：{'已配置' if has_key else '（无）'}")

    # Try detect tier
    try:
        from kaiwu.core.model_capability import detect_model_tier
        tier = detect_model_tier(model, base_url)
        console.print(f"  能力：{tier.value}")
    except Exception:
        pass

    console.print()
    console.print("  [dim]切换模型：kwcode model set <模型名>[/dim]")
    console.print("  [dim]探测能力：kwcode model probe[/dim]")
    console.print()


@model_app.command("set")
def model_set(
    name: str = typer.Argument(..., help="模型名称"),
):
    """切换模型（写入 config.yaml）。"""
    from kaiwu.cli.onboarding import load_config, _save_config

    config = load_config()
    config.setdefault("default", {})
    config["default"]["model"] = name
    _save_config(config)
    console.print(f"  [green]✓ 模型已切换为 {name}[/green]")
    console.print("  [dim]重新启动 kwcode 生效[/dim]")


@model_app.command("probe")
def model_probe():
    """探测当前模型能力（参数量/上下文/推理能力）。"""
    from kaiwu.cli.onboarding import load_config

    config = load_config()
    cfg = config.get("default", {})
    model = cfg.get("model", "未配置")
    base_url = cfg.get("base_url", "http://localhost:11434")

    console.print(f"  探测模型 {model}...")

    # Try Ollama API
    try:
        import httpx
        resp = httpx.post(
            f"{base_url}/api/show",
            json={"name": model},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            params = data.get("details", {}).get("parameter_size", "未知")
            family = data.get("details", {}).get("family", "未知")
            quant = data.get("details", {}).get("quantization_level", "未知")
            console.print()
            console.print(f"  [bold]模型信息[/bold]")
            console.print("  " + "─" * 40)
            console.print(f"  名称：{model}")
            console.print(f"  家族：{family}")
            console.print(f"  参数：{params}")
            console.print(f"  量化：{quant}")

            # Detect reasoning
            try:
                from kaiwu.llm.llama_backend import LLMBackend
                is_reasoning = LLMBackend._check_reasoning_model(model)
                console.print(f"  推理：{'✅ reasoning模型' if is_reasoning else '标准模型'}")
            except Exception:
                pass

            # Detect tier
            try:
                from kaiwu.core.model_capability import detect_model_tier
                tier = detect_model_tier(model, base_url)
                console.print(f"  能力：{tier.value}")
            except Exception:
                pass

            console.print()
            return
    except Exception:
        pass

    console.print(f"  [yellow]无法通过 Ollama API 探测 {model}[/yellow]")
    console.print(f"  [dim]请确认 {base_url} 可访问且模型已下载[/dim]")
    console.print()
