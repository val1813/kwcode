"""
Gate: 确定性优先路由，LLM只做最后兜底。
决策优先级：特殊任务快速路由 → 测试gap路由 → 文件特征检测 → 关键词匹配 → LLM兜底二分类。
"""

import ast
import glob
import json
import logging
import os
from typing import Optional, TYPE_CHECKING

from kaiwu.llm.llama_backend import LLMBackend
from kaiwu.core.gap_detector import GapType, Gap, GAP_TO_EXPERT_TYPE

if TYPE_CHECKING:
    from kaiwu.registry.expert_registry import ExpertRegistry

logger = logging.getLogger(__name__)

__all__ = ["Gate"]

# 保留原有EXPERT_SEQUENCES引用（orchestrator定义）
from kaiwu.core.orchestrator import EXPERT_SEQUENCES

GATE_SYSTEM = "你是任务分类器。只返回JSON，不要有其他内容。"

# 保留GATE_PROMPT供向后兼容（旧测试引用）
GATE_PROMPT = """分析用户输入，返回分类JSON。

重要：如果项目存在.kaiwu/rig.json（仓库结构索引），你应该优先参考它来理解项目的文件结构、API路由和前后端对应关系，以便更准确地分类任务和拆分子任务。

expert_type选项：locator_repair/codegen/refactor/doc/office/chat/vision
用户输入：{user_input}"""

GATE_PROMPT_MINIMAL = """判断这个任务是"新建文件"还是"修改文件"，只输出一个JSON：
{{"action": "create"}} 或 {{"action": "modify"}}

任务：{user_input}"""

VALID_EXPERT_TYPES = {"locator_repair", "codegen", "refactor", "doc", "office",
                      "chat", "vision"}


class Gate:
    """确定性优先路由，LLM只做最后兜底二分类。"""

    _PIPELINE_TO_TYPE = {
        ("locator", "generator", "verifier"): "locator_repair",
        ("generator", "verifier"): "codegen",
        ("locator", "generator"): "doc",
        ("generator",): "codegen",
        ("office",): "office",
        ("chat",): "chat",
        ("vision",): "vision",
    }

    def __init__(self, llm: LLMBackend, use_grammar: bool = False,
                 registry: "ExpertRegistry | None" = None):
        self.llm = llm
        self.use_grammar = use_grammar
        self.registry = registry

    def classify(self, user_input: str, memory_context: str = "",
                 gap: Optional[Gap] = None) -> dict:
        """
        确定性优先路由。优先级从高到低：
        1. 特殊任务快速路由（确定性关键词）
        2. 测试gap路由（confidence >= 0.7）
        3. 文件特征检测（AST确定性）
        4. 关键词匹配
        5. LLM兜底（只做二分类）
        """
        lower = user_input.lower()

        # ══════════════════════════════════════
        # 优先级1：特殊任务快速路由（确定性关键词）
        # ══════════════════════════════════════

        # vision: 图片相关
        if "[图片:" in user_input or "[image:" in lower:
            return self._quick_route("vision", user_input, "keyword")

        # chat: 问候/闲聊/非编码
        chat_signals = ["你好", "hello", "hi", "什么是", "解释一下", "为什么",
                        "怎么理解", "帮我理解", "告诉我"]
        if any(s in lower for s in chat_signals) and not self._has_code_signal(lower):
            return self._quick_route("chat", user_input, "keyword")

        # office: 办公文档
        office_signals = [".xlsx", ".docx", ".pptx", "excel", "word文档",
                          "ppt", "幻灯片", "演示文稿", "汇报"]
        if any(s in lower for s in office_signals):
            return self._quick_route("office", user_input, "keyword")

        # ══════════════════════════════════════
        # 优先级2：测试gap路由（confidence >= 0.7）
        # ══════════════════════════════════════
        if gap and gap.gap_type not in (GapType.UNKNOWN, GapType.NONE) and gap.confidence >= 0.7:
            expert_type = GAP_TO_EXPERT_TYPE.get(gap.gap_type)
            if expert_type:
                result = {
                    "expert_type": expert_type,
                    "task_summary": user_input[:10],
                    "difficulty": self._compute_difficulty_from_gap(gap),
                    "routing_source": "gap_detector",
                    "confidence": gap.confidence,
                    "needs_search": False,
                    "subtask_hint": "",
                }
                # 消解用户意图和gap的冲突
                keyword_type = self._keyword_classify(lower)
                if keyword_type and keyword_type != expert_type:
                    resolved = self._resolve_intent_vs_gap(keyword_type, gap)
                    result["expert_type"] = resolved
                    if resolved != expert_type:
                        result["routing_source"] = "conflict_user_wins"
                return self._inject_registry(result, user_input)

        # ══════════════════════════════════════
        # 优先级3：文件特征检测（AST确定性）
        # ══════════════════════════════════════
        # 注：这里不做文件检测（需要project_root），由orchestrator在pre_test阶段通过gap完成

        # ══════════════════════════════════════
        # 优先级4：关键词匹配（保留现有逻辑）
        # ══════════════════════════════════════
        keyword_result = self._keyword_classify(lower)
        if keyword_result:
            confidence = self._keyword_confidence(lower, keyword_result)
            if confidence >= 0.75:
                result = {
                    "expert_type": keyword_result,
                    "task_summary": user_input[:10],
                    "difficulty": "easy",
                    "routing_source": "keyword",
                    "confidence": confidence,
                    "needs_search": self._needs_search(lower),
                    "subtask_hint": "",
                }
                return self._inject_registry(result, user_input)

        # ══════════════════════════════════════
        # 优先级5：LLM兜底（只做最简单的二分类）
        # ══════════════════════════════════════
        llm_result = self._llm_minimal_classify(user_input)
        # LLM返回的结果如果有_parse_error，保留chat降级行为（向后兼容）
        if "_parse_error" in llm_result:
            llm_result["routing_source"] = "llm_fallback"
            llm_result["confidence"] = 0.3
            return self._inject_registry(llm_result, user_input)
        llm_result["routing_source"] = "llm_fallback"
        llm_result["confidence"] = 0.55
        return self._inject_registry(llm_result, user_input)
        llm_result["confidence"] = 0.55
        return self._inject_registry(llm_result, user_input)

    def _quick_route(self, expert_type: str, user_input: str, source: str) -> dict:
        """快速路由返回结构。"""
        return {
            "expert_type": expert_type,
            "task_summary": user_input[:10],
            "difficulty": "easy",
            "routing_source": source,
            "confidence": 0.95,
            "needs_search": False,
            "subtask_hint": "",
            "expert_name": None,
            "route_type": "general",
        }

    def _has_code_signal(self, lower: str) -> bool:
        """检测是否有代码相关信号（避免误分类为chat）。"""
        code_signals = [".py", ".js", ".ts", ".go", ".rs", ".java",
                        "函数", "方法", "类", "接口", "bug", "修复",
                        "实现", "创建", "生成", "重构", "代码"]
        return any(s in lower for s in code_signals)

    def _keyword_classify(self, lower: str) -> Optional[str]:
        """关键词匹配分类。返回expert_type或None。"""
        # 修复/bug类
        repair_signals = ["修复", "fix", "bug", "报错", "错误", "失败",
                          "不工作", "broken", "error"]
        if any(s in lower for s in repair_signals):
            return "locator_repair"

        # 创建/生成类
        create_signals = ["写一个", "创建", "生成", "新建", "from scratch",
                          "写个", "实现一个", "generate", "create"]
        if any(s in lower for s in create_signals):
            return "codegen"

        # 重构类
        refactor_signals = ["重构", "优化", "整理", "拆分", "extract",
                            "rename", "refactor"]
        if any(s in lower for s in refactor_signals):
            return "refactor"

        # 文档类
        doc_signals = ["文档", "注释", "docstring", "readme", "doc"]
        if any(s in lower for s in doc_signals):
            return "doc"

        return None

    def _keyword_confidence(self, lower: str, expert_type: str) -> float:
        """根据关键词匹配强度估算置信度。"""
        STRONG_SIGNALS = {
            "locator_repair": ["修复", "fix", "bug", "报错", "错误", "失败", ".py:", "line "],
            "codegen": ["写一个", "创建", "生成", "新建", "from scratch", "写个"],
            "refactor": ["重构", "优化", "整理", "拆分", "extract", "rename"],
            "doc": ["文档", "注释", "docstring", "readme"],
        }
        signals = STRONG_SIGNALS.get(expert_type, [])
        matched = sum(1 for s in signals if s in lower)
        if matched >= 2:
            return 0.92
        elif matched == 1:
            return 0.75
        return 0.55

    def _resolve_intent_vs_gap(self, user_expert: str, gap: Gap) -> str:
        """
        解决用户意图和gap的冲突，返回最终expert_type。
        三层置信度消解：
        - confidence >= 0.85 → gap优先
        - 0.5-0.85 → 两者一致才用gap
        - < 0.5 → 用户意图优先
        """
        gap_expert = GAP_TO_EXPERT_TYPE.get(gap.gap_type, user_expert)

        if gap.confidence >= 0.85:
            return gap_expert

        if gap.confidence >= 0.5:
            # 两者一致才用gap
            return gap_expert if gap_expert == user_expert else user_expert

        return user_expert

    def _compute_difficulty_from_gap(self, gap: Gap) -> str:
        """从gap计算任务难度。"""
        if len(gap.files) > 2:
            return "hard"
        if gap.gap_type in (GapType.LOGIC_ERROR, GapType.NOT_IMPLEMENTED):
            return "hard"
        return "easy"

    def _needs_search(self, lower: str) -> bool:
        """检测是否需要实时搜索。"""
        search_signals = ["天气", "气温", "weather", "股价", "汇率",
                          "新闻", "最新", "最近", "today", "latest"]
        return any(s in lower for s in search_signals)

    def _llm_minimal_classify(self, user_input: str) -> dict:
        """LLM只做最简单的二分类：新建文件 vs 修改文件。"""
        prompt = GATE_PROMPT_MINIMAL.format(user_input=user_input[:200])

        try:
            raw = self.llm.generate(
                prompt=prompt,
                system=GATE_SYSTEM,
                max_tokens=30,
                temperature=0.0,
            )

            # 尝试解析JSON响应
            json_str = self._extract_json(raw)
            try:
                parsed = json.loads(json_str)
                if "action" in parsed:
                    if parsed["action"] == "create":
                        return {
                            "expert_type": "codegen",
                            "task_summary": user_input[:10],
                            "difficulty": "easy",
                            "needs_search": False,
                            "subtask_hint": "",
                        }
                    return {
                        "expert_type": "locator_repair",
                        "task_summary": user_input[:10],
                        "difficulty": "easy",
                        "needs_search": False,
                        "subtask_hint": "",
                    }
                # 兼容旧格式Gate JSON
                et = parsed.get("expert_type", "")
                if et in VALID_EXPERT_TYPES:
                    return {
                        "expert_type": et,
                        "task_summary": parsed.get("task_summary", user_input[:10]),
                        "difficulty": parsed.get("difficulty", "easy"),
                        "needs_search": bool(parsed.get("needs_search", False)),
                        "subtask_hint": str(parsed.get("subtask_hint", "")),
                    }
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

            # 简单文本匹配
            if "create" in raw.lower() or "新建" in raw:
                return {
                    "expert_type": "codegen",
                    "task_summary": user_input[:10],
                    "difficulty": "easy",
                    "needs_search": False,
                    "subtask_hint": "",
                }

            # 无法解析 → 返回带_parse_error的chat降级
            return {
                "expert_type": "chat",
                "task_summary": user_input[:10],
                "difficulty": "easy",
                "_parse_error": f"Cannot parse LLM output: {raw[:100]}",
            }

        except Exception as e:
            logger.debug("LLM classify failed: %s", e)
            return {
                "expert_type": "chat",
                "task_summary": user_input[:10],
                "difficulty": "easy",
                "_parse_error": str(e),
            }

    def _parse(self, raw: str, user_input: str) -> dict:
        """Parse and validate Gate output (backward compat for tests)."""
        json_str = self._extract_json(raw)
        try:
            result = json.loads(json_str)
            et = result.get("expert_type", "")
            if et not in VALID_EXPERT_TYPES:
                raise ValueError(f"Invalid expert_type: {et}")
            return {
                "expert_type": et,
                "task_summary": result.get("task_summary", user_input[:10]),
                "difficulty": result.get("difficulty", "easy"),
                "needs_search": bool(result.get("needs_search", False)),
                "subtask_hint": str(result.get("subtask_hint", "")),
            }
        except (json.JSONDecodeError, ValueError, KeyError, AttributeError, TypeError) as e:
            return {
                "expert_type": "chat",
                "task_summary": user_input[:10],
                "difficulty": "easy",
                "_parse_error": str(e),
            }

    @staticmethod
    def _extract_json(text: str) -> str:
        """Extract first JSON object from text."""
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start:end + 1]
        return text.strip()

    def _inject_registry(self, result: dict, user_input: str) -> dict:
        """注入专家注册表信息（保留现有逻辑）。"""
        result.setdefault("expert_name", None)
        result.setdefault("route_type", "general")

        if self.registry:
            match = self.registry.match(user_input)
            if match:
                expert = match["expert"]
                expert_pipeline = tuple(expert["pipeline"])
                general_pipeline = tuple(
                    EXPERT_SEQUENCES.get(result["expert_type"], ["generator", "verifier"])
                )

                result["expert_name"] = match["name"]
                if "confidence" not in result or match["confidence"] > result.get("confidence", 0):
                    result["confidence"] = match["confidence"]
                result["system_prompt"] = expert.get("instructions") or expert.get("system_prompt", "")

                if expert_pipeline == general_pipeline:
                    result["route_type"] = "expert_registry"
                    result["pipeline"] = list(expert_pipeline)
                else:
                    result["route_type"] = "general_with_expert"

        if "confidence" not in result:
            result["confidence"] = 0.55

        return result
