"""
ast-grep based structural extraction backend.
Provides the same parser interface used by GraphBuilder and CallGraph while
keeping the existing tree-sitter backend available as a fallback.
"""

import logging
import os
from typing import Optional

try:
    from ast_grep_py import SgRoot
    AST_GREP_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised when optional dep is absent
    SgRoot = None
    AST_GREP_AVAILABLE = False

logger = logging.getLogger(__name__)


class AstGrepParser:
    """Extract functions and calls with ast-grep structural patterns."""

    EXT_MAP = {
        ".py": "python",
        ".go": "go",
    }

    FUNCTION_PATTERNS = {
        "python": [
            "def $F($$$ARGS):\n    $$$BODY",
            "def $F($$$ARGS) -> $RET:\n    $$$BODY",
        ],
        "go": [
            "func $F($$$ARGS) $$$RET { $$$BODY }",
            "func ($$$RECV) $F($$$ARGS) $$$RET { $$$BODY }",
        ],
    }

    CALL_PATTERNS = {
        "python": [
            "$F($$$ARGS)",
            "$OBJ.$METHOD($$$ARGS)",
        ],
        "go": ["$F($$$ARGS)"],
    }

    def __init__(self):
        if not AST_GREP_AVAILABLE:
            raise ImportError("ast-grep-py is required for AstGrepParser")

    def detect_language(self, filepath: str) -> Optional[str]:
        ext = os.path.splitext(filepath)[1].lower()
        return self.EXT_MAP.get(ext)

    def parse_file(self, filepath: str):
        """Parse a single source file. Returns SgRoot or None if unsupported."""
        language = self.detect_language(filepath)
        if language is None:
            return None
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                return SgRoot(f.read(), language)
        except Exception as exc:
            logger.debug("ast-grep failed to parse %s: %s", filepath, exc)
            return None

    def parse_bytes(self, source: bytes, language: str = "python"):
        """Parse raw bytes with explicit language. For tests."""
        if language not in self.FUNCTION_PATTERNS:
            return None
        try:
            text = source.decode("utf-8", errors="replace")
            return SgRoot(text, language)
        except Exception:
            return None

    def extract_functions(self, tree, source: bytes, language: str = "python") -> list[dict]:
        """
        Extract function/method definitions.
        Returns: [{"name": str, "start_line": int, "end_line": int, "params": list[str]}]
        """
        patterns = self.FUNCTION_PATTERNS.get(language, [])
        if not tree or not patterns:
            return []

        root = tree.root()
        results = []
        seen = set()
        for pattern in patterns:
            for node in root.find_all(pattern=pattern):
                name_node = node.get_match("F")
                if name_node is None:
                    continue
                func_name = name_node.text()
                if language == "python":
                    owner = self._find_python_class_name(node)
                elif language == "go":
                    owner = self._extract_go_receiver_type(node)
                else:
                    owner = None
                qualified = f"{owner}.{func_name}" if owner else func_name
                key = (qualified, node.range().start.index, node.range().end.index)
                if key in seen:
                    continue
                seen.add(key)
                results.append({
                    "name": qualified,
                    "start_line": node.range().start.line + 1,
                    "end_line": node.range().end.line + 1,
                    "params": self._extract_params(node, language),
                })

        return results

    def extract_calls(self, tree, source: bytes, language: str = "python") -> list[dict]:
        """
        Extract function calls.
        Returns: [{"name": str, "line": int, "in_function": str|None}]
        """
        patterns = self.CALL_PATTERNS.get(language, [])
        if not tree or not patterns:
            return []

        root = tree.root()
        results = []
        seen = set()
        for pattern in patterns:
            for node in root.find_all(pattern=pattern):
                callee = self._extract_call_name(node, language)
                if not callee:
                    continue
                caller = self._find_enclosing_function(node, language)
                key = (callee, caller, node.range().start.index)
                if key in seen:
                    continue
                seen.add(key)
                results.append({
                    "name": callee,
                    "line": node.range().start.line + 1,
                    "in_function": caller,
                })

        return results

    @staticmethod
    def _extract_call_name(node, language: str) -> Optional[str]:
        method_node = node.get_match("METHOD")
        if method_node is not None:
            return method_node.text()
        name_node = node.get_match("F")
        if name_node is None:
            return None
        text = name_node.text()
        if language in {"python", "go"} and "." in text:
            return text.rsplit(".", 1)[-1]
        return text

    @classmethod
    def _find_enclosing_function(cls, node, language: str) -> Optional[str]:
        current = node.parent()
        while current is not None:
            if language == "python" and current.kind() == "function_definition":
                name = cls._first_child_text(current, {"identifier"})
                if name:
                    owner = cls._find_python_class_name(current)
                    return f"{owner}.{name}" if owner else name
            if language == "go" and current.kind() in {"function_declaration", "method_declaration"}:
                name = cls._first_child_text(current, {"identifier", "field_identifier"})
                if name:
                    owner = cls._extract_go_receiver_type(current)
                    return f"{owner}.{name}" if owner else name
            current = current.parent()
        return None

    @staticmethod
    def _find_python_class_name(node) -> Optional[str]:
        current = node.parent()
        while current is not None:
            if current.kind() == "class_definition":
                return AstGrepParser._first_child_text(current, {"identifier"})
            current = current.parent()
        return None

    @staticmethod
    def _extract_go_receiver_type(node) -> Optional[str]:
        if node.kind() != "method_declaration":
            return None
        receiver = None
        for child in node.children():
            if child.kind() == "parameter_list":
                receiver = child
                break
        if receiver is None:
            return None
        candidates = AstGrepParser._descendants_of_kind(receiver, {"type_identifier"})
        if candidates:
            return candidates[-1].text()
        fallback = AstGrepParser._descendants_of_kind(receiver, {"identifier"})
        return fallback[-1].text() if fallback else None

    @staticmethod
    def _extract_params(node, language: str) -> list[str]:
        params = []
        for param_node in node.get_multiple_matches("ARGS"):
            kind = param_node.kind()
            if kind in {",", "(", ")"}:
                continue
            name = AstGrepParser._extract_param_name(param_node, language)
            if name:
                params.append(name)
        return params

    @staticmethod
    def _extract_param_name(node, language: str) -> Optional[str]:
        if node.kind() == "identifier":
            return node.text()
        identifiers = AstGrepParser._descendants_of_kind(node, {"identifier"})
        if not identifiers:
            return None
        if language == "go":
            type_names = {
                n.text()
                for n in AstGrepParser._descendants_of_kind(
                    node,
                    {"type_identifier", "qualified_type", "pointer_type", "slice_type", "array_type"},
                )
            }
            for ident in identifiers:
                text = ident.text()
                if text not in type_names:
                    return text
        return identifiers[0].text()

    @staticmethod
    def _first_child_text(node, kinds: set[str]) -> Optional[str]:
        for child in node.children():
            if child.kind() in kinds:
                return child.text()
        return None

    @staticmethod
    def _descendants_of_kind(node, kinds: set[str]) -> list:
        matches = []
        stack = list(node.children())
        while stack:
            current = stack.pop(0)
            if current.kind() in kinds:
                matches.append(current)
            stack.extend(current.children())
        return matches
