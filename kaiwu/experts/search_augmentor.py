"""
SearchAugmentor expert: SearXNG统一搜索 + LLM提取。
搜索 → snippet收集 → LLM从snippet提取关键信息 → 返回精炼结果。

红线：
  SEARCH-RED-1: 零外部 API key（SearXNG本地Docker）
  SEARCH-RED-3: 失败不中断主流程，返回空字符串
  SEARCH-RED-4: 总耗时 ≤15s
"""

import logging
import re
import time

from kaiwu.core.context import TaskContext
from kaiwu.llm.llama_backend import LLMBackend
from kaiwu.search.duckduckgo import search as unified_search
from kaiwu.search.content_fetcher import ContentFetcher

logger = logging.getLogger(__name__)

MAX_SEARCH_SECONDS = 15

EXTRACT_PROMPT = """从以下搜索结果中提取与用户问题直接相关的事实信息。

用户问题：{query}

搜索结果：
{raw_results}

要求：
1. 只提取具体的事实、数据、数字（如温度、日期、价格等）
2. 去掉网站导航、广告、无关内容
3. 用简洁的中文列出关键信息
4. 如果搜索结果中没有相关信息，回复"未找到相关信息"
5. 不要编造任何数据，只提取搜索结果中存在的信息"""


class SearchAugmentorExpert:
    """搜索增强：SearXNG搜索 → LLM提取关键信息。"""

    def __init__(self, llm: LLMBackend):
        self.llm = llm
        self.fetcher = ContentFetcher()

    def search(self, ctx: TaskContext) -> str:
        """完整搜索流水线（供重试路径使用）。任何异常返回空字符串，不阻塞流水线。"""
        t0 = time.time()
        try:
            # 搜索开关检查
            from kaiwu.search.duckduckgo import _is_search_enabled
            if not _is_search_enabled():
                return ""
            query = ctx.user_input[:120]
            raw = self._search_and_collect(query, t0)
            if not raw:
                return ""
            # LLM提取关键信息
            return self._extract(query, raw)
        except Exception as e:
            logger.debug("[search] pipeline error (静默): %s", e)
            return ""

    def search_only(self, query: str) -> str:
        """简化搜索（供ChatExpert使用）。"""
        try:
            t0 = time.time()
            clean_q = self._clean_query(query)
            logger.info("[search_only] query: %s", clean_q)

            raw = self._search_and_collect(clean_q, t0)
            if not raw:
                return ""
            # LLM提取关键信息
            return self._extract(clean_q, raw)
        except Exception as e:
            logger.warning("[search_only] failed: %s", e)
            return ""

    def _search_and_collect(self, query: str, t0: float) -> str:
        """搜索并收集原始snippet+页面正文。BM25重排后取Top结果。"""
        results = unified_search(query, max_results=10)
        if not results:
            return ""

        # BM25 重排：用用户原始问题对搜索结果重打分
        results = self._rerank_results(query, results)

        # 收集snippet
        parts = []
        seen = set()
        for r in results[:8]:
            snippet = r.get("snippet", "").strip()
            title = r.get("title", "").strip()
            if not snippet or snippet in seen:
                continue
            seen.add(snippet)
            parts.append(f"【{title}】{snippet}" if title else snippet)

        snippet_text = "\n\n".join(parts)

        # 始终尝试fetch前2个URL补充正文（snippet经常是导航垃圾）
        if time.time() - t0 < MAX_SEARCH_SECONDS - 3:
            urls = [r["url"] for r in results[:3] if r.get("url")]
            if urls:
                remaining = max(3.0, MAX_SEARCH_SECONDS - (time.time() - t0))
                contents = self.fetcher.fetch_many(urls[:2], timeout=remaining)
                texts = [c for c in contents if c and len(c) > 50]
                if texts:
                    page_text = "\n\n---\n\n".join(texts)[:1500]
                    return (snippet_text + "\n\n---页面正文---\n\n" + page_text)[:3000]

        return snippet_text[:2000] if snippet_text else ""

    @staticmethod
    def _rerank_results(query: str, results: list[dict]) -> list[dict]:
        """BM25 rerank, then Cross-Encoder rerank if available (FLEX-2)."""
        if len(results) <= 1:
            return results
        # Stage 1: BM25 rerank
        try:
            from rank_bm25 import BM25Plus
            corpus = []
            for r in results:
                text = f"{r.get('title', '')} {r.get('snippet', '')}".lower()
                corpus.append(text.split())
            bm25 = BM25Plus(corpus)
            scores = bm25.get_scores(query.lower().split())
            ranked = sorted(
                zip(results, scores), key=lambda x: x[1], reverse=True
            )
            results = [r for r, _ in ranked]
        except Exception:
            pass

        # Stage 2: Cross-Encoder rerank (optional, FLEX-2)
        try:
            from kaiwu.search.reranker import rerank
            results = rerank(query, results, top_k=8)
        except Exception:
            pass

        return results

    def _extract(self, query: str, raw_results: str) -> str:
        """用LLM从原始搜索结果中提取关键信息。"""
        try:
            prompt = EXTRACT_PROMPT.format(
                query=query,
                raw_results=raw_results[:2500],
            )
            extracted = self.llm.generate(
                prompt=prompt,
                max_tokens=500,
                temperature=0.1,
            )
            extracted = extracted.strip()
            if extracted and len(extracted) > 10:
                logger.info("[search] LLM提取完成: %d字", len(extracted))
                return extracted
        except Exception as e:
            logger.warning("[search] LLM提取失败: %s", e)

        # LLM提取失败，降级返回原始snippet
        return raw_results[:1500]

    @staticmethod
    def _clean_query(raw: str) -> str:
        """清洗用户输入为搜索query：去问候语、语气词、指令词。"""
        q = raw.strip()
        for prefix in ("你好", "你好呀", "嗨", "hi", "hello", "帮我", "请",
                        "帮我搜索", "帮我查", "搜索一下", "搜一下", "查一下",
                        "帮我看下", "帮我看看", "我想知道", "我想了解",
                        "告诉我", "请问"):
            if q.startswith(prefix):
                q = q[len(prefix):].lstrip("，, ")
        q = re.sub(r'[？?！!。.~～]+$', '', q).strip()
        return q if len(q) >= 4 else raw.strip()
