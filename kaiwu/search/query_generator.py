"""
查询生成器：一次 LLM 调用，生成 2-3 条英文搜索 query。
LLM 自动决定 site: 限定（第一条 query），不需要额外 API。
意图影响 query 风格，不影响搜索引擎选择。
"""

import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kaiwu.core.context import TaskContext
    from kaiwu.llm.llama_backend import LLMBackend

logger = logging.getLogger(__name__)

QUERY_GEN_PROMPT = """\
You are a search query generator for a coding agent.
Given the user's coding task and intent, generate 2-3 concise English search queries.

Rules:
- Each query should be 5-12 words
- {direction}
- For the FIRST query: decide which site(s) would most likely have the answer,
  and append site restriction. Use your judgment — examples only:
  research/papers/frontier → site:arxiv.org OR site:semanticscholar.org
  open source code/models → site:github.com OR site:huggingface.co
  python packages → site:pypi.org OR site:docs.python.org
  general dev questions → site:stackoverflow.com
  If none applies clearly, do NOT add site restriction.
- For the remaining queries: broader searches without site restriction
- Output ONLY a JSON array of strings, no explanation.

User task: {task}
Verifier feedback: {feedback}
"""

# 意图 → query 方向提示
_DIRECTION_MAP = {
    "code_search": "Generate queries that will find code implementations, GitHub repos, or technical solutions",
    "academic": "Generate queries that will find research papers, algorithms, or theoretical foundations",
    "package": "Generate queries to find specific packages/libraries and their documentation",
    "debug": "Generate queries focused on error messages and fixes. Include the exact error text",
    "general": "Focus on practical coding solutions",
    "realtime": "Generate queries for real-time data (weather, prices, news). First query should target authoritative data sources",
    # Legacy mappings (backward compat)
    "github": "Include 'github' or 'repository' in at least one query",
    "arxiv": "Include 'arxiv' or 'paper' in at least one query",
    "pypi": "Include 'python package' or 'pip install' in at least one query",
    "bug": "Include 'fix' or 'solution' in at least one query",
}


class QueryGenerator:
    def __init__(self, llm: "LLMBackend" = None):
        self.llm = llm

    def generate(self, ctx_or_task, intent: str = "general") -> list[str]:
        """
        生成 2-3 条英文搜索 query。
        ctx_or_task: TaskContext 对象或纯字符串（兼容两种调用方式）。
        """
        # 兼容纯字符串调用（预搜索场景）
        if isinstance(ctx_or_task, str):
            task_text = ctx_or_task
            feedback = ""
        else:
            task_text = ctx_or_task.user_input
            feedback = ""
            if ctx_or_task.verifier_output and isinstance(ctx_or_task.verifier_output, dict):
                feedback = ctx_or_task.verifier_output.get("error_detail", "") or ""

        direction = _DIRECTION_MAP.get(intent, _DIRECTION_MAP["general"])

        # 如果没有 LLM，直接返回 fallback
        if not self.llm:
            return [task_text.strip()[:100] or "python coding help"]

        prompt = QUERY_GEN_PROMPT.format(
            direction=direction,
            task=task_text,
            feedback=str(feedback)[:500],
        )

        try:
            raw = self.llm.generate(prompt, max_tokens=256, temperature=0.3)
            queries = self._parse_queries(raw)
            if queries:
                # 安全过滤
                queries = [self._clean_query(q) for q in queries[:3]]
                return [q for q in queries if q]
        except Exception as e:
            logger.warning("Query generation LLM call failed: %s", e)

        # 回退：直接用 task 构造一条 query
        fallback = task_text.strip()[:100]
        return [fallback] if fallback else ["python coding help"]

    @staticmethod
    def _parse_queries(raw: str) -> list[str]:
        """从 LLM 输出中提取 JSON 数组。容忍 markdown 代码块包裹。"""
        # 去掉 markdown 代码块标记
        cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`")
        try:
            result = json.loads(cleaned)
            if isinstance(result, list):
                return [str(q).strip() for q in result if str(q).strip()]
        except json.JSONDecodeError:
            pass
        # 尝试逐行提取带引号的字符串
        lines = re.findall(r'"([^"]+)"', raw)
        return lines if lines else []

    @staticmethod
    def _clean_query(query: str) -> str:
        """
        安全过滤：去掉可能的 prompt injection 或无效字符。
        保留 site: 限定（这是合法的搜索语法）。
        """
        # 去掉控制字符
        query = re.sub(r'[\x00-\x1f\x7f]', '', query)
        # 去掉过长的 query（搜索引擎通常限制 256 字符）
        query = query[:256]
        # 去掉明显的 injection 尝试
        injection_patterns = [
            r'ignore\s+previous',
            r'system\s*:',
            r'<\|.*?\|>',
            r'\[INST\]',
        ]
        for pattern in injection_patterns:
            if re.search(pattern, query, re.IGNORECASE):
                logger.warning("[query_gen] Blocked suspicious query: %s", query[:50])
                return ""
        return query.strip()
