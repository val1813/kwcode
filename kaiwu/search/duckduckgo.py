"""
搜索引擎：DDG库为主，SearXNG为可选增强。
内网/离线环境静默降级，不报错不阻塞流水线。

网络保护原则：
- SearXNG 降为可选，不自动拉起 Docker
- DDG 库也不可用时返回空列表
- 任何网络异常静默处理，不抛出
"""

import logging
import os
import subprocess
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# SearXNG默认地址（install.sh/ps1自动部署的Docker容器）
DEFAULT_SEARXNG_URL = "http://localhost:8080"
CONTAINER_NAME = "kwcode-searxng"

# DDG库作为主搜索
try:
    from duckduckgo_search import DDGS
    HAS_DDGS = True
except ImportError:
    HAS_DDGS = False


def _is_search_enabled() -> bool:
    """检查搜索是否启用（config.yaml 中 search_enabled 字段）。"""
    from pathlib import Path
    # 环境变量优先
    env_val = os.environ.get("KWCODE_SEARCH_ENABLED", "").lower()
    if env_val in ("0", "false", "no", "off"):
        return False
    if env_val in ("1", "true", "yes", "on"):
        return True
    # 读 config
    for dirname in (".kwcode", ".kaiwu"):
        config_path = os.path.join(Path.home(), dirname, "config.yaml")
        if os.path.exists(config_path):
            try:
                import yaml
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                val = cfg.get("search_enabled")
                if val is not None:
                    return bool(val)
            except Exception:
                pass
    # 默认启用
    return True


def _get_searxng_url() -> str:
    """从config或环境变量读取SearXNG地址。"""
    import os
    url = os.environ.get("KWCODE_SEARXNG_URL", "")
    if url:
        return url.rstrip("/")

    # 读config
    from pathlib import Path
    for dirname in (".kwcode", ".kaiwu"):
        config_path = os.path.join(Path.home(), dirname, "config.yaml")
        if os.path.exists(config_path):
            try:
                import yaml
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                url = cfg.get("searxng_url", "")
                if url:
                    return url.rstrip("/")
            except Exception:
                pass

    return DEFAULT_SEARXNG_URL


def _searxng_available(url: str) -> bool:
    """快速检测SearXNG是否可用（缓存结果）。"""
    try:
        resp = httpx.get(f"{url}/healthz", timeout=2.0)
        return resp.status_code == 200
    except Exception:
        # healthz不存在的旧版本，试首页
        try:
            resp = httpx.head(url, timeout=2.0)
            return resp.status_code < 400
        except Exception:
            return False


def _try_start_searxng() -> bool:
    """尝试自动拉起SearXNG Docker容器。返回是否成功启动。"""
    # 1. 检查docker命令是否存在
    try:
        r = subprocess.run(
            ["docker", "info"],
            capture_output=True, timeout=5, text=True,
        )
        if r.returncode != 0:
            logger.info("[searxng] Docker未运行，跳过自动启动")
            return False
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.info("[searxng] Docker不可用，跳过自动启动")
        return False

    # 2. 容器存在但停了 → docker start
    try:
        r = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name=^{CONTAINER_NAME}$",
             "--format", "{{.Status}}"],
            capture_output=True, timeout=5, text=True,
        )
        status = r.stdout.strip()
        if status:
            # 容器存在
            if "Up" in status:
                logger.info("[searxng] 容器已在运行，等待就绪...")
            else:
                logger.info("[searxng] 容器已停止，正在启动...")
                subprocess.run(
                    ["docker", "start", CONTAINER_NAME],
                    capture_output=True, timeout=10,
                )
        else:
            # 3. 容器不存在 → docker run
            logger.info("[searxng] 容器不存在，正在创建...")
            subprocess.run(
                ["docker", "run", "-d",
                 "--name", CONTAINER_NAME,
                 "--restart", "always",
                 "-p", "8080:8080",
                 "searxng/searxng"],
                capture_output=True, timeout=60,
            )
    except (subprocess.TimeoutExpired, Exception) as e:
        logger.warning("[searxng] 自动启动失败: %s", e)
        return False

    # 4. 等待就绪（最多12秒）
    for _ in range(12):
        time.sleep(1)
        try:
            resp = httpx.get(f"{DEFAULT_SEARXNG_URL}/healthz", timeout=2.0)
            if resp.status_code == 200:
                break
        except Exception:
            pass

    # 5. 确保JSON格式已启用（SearXNG默认只允许html，API需要json）
    _ensure_json_format()

    # 6. 最终验证：尝试一次JSON API调用
    try:
        resp = httpx.get(
            f"{DEFAULT_SEARXNG_URL}/search",
            params={"q": "test", "format": "json"},
            timeout=5.0,
        )
        if resp.status_code == 200:
            logger.info("[searxng] 自动启动成功，JSON API可用")
            return True
        elif resp.status_code == 403:
            logger.warning("[searxng] JSON格式未启用，降级到DDG")
            return False
    except Exception:
        pass

    # healthz通过但API不行，也算部分成功
    try:
        resp = httpx.get(f"{DEFAULT_SEARXNG_URL}/healthz", timeout=2.0)
        if resp.status_code == 200:
            logger.info("[searxng] 自动启动成功（healthz正常）")
            return True
    except Exception:
        pass

    logger.warning("[searxng] 自动启动超时，降级到DDG")
    return False


def _ensure_json_format():
    """确保SearXNG容器的settings.yml里启用了json格式。"""
    try:
        # 检查当前formats配置
        r = subprocess.run(
            ["docker", "exec", CONTAINER_NAME,
             "grep", "-A2", "formats:", "/etc/searxng/settings.yml"],
            capture_output=True, timeout=5, text=True,
        )
        if "json" in r.stdout:
            return  # 已启用

        # 添加json格式
        subprocess.run(
            ["docker", "exec", CONTAINER_NAME,
             "sed", "-i", r"s/^    - html$/    - html\n    - json/",
             "/etc/searxng/settings.yml"],
            capture_output=True, timeout=5,
        )
        # 重启容器使配置生效
        subprocess.run(
            ["docker", "restart", CONTAINER_NAME],
            capture_output=True, timeout=15,
        )
        # 等待重启完成
        for _ in range(8):
            time.sleep(1)
            try:
                resp = httpx.get(f"{DEFAULT_SEARXNG_URL}/healthz", timeout=2.0)
                if resp.status_code == 200:
                    logger.info("[searxng] JSON格式已启用并重启完成")
                    return
            except Exception:
                pass
        logger.info("[searxng] JSON格式已添加，等待重启")
    except Exception as e:
        logger.debug("[searxng] 配置JSON格式失败: %s", e)


# Session-level cache
_searxng_ok: Optional[bool] = None


def search(query: str, max_results: int = 10, timeout: float = 10.0) -> list[dict]:
    """
    搜索入口：DDG为主，SearXNG为可选增强。
    内网/离线环境静默返回空列表，不报错不阻塞。
    返回 [{url, title, snippet}, ...]
    """
    global _searxng_ok

    # ── 搜索开关：离线用户可完全禁用 ──
    if not _is_search_enabled():
        logger.debug("[search] 搜索已禁用(search_enabled=false)")
        return []

    searxng_url = _get_searxng_url()

    # 首次检测SearXNG可用性（缓存整个session，不自动拉起Docker）
    if _searxng_ok is None:
        _searxng_ok = _searxng_available(searxng_url)
        if _searxng_ok:
            logger.info("[search] SearXNG可用: %s", searxng_url)
        else:
            logger.debug("[search] SearXNG不可用，使用DDG")

    # 并行搜索：SearXNG + DDG 同时跑，结果去重合并
    try:
        if _searxng_ok and HAS_DDGS:
            return _search_parallel(query, max_results, timeout, searxng_url)

        # 单引擎 fallback
        if _searxng_ok:
            results = _search_searxng(query, max_results, timeout, searxng_url)
            if results:
                return results

        return _search_ddg(query, max_results, timeout)
    except Exception as e:
        # 任何网络异常静默处理，返回空列表
        logger.debug("[search] 搜索异常(静默): %s", e)
        return []


def _search_parallel(query: str, max_results: int, timeout: float, searxng_url: str) -> list[dict]:
    """SearXNG + DDG 并行执行，按URL去重合并，提高召回率。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results_map: dict[str, dict] = {}  # url → result (dedup)

    def _run_searxng():
        return _search_searxng(query, max_results, timeout, searxng_url)

    def _run_ddg():
        return _search_ddg(query, max_results, timeout)

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="search_") as pool:
        futures = {
            pool.submit(_run_searxng): "searxng",
            pool.submit(_run_ddg): "ddg",
        }
        for future in as_completed(futures, timeout=timeout + 2):
            engine = futures[future]
            try:
                engine_results = future.result()
                for r in engine_results:
                    url = r.get("url", "")
                    if not url:
                        # Instant answers (no URL) always keep
                        results_map[f"_instant_{len(results_map)}"] = r
                    elif url not in results_map:
                        results_map[url] = r
            except Exception as e:
                logger.debug("[search] %s parallel failed: %s", engine, e)

    merged = list(results_map.values())[:max_results]
    logger.info("[search] 并行搜索合并 %d 条结果（去重后）", len(merged))
    return merged


def _search_searxng(query: str, max_results: int, timeout: float, base_url: str) -> list[dict]:
    """SearXNG JSON API搜索。"""
    try:
        resp = httpx.get(
            f"{base_url}/search",
            params={
                "q": query,
                "format": "json",
                "categories": "general",
                "language": "auto",
                "pageno": 1,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for r in data.get("results", [])[:max_results]:
            results.append({
                "url": r.get("url", ""),
                "title": r.get("title", ""),
                "snippet": r.get("content", ""),
            })

        # SearXNG还返回infobox/answers，非常适合天气等即时查询
        for ans in data.get("answers", []):
            if isinstance(ans, str) and ans.strip():
                results.insert(0, {"url": "", "title": "即时回答", "snippet": ans.strip()})
        for ib in data.get("infoboxes", []):
            content = ib.get("content", "")
            if content:
                results.insert(0, {"url": ib.get("url", ""), "title": ib.get("infobox", ""), "snippet": content})

        logger.info("[searxng] 返回 %d 条结果", len(results))
        return results
    except Exception as e:
        logger.warning("[searxng] 搜索失败: %s", e)
        return []


def _search_ddg(query: str, max_results: int, timeout: float) -> list[dict]:
    """DDG库fallback（SearXNG不可用时）。"""
    if not HAS_DDGS:
        logger.warning("[ddg] duckduckgo-search未安装，无法搜索")
        return []

    try:
        with DDGS() as ddgs:
            raw = ddgs.text(query, max_results=max_results)
        results = []
        for r in raw:
            results.append({
                "url": r.get("href", ""),
                "title": r.get("title", ""),
                "snippet": r.get("body", ""),
            })
        logger.info("[ddg] 返回 %d 条结果", len(results))
        return results
    except Exception as e:
        logger.warning("[ddg] 搜索失败: %s", e)
        return []
