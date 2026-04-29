"""
Gate: single LLM call, structured JSON output, routes to expert pipeline.
RED-1: Must output structured JSON, no string parsing.
v0.4.3: LLM通用分类为主，专家知识叠加（不替代）。
"""

import json
import logging
from typing import Optional, TYPE_CHECKING

from kaiwu.llm.llama_backend import LLMBackend
from kaiwu.core.orchestrator import EXPERT_SEQUENCES

if TYPE_CHECKING:
    from kaiwu.registry.expert_registry import ExpertRegistry

logger = logging.getLogger(__name__)

GATE_SYSTEM = "你是任务分类器。只返回JSON，不要有其他内容。"

GATE_PROMPT = """分析用户输入，返回分类JSON。

expert_type选项：
- locator_repair：修复bug、修改已有代码（用户明确提到已有文件路径如src/xxx.py）
- codegen：从零创建全新文件或全新项目（"写一个"、"生成"、"创建"开头的代码任务）
- refactor：重构、优化、整理已有代码结构（用户明确提到已有文件+重构/拆分/提取）
- doc：写注释、文档、README（仅限代码相关文档，用户明确提到已有文件+docstring/注释）
- office：仅限生成Excel(.xlsx)/Word(.docx)/PPT(.pptx)办公文档，不包括代码文件
- chat：问候、闲聊、非编码问题、询问天气、询问知识

difficulty选项：easy | hard（hard = 跨多文件/逻辑复杂/描述模糊）

关键区分规则：
- office仅用于Excel/Word/PPT，代码文件(.py/.js/.html/.css/.json/.go/.ts/.sh)一律不选office
- "写一个xxx.py/html/js/css/json/go/ts/sh" → codegen（不是office！）
- "修复src/xxx.py" → locator_repair
- "重构src/xxx.py" → refactor
- 不确定时优先选codegen或locator_repair，不要选office

示例：
- "你好" → {{"expert_type": "chat", "task_summary": "问候", "difficulty": "easy"}}
- "今天南京天气" → {{"expert_type": "chat", "task_summary": "问天气", "difficulty": "easy"}}
- "帮我修复登录bug" → {{"expert_type": "locator_repair", "task_summary": "修复登录", "difficulty": "easy"}}
- "修复src/parser.py中的IndexError" → {{"expert_type": "locator_repair", "task_summary": "修复越界", "difficulty": "easy"}}
- "重构src/reports.py提取公共函数" → {{"expert_type": "refactor", "task_summary": "提取函数", "difficulty": "easy"}}
- "写个排序函数" → {{"expert_type": "codegen", "task_summary": "排序函数", "difficulty": "easy"}}
- "写一个Flask API" → {{"expert_type": "codegen", "task_summary": "Flask API", "difficulty": "easy"}}
- "写一个app.py" → {{"expert_type": "codegen", "task_summary": "生成app", "difficulty": "easy"}}
- "生成一个config.json" → {{"expert_type": "codegen", "task_summary": "生成配置", "difficulty": "easy"}}
- "给这个函数写注释" → {{"expert_type": "doc", "task_summary": "写注释", "difficulty": "easy"}}
- "修复src/app.py的import错误" → {{"expert_type": "locator_repair", "task_summary": "修复import", "difficulty": "easy"}}
- "生成一个Excel报表" → {{"expert_type": "office", "task_summary": "Excel报表", "difficulty": "easy"}}

格式：{{"expert_type": "...", "task_summary": "10字内", "difficulty": "..."}}

用户输入：{user_input}"""

# JSON grammar constraint for llama.cpp
GATE_GRAMMAR = r'''
root   ::= "{" ws expert-type "," ws task-summary "," ws difficulty "}" ws
expert-type ::= "\"expert_type\"" ws ":" ws "\"" expert-val "\""
expert-val ::= "locator_repair" | "codegen" | "refactor" | "doc" | "office" | "chat"
task-summary ::= "\"task_summary\"" ws ":" ws string
difficulty ::= "\"difficulty\"" ws ":" ws ("\"easy\"" | "\"hard\"")
string ::= "\"" [^"]* "\""
ws ::= [ \t\n]*
'''

VALID_EXPERT_TYPES = {"locator_repair", "codegen", "refactor", "doc", "office", "chat"}
VALID_DIFFICULTIES = {"easy", "hard"}


class Gate:
    """Task classifier. Expert registry first, LLM fallback."""

    # Map expert pipeline to existing expert_type for orchestrator compatibility
    _PIPELINE_TO_TYPE = {
        ("locator", "generator", "verifier"): "locator_repair",
        ("generator", "verifier"): "codegen",
        ("locator", "generator"): "doc",
        ("generator",): "codegen",
        ("office",): "office",
        ("chat",): "chat",
    }

    def __init__(self, llm: LLMBackend, use_grammar: bool = False, registry: "ExpertRegistry | None" = None):
        self.llm = llm
        self.use_grammar = use_grammar
        self.registry = registry

    def classify(self, user_input: str, memory_context: str = "") -> dict:
        """
        Classify user input: LLM通用分类为主，专家知识为辅（叠加模式）。
        1. LLM通用分类 → expert_type (codegen/locator_repair/refactor/doc/chat)
        2. 专家关键词匹配 → 叠加领域知识(system_prompt)，不替代通用分类
        """
        # ── Step 1: LLM通用分类（始终执行，作为主分类结果）──
        prompt = GATE_PROMPT.format(user_input=user_input)
        if memory_context:
            prompt = f"项目记忆：\n{memory_context}\n\n{prompt}"

        grammar = GATE_GRAMMAR if self.use_grammar else None

        raw = self.llm.generate(
            prompt=prompt,
            system=GATE_SYSTEM,
            max_tokens=150,
            temperature=0.01,
            stop=["\n\n"],
            grammar_str=grammar,
        )

        result = self._parse(raw, user_input)
        result = self._postprocess(result, user_input)

        # ── Step 2: 专家关键词匹配（叠加模式，不替代通用分类）──
        result["expert_name"] = None
        result["route_type"] = "general"

        if self.registry:
            match = self.registry.match(user_input)
            if match:
                expert = match["expert"]
                expert_pipeline = tuple(expert["pipeline"])
                general_pipeline = tuple(
                    EXPERT_SEQUENCES.get(result["expert_type"], ["generator", "verifier"])
                )

                # 专家pipeline和通用分类一致 → 用专家（加载system_prompt）
                # 不一致 → 以通用分类为准，专家system_prompt作为附加知识注入
                result["expert_name"] = match["name"]
                result["confidence"] = match["confidence"]
                # Progressive disclosure: use instructions (SKILL.md) or system_prompt (YAML)
                result["system_prompt"] = expert.get("instructions") or expert.get("system_prompt", "")

                if expert_pipeline == general_pipeline:
                    # 完全一致：走专家路由
                    result["route_type"] = "expert_registry"
                    result["pipeline"] = list(expert_pipeline)
                else:
                    # 不一致：通用分类为主，专家知识为辅
                    result["route_type"] = "general_with_expert"
                    # 不覆盖pipeline，让orchestrator用通用的EXPERT_SEQUENCES

        return result

    @staticmethod
    def _postprocess(result: dict, user_input: str) -> dict:
        """最后一道防线：仅纠正office误分类。不替代模型分类能力。"""
        et = result.get("expert_type", "chat")
        lower = user_input.lower()

        # office仅限Excel/Word/PPT办公文档，代码任务不应走office
        if et == "office":
            # 只有明确提到办公文档格式才保留office
            _OFFICE_FORMATS = (".xlsx", ".docx", ".pptx", "excel", "word文档", "ppt模板", "幻灯片")
            if not any(fmt in lower for fmt in _OFFICE_FORMATS):
                result["expert_type"] = "chat"  # 降级到chat，让模型重新理解

        return result

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
        except (json.JSONDecodeError, ValueError, KeyError, AttributeError, TypeError) as e:
            logger.warning("Gate parse failed (raw=%r): %s", raw[:200], e)
            return {
                "expert_type": "chat",
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
