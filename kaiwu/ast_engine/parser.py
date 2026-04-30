"""
Multi-language tree-sitter parser.
Supports Python and Go AST extraction; unsupported languages fall back gracefully.
"""

import logging
import os
from typing import Optional

import tree_sitter
import tree_sitter_go
import tree_sitter_python

logger = logging.getLogger(__name__)


class TreeSitterParser:
    """Parse source files using tree-sitter."""

    SUPPORTED = {
        "python": tree_sitter_python,
        "go": tree_sitter_go,
    }

    # File extension -> language key
    EXT_MAP = {
        ".py": "python",
        ".go": "go",
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
        if language == "go":
            return self._extract_go_functions(tree, lang)
        return self._extract_python_functions(tree, lang)

    def _extract_python_functions(self, tree: tree_sitter.Tree,
                                  lang: tree_sitter.Language) -> list[dict]:
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
            func_node = name_node.parent
            start_line = name_node.start_point[0] + 1
            end_line = func_node.end_point[0] + 1 if func_node else start_line
            class_name = self._find_enclosing_class(name_node)
            qualified = f"{class_name}.{func_name}" if class_name else func_name
            results.append({
                "name": qualified,
                "start_line": start_line,
                "end_line": end_line,
                "params": self._extract_params(params_node),
            })

        return results

    def _extract_go_functions(self, tree: tree_sitter.Tree,
                              lang: tree_sitter.Language) -> list[dict]:
        results = []
        queries = [
            "(function_declaration name: (identifier) @name "
            "parameters: (parameter_list) @params)",
            "(method_declaration name: (field_identifier) @name "
            "parameters: (parameter_list) @params)",
        ]
        for query_text in queries:
            query = tree_sitter.Query(lang, query_text)
            cursor = tree_sitter.QueryCursor(query)
            for _, captures in cursor.matches(tree.root_node):
                name_node = captures["name"][0]
                params_node = captures.get("params", [None])[0]
                func_node = name_node.parent
                func_name = name_node.text.decode("utf-8")
                receiver = self._extract_go_receiver_type(func_node)
                qualified = f"{receiver}.{func_name}" if receiver else func_name
                start_line = name_node.start_point[0] + 1
                end_line = func_node.end_point[0] + 1 if func_node else start_line
                results.append({
                    "name": qualified,
                    "start_line": start_line,
                    "end_line": end_line,
                    "params": self._extract_go_params(params_node) if params_node else [],
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
        query_texts = [
            "(call function: (identifier) @name)",
            "(call function: (attribute attribute: (identifier) @name))",
        ]
        if language == "go":
            query_texts = [
                "(call_expression function: (identifier) @name)",
                "(call_expression function: "
                "(selector_expression field: (field_identifier) @name))",
            ]

        for query_text in query_texts:
            query = tree_sitter.Query(lang, query_text)
            cursor = tree_sitter.QueryCursor(query)
            for _, captures in cursor.matches(tree.root_node):
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
                name_node = TreeSitterParser._first_child_of_type(p, "identifier")
                if name_node is not None:
                    class_name = TreeSitterParser._find_enclosing_class(name_node)
                    fname = name_node.text.decode("utf-8")
                    return f"{class_name}.{fname}" if class_name else fname
            if p.type in ("function_declaration", "method_declaration"):
                name_node = p.child_by_field_name("name")
                if name_node is not None:
                    fname = name_node.text.decode("utf-8")
                    receiver = TreeSitterParser._extract_go_receiver_type(p)
                    return f"{receiver}.{fname}" if receiver else fname
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
        """Extract parameter names from a Python parameters node."""
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

    @staticmethod
    def _extract_go_params(params_node: tree_sitter.Node) -> list[str]:
        """Extract named parameters from a Go parameter_list node."""
        params = []
        for child in params_node.children:
            if child.type != "parameter_declaration":
                continue
            names = TreeSitterParser._descendants_of_type(child, {"identifier"})
            type_nodes = TreeSitterParser._descendants_of_type(
                child,
                {"type_identifier", "qualified_type", "pointer_type", "slice_type", "array_type"},
            )
            type_text = {node.text.decode("utf-8") for node in type_nodes}
            for name_node in names:
                name = name_node.text.decode("utf-8")
                if name not in type_text:
                    params.append(name)
        return params

    @staticmethod
    def _extract_go_receiver_type(method_node: Optional[tree_sitter.Node]) -> Optional[str]:
        """Extract receiver type from a Go method_declaration node."""
        if method_node is None or method_node.type != "method_declaration":
            return None
        receiver = method_node.child_by_field_name("receiver")
        if receiver is None:
            return None
        candidates = TreeSitterParser._descendants_of_type(receiver, {"type_identifier"})
        if candidates:
            return candidates[-1].text.decode("utf-8")
        fallback = TreeSitterParser._descendants_of_type(receiver, {"identifier"})
        return fallback[-1].text.decode("utf-8") if fallback else None

    @staticmethod
    def _first_child_of_type(node: tree_sitter.Node, node_type: str) -> Optional[tree_sitter.Node]:
        for child in node.children:
            if child.type == node_type:
                return child
        return None

    @staticmethod
    def _descendants_of_type(node: tree_sitter.Node, node_types: set[str]) -> list[tree_sitter.Node]:
        matches = []
        stack = list(node.children)
        while stack:
            current = stack.pop(0)
            if current.type in node_types:
                matches.append(current)
            stack.extend(current.children)
        return matches
