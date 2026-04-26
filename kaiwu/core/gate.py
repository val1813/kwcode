"""
Gate: single LLM call, structured JSON output, routes to expert pipeline.
RED-1: Must output structured JSON, no string parsing.
"""

import json
import logging
from typing import Optional

from kaiwu.llm.llama_backend import LLMBackend

logger = logging.getLogger(__name__)

GATE_SYSTEM = "你是任务分类器。只返回JSON，不要有其他内容。"

GATE_PROMPT = """分析用户输入，返回分类JSON。

expert_type选项：
- locator_repair：修复bug、修改已有代码、在已有文件中添加/删除函数
- codegen：从零创建全新文件或全新项目
- refactor：重构、优化、整理已有代码结构
- doc：写注释、文档、README
- office：生成Excel/Word/PPT等办公文档

difficulty选项：easy | hard（hard = 跨多文件/逻辑复杂/描述模糊）

注意：只要任务涉及已有文件，就选locator_repair或refactor，不要选codegen。
codegen仅用于"从零创建"的场景。

格式：{{"expert_type": "...", "task_summary": "10字内", "difficulty": "..."}}

用户输入：{user_input}"""

# JSON grammar constraint for llama.cpp (fallback if free-form JSON fails >5%)
GATE_GRAMMAR = r'''
root   ::= "{" ws expert-type "," ws task-summary "," ws difficulty "}" ws
expert-type ::= "\"expert_type\"" ws ":" ws "\"" expert-val "\""
expert-val ::= "locator_repair" | "codegen" | "refactor" | "doc" | "office"
task-summary ::= "\"task_summary\"" ws ":" ws string
difficulty ::= "\"difficulty\"" ws ":" ws ("\"easy\"" | "\"hard\"")
string ::= "\"" [^"]* "\""
ws ::= [ \t\n]*
'''

VALID_EXPERT_TYPES = {"locator_repair", "codegen", "refactor", "doc", "office"}
VALID_DIFFICULTIES = {"easy", "hard"}


class Gate:
    """Task classifier. One LLM call, deterministic routing."""

    def __init__(self, llm: LLMBackend, use_grammar: bool = False):
        self.llm = llm
        self.use_grammar = use_grammar

    def classify(self, user_input: str, memory_context: str = "") -> dict:
        """
        Classify user input into expert_type + difficulty.
        Returns dict with keys: expert_type, task_summary, difficulty.
        On parse failure, falls back to locator_repair/easy.
        """
        prompt = GATE_PROMPT.format(user_input=user_input)
        if memory_context:
            prompt = f"项目记忆：\n{memory_context}\n\n{prompt}"

        grammar = GATE_GRAMMAR if self.use_grammar else None

        raw = self.llm.generate(
            prompt=prompt,
            system=GATE_SYSTEM,
            max_tokens=150,
            temperature=0.0,
            stop=["\n\n"],
            grammar_str=grammar,
        )

        return self._parse(raw, user_input)

    def _parse(self, raw: str, user_input: str) -> dict:
        """Parse and validate Gate output. Fallback on any failure."""
        # Try to extract JSON from response (model might wrap it in text)
        json_str = self._extract_json(raw)
        try:
            result = json.loads(json_str)
            # Validate required fields
            et = result.get("expert_type", "")
            diff = result.get("difficulty", "")
            summary = result.get("task_summary", "")

            if et not in VALID_EXPERT_TYPES:
                raise ValueError(f"Invalid expert_type: {et}")
            if diff not in VALID_DIFFICULTIES:
                raise ValueError(f"Invalid difficulty: {diff}")

            return {
                "expert_type": et,
                "task_summary": summary[:20] if summary else user_input[:10],
                "difficulty": diff,
            }
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning("Gate parse failed (raw=%r): %s", raw[:200], e)
            return {
                "expert_type": "locator_repair",
                "task_summary": user_input[:10],
                "difficulty": "easy",
                "_parse_error": str(e),
            }

    @staticmethod
    def _extract_json(text: str) -> str:
        """Extract first JSON object from text."""
        # Find first { and last }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start:end + 1]
        return text.strip()
