"""
Model capability detection and adaptive strategy.
P2-RED-1: Detection is local-only, no data sent to external servers.
"""

import logging
import re
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class ModelTier(Enum):
    SMALL = "small"    # <10B: gemma3:4b, qwen3:8b, deepseek-r1:8b
    MEDIUM = "medium"  # 10B-30B: qwen3:14b, qwen3:30b-a3b
    LARGE = "large"    # >30B: qwen3:72b, deepseek-r1:70b


@dataclass
class ModelStrategy:
    """Execution strategy determined by model tier."""
    tier: ModelTier
    gate_confidence_threshold: float
    force_plan_mode: bool
    max_files_per_task: int
    max_functions_per_task: int
    max_retries: int
    search_trigger_after: int
    complexity_warning_threshold: int


STRATEGIES = {
    ModelTier.SMALL: ModelStrategy(
        tier=ModelTier.SMALL,
        gate_confidence_threshold=0.90,
        force_plan_mode=True,
        max_files_per_task=2,
        max_functions_per_task=5,
        max_retries=3,
        search_trigger_after=1,
        complexity_warning_threshold=2,
    ),
    ModelTier.MEDIUM: ModelStrategy(
        tier=ModelTier.MEDIUM,
        gate_confidence_threshold=0.80,
        force_plan_mode=False,
        max_files_per_task=4,
        max_functions_per_task=10,
        max_retries=3,
        search_trigger_after=2,
        complexity_warning_threshold=4,
    ),
    ModelTier.LARGE: ModelStrategy(
        tier=ModelTier.LARGE,
        gate_confidence_threshold=0.70,
        force_plan_mode=False,
        max_files_per_task=8,
        max_functions_per_task=20,
        max_retries=3,
        search_trigger_after=2,
        complexity_warning_threshold=8,
    ),
}

# Known model lists for fallback detection
_KNOWN_SMALL = {"gemma3:4b", "gemma4:e2b", "phi3:mini", "qwen3:8b", "deepseek-r1:8b"}
_KNOWN_LARGE = {"qwen3:72b", "deepseek-r1:70b", "llama3:70b", "qwen3:110b"}

# Cache: model_name → ModelTier
_tier_cache: dict[str, ModelTier] = {}


def detect_model_tier(model_name: str, ollama_url: str = "http://localhost:11434") -> ModelTier:
    """
    Detect model capability tier.
    Priority: Ollama API parameter count → model name pattern → known list → default MEDIUM.
    P2-RED-1: All detection is local (ollama_url is localhost).
    """
    if model_name in _tier_cache:
        return _tier_cache[model_name]

    tier = _detect_from_api(model_name, ollama_url)
    if tier is None:
        tier = _detect_from_name(model_name)

    _tier_cache[model_name] = tier
    logger.info("[model_capability] %s → %s", model_name, tier.value)
    return tier


def _detect_from_api(model_name: str, ollama_url: str) -> ModelTier | None:
    """Try Ollama /api/show to get parameter count."""
    try:
        import httpx
        resp = httpx.post(
            f"{ollama_url}/api/show",
            json={"name": model_name},
            timeout=3,
        )
        if resp.status_code == 200:
            data = resp.json()
            params = data.get("modelinfo", {}).get("general.parameter_count", 0)
            if params > 0:
                params_b = params / 1e9
                if params_b < 10:
                    return ModelTier.SMALL
                elif params_b < 30:
                    return ModelTier.MEDIUM
                else:
                    return ModelTier.LARGE
    except Exception:
        pass
    return None


def _detect_from_name(model_name: str) -> ModelTier:
    """Fallback: infer tier from model name patterns (FLEX-1)."""
    name = model_name.lower()

    # Pattern: qwen3:8b, deepseek-r1:14b, llama3:70b
    numbers = re.findall(r'[:\-](\d+)b', name)
    if numbers:
        b = int(numbers[0])
        if b < 10:
            return ModelTier.SMALL
        elif b < 30:
            return ModelTier.MEDIUM
        else:
            return ModelTier.LARGE

    # Pattern: qwen3-8b (without colon)
    numbers2 = re.findall(r'(\d+)b', name)
    if numbers2:
        b = int(numbers2[0])
        if b < 10:
            return ModelTier.SMALL
        elif b < 30:
            return ModelTier.MEDIUM
        else:
            return ModelTier.LARGE

    # Known model lists
    if model_name in _KNOWN_SMALL:
        return ModelTier.SMALL
    if model_name in _KNOWN_LARGE:
        return ModelTier.LARGE

    # Default to MEDIUM
    return ModelTier.MEDIUM


def get_effective_ctx(model_name: str,
                      ollama_url: str = "http://localhost:11434") -> int:
    """
    获取当前模型实际可用的ctx大小。
    查询链：llama.cpp /props → vLLM /v1/models → Ollama modelinfo → 按tier默认值。
    失败全部静默，返回保守默认值。
    """
    import httpx

    # 1. llama.cpp /props → 运行时真实值，最准
    try:
        r = httpx.get("http://localhost:8080/props", timeout=2)
        if r.status_code == 200:
            n_ctx = r.json().get("n_ctx")
            if n_ctx and n_ctx > 0:
                return int(n_ctx * 0.8)
    except Exception:
        pass

    # 2. vLLM /v1/models → max_model_len
    try:
        vllm_url = ollama_url.replace("11434", "8000")
        r = httpx.get(f"{vllm_url}/v1/models", timeout=2)
        if r.status_code == 200:
            data = r.json().get("data", [])
            if data and "max_model_len" in data[0]:
                return int(data[0]["max_model_len"] * 0.8)
    except Exception:
        pass

    # 3. Ollama /api/show → modelinfo.llama.context_length（模型原生上限）
    try:
        r = httpx.post(
            f"{ollama_url}/api/show",
            json={"name": model_name},
            timeout=3,
        )
        if r.status_code == 200:
            data = r.json()
            native_ctx = data.get("modelinfo", {}).get("llama.context_length", 0)
            if native_ctx > 0:
                # 原生上限取80%，且不超过65536（避免本地推理速度崩）
                return min(int(native_ctx * 0.8), 65536)
    except Exception:
        pass

    # 4. 按tier给保守默认值
    tier = detect_model_tier(model_name, ollama_url)
    return {
        ModelTier.SMALL: 16384,
        ModelTier.MEDIUM: 32768,
        ModelTier.LARGE: 65536,
    }[tier]


def get_strategy(tier: ModelTier) -> ModelStrategy:
    """Get execution strategy for a given tier."""
    return STRATEGIES[tier]


def tier_display_name(tier: ModelTier) -> str:
    """Chinese display name for CLI."""
    return {
        ModelTier.SMALL: "小模型模式",
        ModelTier.MEDIUM: "中等模型",
        ModelTier.LARGE: "大模型模式",
    }[tier]
