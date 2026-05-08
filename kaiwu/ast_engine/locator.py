"""
AST-based locator using tree-sitter call graph.
Strategy:
  1. Build call graph for the project
  2. Extract keywords from task description / error message
  3. Find entry functions matching keywords
  4. Expand along call graph (2 hops)
  5. Return candidate functions with file locations
"""

import logging
import re
from typing import Optional

from kaiwu.ast_engine.parser import TreeSitterParser
from kaiwu.ast_engine.call_graph import CallGraph

try:
    from kaiwu.ast_engine.ast_grep_backend import AST_GREP_AVAILABLE, AstGrepParser
except ImportError:  # pragma: no cover - fallback for minimal installs
    AST_GREP_AVAILABLE = False
    AstGrepParser = None

logger = logging.getLogger(__name__)

# Common Python builtins / noise words to skip during keyword matching
_SKIP_KEYWORDS = {
    "self", "cls", "none", "true", "false", "return", "import", "from",
    "class", "def", "if", "else", "for", "while", "try", "except",
    "with", "as", "in", "not", "and", "or", "is", "the", "a", "an",
    "print", "len", "str", "int", "list", "dict", "set", "type",
}


class ASTLocator:
    """AST-based locator using tree-sitter call graph."""

    def __init__(self, parser: Optional[TreeSitterParser] = None):
        if parser is not None:
            self.parser = parser
        elif AST_GREP_AVAILABLE and AstGrepParser is not None:
            self.parser = AstGrepParser()
        else:
            self.parser = TreeSitterParser()

    def locate(self, project_root: str, task_description: str,
               error_keywords: Optional[list[str]] = None) -> dict:
        """
        Locate relevant functions using AST call graph.
        Returns: {
            "relevant_files": list[str],
            "relevant_functions": list[str],
            "candidates": list[{"name": str, "file": str, "start_line": int, "relation": str}],
        }
        """
        graph = CallGraph.build_from_project(project_root, self.parser)

        # Extract keywords from task description
        keywords = self._extract_keywords(task_description)
        if error_keywords:
            keywords.extend([k.lower() for k in error_keywords])

        # Find entry functions matching keywords
        entry_funcs = set()
        for kw in keywords:
            entry_funcs.update(graph.find_by_keyword(kw))

        # If no keyword matches, try matching against file paths
        if not entry_funcs:
            for kw in keywords:
                for func_name in graph.functions:
                    loc = graph.get_location(func_name)
                    if loc and kw in loc["file"].lower():
                        entry_funcs.add(func_name)

        # Expand along call graph
        all_candidates = []
        seen = set()
        for entry in entry_funcs:
            related = graph.get_related(entry, depth=2)
            for r in related:
                if r["name"] not in seen:
                    seen.add(r["name"])
                    all_candidates.append(r)

        # Rank: entry > callee > caller, then by keyword relevance
        all_candidates.sort(key=lambda c: (
            {"entry": 0, "callee": 1, "caller": 2}.get(c["relation"], 3),
            -self._keyword_score(c["name"], keywords),
        ))

        # Deduplicate files preserving order
        relevant_files = []
        file_seen = set()
        for c in all_candidates:
            if c["file"] not in file_seen:
                file_seen.add(c["file"])
                relevant_files.append(c["file"])

        relevant_functions = [c["name"] for c in all_candidates]

        return {
            "relevant_files": relevant_files,
            "relevant_functions": relevant_functions,
            "candidates": all_candidates,
        }

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        """Extract meaningful keywords from task description."""
        # Split on non-alphanumeric (keep Chinese chars)
        tokens = re.findall(r"[a-zA-Z_]\w*", text.lower())
        # Filter noise
        keywords = [t for t in tokens if t not in _SKIP_KEYWORDS and len(t) > 2]
        # Map common Chinese terms to English function-name keywords
        cn_map = {
            "密码": "password",
            "登录": "login",
            "分页": "paginate",
            "上传": "upload",
            "缓存": "cache",
            "过期": "expire",
            "断开": "disconnect",
            "日期": "date",
            "时区": "timezone",
            "订单": "order",
            "库存": "stock",
            "配置": "config",
            "环境变量": "env",
            "邮件": "email",
            "附件": "attach",
            "导出": "export",
            "乱码": "encode",
            "校验": "verify",
            "发送": "send",
            "连接": "connect",
            "刷新": "refresh",
            "超卖": "deduct",
            "文件名": "filename",
        }
        # Substring match: check if any cn_map key appears in the text
        for cn_key, en_val in cn_map.items():
            if cn_key in text:
                keywords.append(en_val)

        return keywords

    @staticmethod
    def _keyword_score(func_name: str, keywords: list[str]) -> int:
        """Score how many keywords match the function name."""
        name_lower = func_name.lower()
        return sum(1 for kw in keywords if kw in name_lower)
