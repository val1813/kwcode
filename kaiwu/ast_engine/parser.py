"""
Multi-language tree-sitter parser.
MVP: Python only. Other languages fall back gracefully.
"""

import logging
import os
from typing import Optional

import tree_sitter
import tree_sitter_python

logger = logging.getLogger(__name__)


class TreeSitterParser:
    """Parse source files using tree-sitter."""

    SUPPORTED = {
        "python": tree_sitter_python,
    }

    # File extension -> language key
    EXT_MAP = {
        ".py": "python",
    }

    def __init__(self):
        self._parsers: dict[str, tree_sitter.Parser] = {}
        self._languages: dict[str, tree_sitter.Language] = {}
        for lang_key, mod in self.SUPPORTED.items():
            lang = tree_sitter.Language(mod.language())
            self._languages[lang_key] = lang
            self._parsers[lang_key] = tree_sitter.Parser(lang)

    def detect_language(self, filepath: str) -> Optional[str]:
        ext = os.path.splitext(filepath)[1].lower()
        return self.EXT_MAP.get(ext)

    def _detect_language(self, filepath: str) -> Optional[str]:
        return self.detect_language(filepath)

    def parse_file(self, filepath: str) -> Optional[tree_sitter.Tree]:
        """Parse a single file. Returns tree or None if unsupported."""
        lang = self._detect_language(filepath)
        if lang is None:
            return None
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                source = f.read()
            return self._parsers[lang].parse(source.encode("utf-8"))
        except Exception:
            logger.debug("Failed to parse %s", filepath)
            return None

    def parse_bytes(self, source: bytes, language: str = "python") -> Optional[tree_sitter.Tree]:
        """Parse raw bytes with explicit language. For testing."""
        parser = self._parsers.get(language)
        if parser is None:
            return None
        try:
            return parser.parse(source)
        except Exception:
            return None

    def get_language(self, lang_key: str = "python") -> Optional[tree_sitter.Language]:
        return self._languages.get(lang_key)

    def extract_functions(self, tree: tree_sitter.Tree, source: bytes,
                          language: str = "python") -> list[dict]:
        """
        Extract all function/method definitions from a tree.
        Returns: [{"name": str, "start_line": int, "end_line": int, "params": list[str]}]
        """
        lang = self._languages.get(language)
        if lang is None:
            return []

        query = tree_sitter.Query(
            lang,
            "(function_definition name: (identifier) @name "
            "parameters: (parameters) @params)"
        )
        cursor = tree_sitter.QueryCursor(query)
        results = []

        for _, captures in cursor.matches(tree.root_node):
            name_node = captures["name"][0]
            params_node = captures["params"][0]

            func_name = name_node.text.decode("utf-8")
            start_line = name_node.start_point[0] + 1

            # Find the function_definition node to get end_line
            func_node = name_node.parent
            end_line = func_node.end_point[0] + 1 if func_node else start_line

            # Check if inside a class -> prefix with class name
            class_name = self._find_enclosing_class(name_node)
            qualified = f"{class_name}.{func_name}" if class_name else func_name

            # Extract parameter names
            params = self._extract_params(params_node)

            results.append({
                "name": qualified,
                "start_line": start_line,
                "end_line": end_line,
                "params": params,
            })

        return results

    def extract_calls(self, tree: tree_sitter.Tree, source: bytes,
                      language: str = "python") -> list[dict]:
        """
        Extract all function calls from a tree.
        Returns: [{"name": str, "line": int, "in_function": str|None}]
        where in_function is the enclosing function name (None if top-level).
        """
        lang = self._languages.get(language)
        if lang is None:
            return []

        results = []

        # Direct calls: func(...)
        q1 = tree_sitter.Query(lang, "(call function: (identifier) @name)")
        c1 = tree_sitter.QueryCursor(q1)
        for _, captures in c1.matches(tree.root_node):
            node = captures["name"][0]
            results.append({
                "name": node.text.decode("utf-8"),
                "line": node.start_point[0] + 1,
                "in_function": self._find_enclosing_function(node),
            })

        # Attribute calls: obj.method(...)
        q2 = tree_sitter.Query(
            lang,
            "(call function: (attribute attribute: (identifier) @name))"
        )
        c2 = tree_sitter.QueryCursor(q2)
        for _, captures in c2.matches(tree.root_node):
            node = captures["name"][0]
            results.append({
                "name": node.text.decode("utf-8"),
                "line": node.start_point[0] + 1,
                "in_function": self._find_enclosing_function(node),
            })

        return results

    @staticmethod
    def _find_enclosing_function(node: tree_sitter.Node) -> Optional[str]:
        """Walk up the tree to find the enclosing function name."""
        p = node.parent
        while p is not None:
            if p.type == "function_definition":
                for child in p.children:
                    if child.type == "identifier":
                        # Also check for class context
                        class_name = TreeSitterParser._find_enclosing_class(child)
                        fname = child.text.decode("utf-8")
                        return f"{class_name}.{fname}" if class_name else fname
            p = p.parent
        return None

    @staticmethod
    def _find_enclosing_class(node: tree_sitter.Node) -> Optional[str]:
        """Walk up the tree to find the enclosing class name."""
        p = node.parent
        while p is not None:
            if p.type == "class_definition":
                for child in p.children:
                    if child.type == "identifier":
                        return child.text.decode("utf-8")
            p = p.parent
        return None

    @staticmethod
    def _extract_params(params_node: tree_sitter.Node) -> list[str]:
        """Extract parameter names from a parameters node."""
        params = []
        for child in params_node.children:
            if child.type == "identifier":
                params.append(child.text.decode("utf-8"))
            elif child.type in ("default_parameter", "typed_parameter",
                                "typed_default_parameter"):
                for c in child.children:
                    if c.type == "identifier":
                        params.append(c.text.decode("utf-8"))
                        break
            elif child.type == "list_splat_pattern":
                for c in child.children:
                    if c.type == "identifier":
                        params.append("*" + c.text.decode("utf-8"))
                        break
            elif child.type == "dictionary_splat_pattern":
                for c in child.children:
                    if c.type == "identifier":
                        params.append("**" + c.text.decode("utf-8"))
                        break
        return params
