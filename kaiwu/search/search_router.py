"""
SearchRouter: 意图感知搜索路由，零 key 默认可用。

分层架构：
  Layer 0：专项 API（零 key，最精准）
    - arxiv.org API → 研究论文
    - Semantic Scholar → 学术搜索
    - GitHub REST API → 开源代码（60次/小时）
    - PyPI JSON API → 包文档
    - Open-Meteo API → 天气数据
  Layer 1：DuckDuckGo（零 key，通用搜索）
  Layer 2：Tavily（可选 key，质量提升）

理论来源：
- ARCS retrieval-before-generation（arXiv:2504.20434）
- Wink 失败类型分类（arXiv:2602.17037）
"""

import logging
import re
from typing import Optional

import httpx

from kaiwu.core.network import get_httpx_kwargs

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0


def arxiv_search(query: str, max_results: int = 5) -> list[dict]:
    """arXiv API 搜索（零 key，无限制）。"""
    try:
        import urllib.parse
        q = urllib.parse.quote(query)
        url = f"http://export.arxiv.org/api/query?search_query=all:{q}&max_results={max_results}&sortBy=relevance"
        resp = httpx.get(url, timeout=_TIMEOUT, **get_httpx_kwargs())
        if resp.status_code != 200:
            return []
        # 简单 XML 解析
        results = []
        entries = re.findall(r'<entry>(.*?)</entry>', resp.text, re.DOTALL)
        for entry in entries[:max_results]:
            title = re.search(r'<title>(.*?)</title>', entry, re.DOTALL)
            summary = re.search(r'<summary>(.*?)</summary>', entry, re.DOTALL)
            link = re.search(r'<id>(.*?)</id>', entry)
            if title:
                results.append({
                    "title": title.group(1).strip().replace("\n", " "),
                    "content": (summary.group(1).strip()[:500] if summary else ""),
                    "url": link.group(1).strip() if link else "",
                })
        return results
    except Exception as e:
        logger.debug("[search_router] arxiv failed: %s", e)
        return []


def semantic_scholar_search(query: str, max_results: int = 5) -> list[dict]:
    """Semantic Scholar API（零 key，AI 相关性排序）。"""
    try:
        import urllib.parse
        q = urllib.parse.quote(query)
        url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={q}&limit={max_results}&fields=title,abstract,url"
        resp = httpx.get(url, timeout=_TIMEOUT, **get_httpx_kwargs())
        if resp.status_code != 200:
            return []
        data = resp.json()
        results = []
        for paper in data.get("data", [])[:max_results]:
            results.append({
                "title": paper.get("title", ""),
                "content": (paper.get("abstract") or "")[:500],
                "url": paper.get("url", ""),
            })
        return results
    except Exception as e:
        logger.debug("[search_router] semantic_scholar failed: %s", e)
        return []


def github_search(query: str, token: str = "", max_results: int = 5) -> list[dict]:
    """GitHub Code/Repo 搜索（零 key 60次/小时，有 token 5000次/小时）。"""
    try:
        import urllib.parse
        q = urllib.parse.quote(query)
        url = f"https://api.github.com/search/repositories?q={q}&sort=stars&per_page={max_results}"
        headers = {"Accept": "application/vnd.github.v3+json"}
        if token:
            headers["Authorization"] = f"token {token}"
        resp = httpx.get(url, headers=headers, timeout=_TIMEOUT, **get_httpx_kwargs())
        if resp.status_code != 200:
            return []
        data = resp.json()
        results = []
        for repo in data.get("items", [])[:max_results]:
            results.append({
                "title": repo.get("full_name", ""),
                "content": (repo.get("description") or "")[:300],
                "url": repo.get("html_url", ""),
            })
        return results
    except Exception as e:
        logger.debug("[search_router] github failed: %s", e)
        return []


def pypi_search(query: str) -> list[dict]:
    """PyPI JSON API（零 key，包信息查询）。"""
    try:
        # PyPI 没有搜索 API，但可以直接查包名
        package = query.strip().split()[0].lower().replace(" ", "-")
        url = f"https://pypi.org/pypi/{package}/json"
        resp = httpx.get(url, timeout=_TIMEOUT, **get_httpx_kwargs())
        if resp.status_code != 200:
            return []
        data = resp.json()
        info = data.get("info", {})
        return [{
            "title": f"{info.get('name', '')} {info.get('version', '')}",
            "content": (info.get("summary", "") + "\n" + (info.get("description", "") or ""))[:500],
            "url": info.get("project_url", "") or info.get("package_url", ""),
        }]
    except Exception as e:
        logger.debug("[search_router] pypi failed: %s", e)
        return []


def open_meteo_search(query: str) -> str:
    """Open-Meteo API（零 key，完全免费天气数据）。"""
    try:
        # 从 query 提取城市名，用 geocoding API 获取坐标
        city = re.sub(r'(天气|气温|温度|weather|forecast|的|查|看)', '', query).strip()
        if not city:
            city = "Beijing"

        # Geocoding
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1&language=zh"
        geo_resp = httpx.get(geo_url, timeout=_TIMEOUT, **get_httpx_kwargs())
        if geo_resp.status_code != 200:
            return ""
        geo_data = geo_resp.json()
        results = geo_data.get("results", [])
        if not results:
            return f"未找到城市: {city}"
        lat = results[0]["latitude"]
        lon = results[0]["longitude"]
        name = results[0].get("name", city)

        # Weather
        weather_url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code"
            f"&daily=temperature_2m_max,temperature_2m_min,weather_code"
            f"&timezone=auto&forecast_days=3"
        )
        w_resp = httpx.get(weather_url, timeout=_TIMEOUT, **get_httpx_kwargs())
        if w_resp.status_code != 200:
            return ""
        w_data = w_resp.json()
        current = w_data.get("current", {})
        daily = w_data.get("daily", {})

        # 格式化输出
        lines = [f"📍 {name} 天气"]
        if current:
            lines.append(f"当前: {current.get('temperature_2m', '?')}°C, "
                        f"湿度 {current.get('relative_humidity_2m', '?')}%, "
                        f"风速 {current.get('wind_speed_10m', '?')}km/h")
        if daily and daily.get("time"):
            lines.append("未来3天:")
            for i, date in enumerate(daily["time"][:3]):
                tmax = daily.get("temperature_2m_max", [None])[i]
                tmin = daily.get("temperature_2m_min", [None])[i]
                lines.append(f"  {date}: {tmin}~{tmax}°C")
        return "\n".join(lines)
    except Exception as e:
        logger.debug("[search_router] open_meteo failed: %s", e)
        return ""


def duckduckgo_search(query: str, max_results: int = 5) -> list[dict]:
    """DuckDuckGo 搜索（复用现有模块）。"""
    try:
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "content": r.get("body", "")[:500],
                    "url": r.get("href", ""),
                })
        return results
    except Exception as e:
        logger.debug("[search_router] ddg failed: %s", e)
        return []


def tavily_search(query: str, api_key: str, max_results: int = 5) -> str:
    """Tavily 搜索（需要 key，1000次/月免费）。"""
    try:
        url = "https://api.tavily.com/search"
        payload = {
            "api_key": api_key,
            "query": query,
            "max_results": max_results,
            "include_answer": True,
        }
        resp = httpx.post(url, json=payload, timeout=_TIMEOUT, **get_httpx_kwargs())
        if resp.status_code != 200:
            return ""
        data = resp.json()
        parts = []
        if data.get("answer"):
            parts.append(f"[摘要] {data['answer']}")
        for r in data.get("results", [])[:3]:
            parts.append(f"[{r.get('title', '')}]({r.get('url', '')})\n{r.get('content', '')[:300]}")
        return "\n\n".join(parts)
    except Exception as e:
        logger.debug("[search_router] tavily failed: %s", e)
        return ""


class SearchRouter:
    """
    意图感知搜索路由：按任务类型选最精准的搜索源。
    零 key 默认可用，可选 key 提升质量。
    """

    def __init__(self, tavily_key: str = "", github_token: str = ""):
        self._tavily = tavily_key
        self._github_token = github_token

    def search(self, query: str, intent: str,
               error_context: Optional[dict] = None) -> str:
        """
        按意图路由搜索。
        Args:
            query: 搜索词
            intent: 意图类型 (research/code_solution/code_example/weather/library_doc/general)
            error_context: 错误上下文（可选）
        Returns:
            格式化的搜索结果文本
        """
        if intent == "research":
            results = arxiv_search(query)
            if not results:
                results = semantic_scholar_search(query)
            return self._format(results)

        elif intent == "code_solution":
            # 错误驱动搜索
            results = github_search(query, token=self._github_token)
            if not results:
                results = duckduckgo_search(query + " site:stackoverflow.com")
            return self._format(results)

        elif intent == "code_example":
            return self._format(github_search(query, token=self._github_token))

        elif intent == "weather":
            return open_meteo_search(query)

        elif intent == "library_doc":
            results = pypi_search(query)
            if not results:
                results = github_search(query, token=self._github_token)
            return self._format(results)

        else:  # general / realtime
            if self._tavily:
                return tavily_search(query, self._tavily)
            return self._format(duckduckgo_search(query))

    def _format(self, results: list[dict]) -> str:
        """格式化搜索结果为 LLM 可读文本。"""
        if not results:
            return ""
        return "\n\n".join(
            f"[{r.get('title', '')}]({r.get('url', '')})\n{r.get('content', '')[:500]}"
            for r in results[:3]
        )
