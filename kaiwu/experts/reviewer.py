"""
Reviewer expert: 需求对齐验证（第二层回检）。
验证 Generator 输出是否真正满足用户意图，而不只是"能跑通"。

元专家体系第5个原子能力：
  Locator（定位）→ Generator（生成）→ Verifier（测试）→ Debugger（调试）→ Reviewer（审查）

Reviewer 在 Verifier 通过后执行，用 LLM 对比：
  - 用户原始意图
  - 实际代码变更
判断是否对齐，输出 {aligned: bool, gap: str}
"""

import json
import logging
import re
from typing import Optional

from kaiwu.core.context import TaskContext
from kaiwu.llm.llama_backend import LLMBackend

logger = logging.getLogger(__name__)

REVIEW_PROMPT = """判断代码修改是否满足需求。

需求：{user_input}

修改内容：
{changes}

{test_context}

判断：
1. 核心问题是否解决？（对照需求逐条检查）
2. 是否有遗漏？（需求要求了但没做的）
3. 修改的文件和函数是否正确？（改错文件=完全不对齐）

只输出JSON：
{{"aligned": true/false, "confidence": 0.0-1.0, "gap": "差距（一句话）"}}"""


class ReviewerExpert:
    """
    需求对齐审查。Verifier 通过后执行。
    用 LLM 对比用户意图和实际变更，判断是否真正完成了任务。
    非阻塞：审查失败不回滚，只记录 gap 供用户参考。
    """

    def __init__(self, llm: LLMBackend):
        self.llm = llm

    def review(self, ctx: TaskContext) -> dict:
        """
        审查 Generator 输出是否对齐用户需求。
        返回 {"aligned": bool, "confidence": float, "gap": str}
        失败时返回 {"aligned": True, "confidence": 0.0, "gap": ""} (乐观降级)
        """
        try:
            # 提取变更摘要
            changes = self._extract_changes(ctx)
            if not changes:
                return {"aligned": True, "confidence": 0.0, "gap": ""}

            # 测试上下文：让Reviewer看到测试期望
            test_ctx = ""
            initial_failure = getattr(ctx, 'initial_test_failure', '')
            if initial_failure:
                test_ctx = f"测试报错（修改前）：\n{initial_failure[:300]}"

            # LLM 审查
            prompt = REVIEW_PROMPT.format(
                user_input=ctx.user_input[:200],
                changes=changes[:800],
                test_context=test_ctx,
            )

            response = self.llm.generate(
                prompt=prompt,
                system="你是代码审查专家，只输出JSON判断，不要解释。",
                max_tokens=150,
                temperature=0.0,
            )

            # 解析结果
            return self._parse_response(response)

        except Exception as e:
            logger.warning("[reviewer] review failed: %s", e)
            return {"aligned": True, "confidence": 0.0, "gap": ""}

    @staticmethod
    def _extract_changes(ctx: TaskContext) -> str:
        """从 ctx 提取代码变更摘要。"""
        if not ctx.generator_output:
            return ""

        parts = []
        patches = ctx.generator_output.get("patches", [])
        for patch in patches[:3]:  # 最多看3个文件
            file_path = patch.get("file", "unknown")
            modified = patch.get("modified", "")[:300]
            original = patch.get("original", "")[:200]
            if modified:
                parts.append(f"文件: {file_path}\n修改后:\n{modified}")
                if original:
                    parts.append(f"修改前:\n{original}")

        explanation = ctx.generator_output.get("explanation", "")
        if explanation:
            parts.append(f"说明: {explanation[:200]}")

        return "\n\n".join(parts)

    @staticmethod
    def _parse_response(response: str) -> dict:
        """解析 LLM 的 JSON 审查结果。"""
        # 提取 JSON
        json_match = re.search(r'\{[^}]+\}', response)
        if not json_match:
            return {"aligned": True, "confidence": 0.0, "gap": ""}

        try:
            result = json.loads(json_match.group())
            return {
                "aligned": bool(result.get("aligned", True)),
                "confidence": float(result.get("confidence", 0.0)),
                "gap": str(result.get("gap", ""))[:100],
            }
        except (json.JSONDecodeError, ValueError, TypeError):
            return {"aligned": True, "confidence": 0.0, "gap": ""}
