"""
LLM backend: llama.cpp wrapper with Ollama-compatible HTTP fallback.
Provides a unified interface for all expert/gate LLM calls.
"""

import json
import logging
import os
import re
from typing import Optional

import httpx

from kaiwu.core.network import is_china_network

logger = logging.getLogger(__name__)


class BudgetExceededError(Exception):
    """Raised when token budget is exceeded."""
    pass

# Try importing llama_cpp; if unavailable, fall back to HTTP-only mode
try:
    from llama_cpp import Llama, LlamaGrammar
    HAS_LLAMA_CPP = True
except ImportError:
    HAS_LLAMA_CPP = False
    Llama = None
    LlamaGrammar = None


class LLMBackend:
    """Unified LLM interface supporting llama.cpp native and Ollama HTTP."""

    # Models known to use thinking/reasoning tokens that consume num_predict budget
    REASONING_PREFIXES = ("deepseek-r1", "qwq", "qwen3", "gemma4")
    # Multiplier for num_predict when using reasoning models
    REASONING_TOKEN_MULTIPLIER = 8

    # ModelScope model mapping for China network auto-switching
    MODELSCOPE_MODELS = {
        "deepseek-r1:8b": "deepseek-ai/DeepSeek-R1-Distill-Qwen-8B",
        "qwen3:8b": "Qwen/Qwen3-8B",
        "qwen3:14b": "Qwen/Qwen3-14B",
        "gemma3:4b": "google/gemma-3-4b-it",
    }

    def __init__(
        self,
        model_path: Optional[str] = None,
        ollama_url: str = "http://localhost:11434",
        ollama_model: str = "qwen3-8b",
        n_ctx: int = 8192,
        n_gpu_layers: int = -1,
        verbose: bool = False,
        api_key: str = "",
    ):
        self.ollama_url = ollama_url.rstrip("/")
        self.ollama_model = ollama_model
        self.api_key = api_key
        self.verbose = verbose
        self._llm: Optional[object] = None
        self._mode = "none"
        self._is_reasoning = self._detect_reasoning_model(ollama_model)
        self._tps_estimator = None  # set externally by CLI for tok/s tracking
        self._last_elapsed: float = 0.0  # last generate elapsed seconds
        # Detect if this is an OpenAI-compatible API (not Ollama)
        self._is_openai_compat = self._detect_openai_compat(ollama_url)
        # Token budget tracking
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0
        self._call_count: int = 0
        self._token_budget: int = 0  # 0 = unlimited

        # Prefer native llama.cpp if model_path provided and library available
        if model_path and HAS_LLAMA_CPP:
            try:
                self._llm = Llama(
                    model_path=model_path,
                    n_ctx=n_ctx,
                    n_gpu_layers=n_gpu_layers,
                    verbose=verbose,
                )
                self._mode = "llama_cpp"
                logger.info("LLM backend: llama.cpp native (model=%s)", model_path)
                return
            except Exception as e:
                logger.warning("llama.cpp init failed: %s, falling back to Ollama", e)

        # Fallback: Ollama HTTP API
        self._mode = "ollama"
        if self._is_openai_compat:
            logger.info("LLM backend: OpenAI-compatible HTTP (%s, model=%s)", self.ollama_url, self.ollama_model)
        else:
            logger.info("LLM backend: Ollama HTTP (%s, model=%s)", self.ollama_url, self.ollama_model)

    @staticmethod
    def _detect_openai_compat(url: str) -> bool:
        """
        Detect if the URL is an OpenAI-compatible API (not Ollama).
        Ollama runs on localhost:11434 and has /api/tags endpoint.
        Everything else is treated as OpenAI-compatible (/v1/chat/completions).
        """
        url_lower = url.lower()
        # Obvious Ollama indicators
        if "localhost:11434" in url_lower or "127.0.0.1:11434" in url_lower:
            return False
        # If URL contains /v1 already, it's OpenAI-compatible
        if "/v1" in url_lower:
            return True
        # If it's not localhost or has a known cloud domain, it's OpenAI-compatible
        cloud_indicators = [
            "api.deepseek.com", "api.siliconflow.cn", "api.openai.com",
            "api.groq.com", "api.together.xyz", "openrouter.ai",
            "dashscope.aliyuncs.com", "api.moonshot.cn", "api.lingyiwanwu.com",
        ]
        for indicator in cloud_indicators:
            if indicator in url_lower:
                return True
        # If it's not localhost at all, assume OpenAI-compatible
        if "localhost" not in url_lower and "127.0.0.1" not in url_lower:
            return True
        return False

    @property
    def token_usage(self) -> dict:
        """Return token usage stats for current session."""
        return {
            "input_tokens": self._total_input_tokens,
            "output_tokens": self._total_output_tokens,
            "total_tokens": self._total_input_tokens + self._total_output_tokens,
            "call_count": self._call_count,
        }

    def set_token_budget(self, budget: int):
        """Set max total tokens for this session. 0 = unlimited."""
        self._token_budget = budget

    def reset_token_usage(self):
        """Reset token counters (e.g. at start of new task)."""
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._call_count = 0

    def _track_tokens(self, input_tokens: int, output_tokens: int):
        """Track token usage. Raises BudgetExceededError if over budget."""
        self._total_input_tokens += input_tokens
        self._total_output_tokens += output_tokens
        self._call_count += 1
        if self._token_budget > 0:
            total = self._total_input_tokens + self._total_output_tokens
            if total > self._token_budget:
                raise BudgetExceededError(
                    f"Token budget exceeded: {total}/{self._token_budget}"
                )

    def ensure_model_available(self) -> None:
        """Check if model is pulled in Ollama; auto-switch to ModelScope on China networks."""
        if self._mode != "ollama":
            return

        # Query Ollama for available models
        try:
            resp = httpx.get(f"{self.ollama_url}/api/tags", timeout=10.0)
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
        except Exception as e:
            logger.warning("无法查询 Ollama 模型列表: %s", e)
            return

        # Check if current model is already available
        # Ollama tags list includes full "name:tag" entries; match exactly or
        # treat a tagless request (e.g. "qwen3") as matching "qwen3:latest".
        target = self.ollama_model
        for m in models:
            if m == target or m == f"{target}:latest":
                logger.debug("模型 %s 已存在于 Ollama", self.ollama_model)
                return

        # Model not found — try ModelScope if on China network
        if self.ollama_model not in self.MODELSCOPE_MODELS:
            logger.info("模型 %s 未找到，且不在 ModelScope 映射表中，跳过自动切换", self.ollama_model)
            return

        if not is_china_network():
            logger.debug("模型 %s 未找到，非国内网络，使用默认源拉取", self.ollama_model)
            return

        logger.info("模型未找到，检测到国内网络，尝试从 ModelScope 拉取...")
        os.environ["OLLAMA_MODELS"] = "https://modelscope.cn/models"
        logger.info("已设置 OLLAMA_MODELS=%s", os.environ["OLLAMA_MODELS"])

    def generate(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.0,
        stop: Optional[list[str]] = None,
        grammar_str: Optional[str] = None,
    ) -> str:
        """Generate text completion. Returns raw string output."""
        import time as _time
        t0 = _time.perf_counter()
        if self._mode == "llama_cpp":
            result = self._generate_native(prompt, system, max_tokens, temperature, stop, grammar_str)
        else:
            result = self._generate_ollama(prompt, system, max_tokens, temperature, stop)
        self._last_elapsed = _time.perf_counter() - t0
        if self._tps_estimator:
            self._tps_estimator.record(result, self._last_elapsed)
        return result

    def _generate_native(
        self, prompt: str, system: str, max_tokens: int,
        temperature: float, stop: Optional[list[str]], grammar_str: Optional[str],
    ) -> str:
        kwargs: dict = {
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if stop:
            kwargs["stop"] = stop
        if grammar_str and LlamaGrammar:
            kwargs["grammar"] = LlamaGrammar.from_string(grammar_str)

        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        resp = self._llm(full_prompt, **kwargs)
        return resp["choices"][0]["text"].strip()

    def _generate_ollama(
        self, prompt: str, system: str, max_tokens: int,
        temperature: float, stop: Optional[list[str]],
    ) -> str:
        # Always use /api/chat for Ollama — reasoning models (deepseek-r1 etc.)
        # consume num_predict budget with thinking tokens in /api/generate,
        # returning empty responses. /api/chat separates thinking from content.
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self._chat_ollama(messages, max_tokens, temperature, stop)

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """Strip <think>...</think> blocks from reasoning models (e.g. deepseek-r1)."""
        cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        return cleaned if cleaned else text

    def chat(
        self,
        messages: list[dict],
        max_tokens: int = 1024,
        temperature: float = 0.0,
        stop: Optional[list[str]] = None,
        grammar_str: Optional[str] = None,
    ) -> str:
        """Chat-style completion (for Ollama /api/chat or converted to prompt for llama.cpp)."""
        import time as _time
        t0 = _time.perf_counter()
        if self._mode == "ollama":
            result = self._chat_ollama(messages, max_tokens, temperature, stop)
        else:
            # Convert messages to single prompt for llama.cpp
            system = ""
            prompt_parts = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system":
                    system = content
                elif role == "user":
                    prompt_parts.append(f"User: {content}")
                elif role == "assistant":
                    prompt_parts.append(f"Assistant: {content}")
            prompt_parts.append("Assistant:")
            prompt = "\n".join(prompt_parts)
            result = self._generate_native(prompt, system, max_tokens, temperature, stop, grammar_str)
        self._last_elapsed = _time.perf_counter() - t0
        if self._tps_estimator:
            self._tps_estimator.record(result, self._last_elapsed)
        return result

    def _chat_ollama(
        self, messages: list[dict], max_tokens: int,
        temperature: float, stop: Optional[list[str]],
    ) -> str:
        # Route to OpenAI-compatible API if detected
        if self._is_openai_compat:
            return self._chat_openai_compat(messages, max_tokens, temperature, stop)

        effective_tokens = max_tokens
        effective_temp = temperature

        if self._is_reasoning:
            effective_tokens = max_tokens * self.REASONING_TOKEN_MULTIPLIER
            if temperature == 0.0:
                effective_temp = 0.01

        payload = {
            "model": self.ollama_model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_predict": effective_tokens,
                "temperature": effective_temp,
            },
        }

        if stop and not self._is_reasoning:
            payload["options"]["stop"] = stop

        try:
            resp = httpx.post(
                f"{self.ollama_url}/api/chat",
                json=payload,
                timeout=360.0,
            )
            resp.raise_for_status()
            data = resp.json()
            msg = data.get("message", {})
            if not msg:
                logger.error("Ollama response missing 'message' key: %s", str(data)[:200])
                return ""
            raw = msg.get("content", "").strip()

            if not raw and self._is_reasoning:
                thinking = msg.get("thinking", "")
                if thinking:
                    logger.info("content为空，从thinking字段提取（%d chars）", len(thinking))
                    raw = thinking.strip()

            # Track tokens (estimate for Ollama: ~4 chars per token)
            input_est = sum(len(m.get("content", "")) for m in messages) // 4
            output_est = len(raw) // 4
            self._track_tokens(input_est, output_est)

            return self._strip_thinking(raw)
        except BudgetExceededError:
            raise
        except Exception as e:
            logger.error("Ollama chat failed: %s", e)
            raise

    def _chat_openai_compat(
        self, messages: list[dict], max_tokens: int,
        temperature: float, stop: Optional[list[str]],
    ) -> str:
        """OpenAI-compatible API (/v1/chat/completions)."""
        effective_temp = temperature
        if self._is_reasoning and temperature == 0.0:
            effective_temp = 0.01

        # Build URL: if base already ends with /v1, don't double it
        base = self.ollama_url.rstrip("/")
        if base.endswith("/v1"):
            url = f"{base}/chat/completions"
        else:
            url = f"{base}/v1/chat/completions"

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.ollama_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": effective_temp,
            "stream": False,
        }

        if stop and not self._is_reasoning:
            payload["stop"] = stop

        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=360.0)
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                logger.error("OpenAI API response missing choices: %s", str(data)[:200])
                return ""
            raw = (choices[0].get("message", {}).get("content") or "").strip()

            # Track tokens (use API response if available, else estimate)
            usage = data.get("usage", {})
            input_tokens = usage.get("prompt_tokens", sum(len(m.get("content", "")) for m in messages) // 4)
            output_tokens = usage.get("completion_tokens", len(raw) // 4)
            self._track_tokens(input_tokens, output_tokens)

            return self._strip_thinking(raw)
        except BudgetExceededError:
            raise
        except Exception as e:
            logger.error("OpenAI-compatible API call failed: %s", e)
            raise

    @classmethod
    def _detect_reasoning_model(cls, model_name: str) -> bool:
        """Detect if model uses thinking/reasoning tokens. Uses prefix matching."""
        name_lower = model_name.lower().split(":")[0]  # strip tag like :8b
        return any(name_lower.startswith(p) for p in cls.REASONING_PREFIXES)

    def set_endpoint(self, base_url: str, api_key: str = "", model: str = None):
        """动态切换API endpoint和模型，支持/api temp和/model命令。"""
        self.ollama_url = base_url.rstrip("/")
        self.api_key = api_key
        self._mode = "ollama"
        if model:
            self.ollama_model = model
            self._is_reasoning = self._detect_reasoning_model(model)
        logger.info("[llm] endpoint切换到 %s model=%s reasoning=%s",
                    base_url, self.ollama_model, self._is_reasoning)
