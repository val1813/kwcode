"""
Multi-language tree-sitter parser.
MVP: Python only. Other languages fall back gracefully.
Extended: Optional support for JavaScript, TypeScript, Go, Rust, Java.
"""

import logging
import os
from typing import Optional

import tree_sitter
import tree_sitter_python

logger = logging.getLogger(__name__)

# ── Optional language bindings (graceful fallback) ──────────────
_OPTIONAL_LANGUAGES = {}

try:
    import tree_sitter_javascript
    _OPTIONAL_LANGUAGES["javascript"] = tree_sitter_javascript
except ImportError:
    pass

try:
    import tree_sitter_typescript
    _OPTIONAL_LANGUAGES["typescript"] = tree_sitter_typescript
except ImportError:
    pass

try:
    import tree_sitter_go
    _OPTIONAL_LANGUAGES["go"] = tree_sitter_go
except ImportError:
    pass

try:
    import tree_sitter_rust
    _OPTIONAL_LANGUAGES["rust"] = tree_sitter_rust
except ImportError:
    pass

try:
    import tree_sitter_java
    _OPTIONAL_LANGUAGES["java"] = tree_sitter_java
except ImportError:
    pass


# ── Function definition node types per language ─────────────────
_FUNC_NODE_TYPES = {
    "python":     ["function_definition"],
    "javascript": ["function_declaration", "method_definition"],
    "typescript": ["function_declaration", "method_definition"],
    "go":         ["function_declaration", "method_declaration"],
    "rust":       ["function_item"],
    "java":       ["method_declaration", "constructor_declaration"],
}

# ── Function name extraction queries per language ───────────────
_FUNC_QUERIES = {
    "python": "(function_definition name: (identifier) @name parameters: (parameters) @params)",
    "javascript": [
        "(function_declaration name: (identifier) @name parameters: (formal_parameters) @params)",
        "(method_definition name: (property_identifier) @name parameters: (formal_parameters) @params)",
    ],
    "typescript": [
        "(function_declaration name: (identifier) @name parameters: (formal_parameters) @params)",
        "(method_definition name: (property_identifier) @name parameters: (formal_parameters) @params)",
    ],
    "go": [
        "(function_declaration name: (identifier) @name parameters: (parameter_list) @params)",
        "(method_declaration name: (field_identifier) @name parameters: (parameter_list) @params)",
    ],
    "rust": "(function_item name: (identifier) @name parameters: (parameters) @params)",
    "java": "(method_declaration name: (identifier) @name parameters: (formal_parameters) @params)",
}

# ── Call queries per language ───────────────────────────────────
_CALL_QUERIES = {
    "python": [
        "(call function: (identifier) @name)",
        "(call function: (attribute attribute: (identifier) @name))",
    ],
    "javascript": [
        "(call_expression function: (identifier) @name)",
        "(call_expression function: (member_expression property: (property_identifier) @name))",
    ],
    "typescript": [
        "(call_expression function: (identifier) @name)",
        "(call_expression function: (member_expression property: (property_identifier) @name))",
    ],
    "go": [
        "(call_expression function: (identifier) @name)",
        "(call_expression function: (selector_expression field: (field_identifier) @name))",
    ],
    "rust": [
        "(call_expression function: (identifier) @name)",
        "(call_expression function: (field_expression field: (field_identifier) @name))",
    ],
    "java": [
        "(method_invocation name: (identifier) @name)",
    ],
}

# ── Class node types per language ──────────────────────────────
_CLASS_NODE_TYPES = {
    "python":     "class_definition",
    "javascript": "class_declaration",
    "typescript": "class_declaration",
    "go":         None,  # Go has no classes
    "rust":       None,  # Rust uses impl blocks
    "java":       "class_declaration",
}


class TreeSitterParser:
    """Parse source files using tree-sitter."""

    SUPPORTED = {
        "python": tree_sitter_python,
        **_OPTIONAL_LANGUAGES,
    }

    # File extension -> language key
    EXT_MAP = {
        ".py": "python",
        ".js": "javascript",
        ".mjs": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
    }

    def __init__(self):
        self._parsers: dict[str, tree_sitter.Parser] = {}
        self._languages: dict[str, tree_sitter.Language] = {}
        for lang_key, mod in self.SUPPORTED.items():
            try:
                # tree-sitter-typescript has .language_typescript() instead of .language()
                if lang_key == "typescript" and hasattr(mod, "language_typescript"):
                    lang = tree_sitter.Language(mod.language_typescript())
                else:
                    lang = tree_sitter.Language(mod.language())
                self._languages[lang_key] = lang
                self._parsers[lang_key] = tree_sitter.Parser(lang)
            except Exception as e:
                logger.debug("Failed to init tree-sitter for %s: %s", lang_key, e)

    def supported_languages(self) -> list[str]:
        """Return list of languages with available parsers."""
        return list(self._parsers.keys())

    def _detect_language(self, filepath: str) -> Optional[str]:
        ext = os.path.splitext(filepath)[1].lower()
        lang = self.EXT_MAP.get(ext)
        # Only return if we actually have a parser for it
        if lang and lang in self._parsers:
            return lang
        return None

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

    def detect_file_language(self, filepath: str) -> Optional[str]:
        """Public method to detect language for a file path."""
        return self._detect_language(filepath)

    def extract_functions(self, tree: tree_sitter.Tree, source: bytes,
                          language: str = "python") -> list[dict]:
        """
        Extract all function/method definitions from a tree.
        Returns: [{"name": str, "start_line": int, "end_line": int, "params": list[str]}]
        """
        lang = self._languages.get(language)
        if lang is None:
            return []

        queries = _FUNC_QUERIES.get(language)
        if not queries:
            return []

        # Normalize to list
        if isinstance(queries, str):
            queries = [queries]

        results = []
        for q_str in queries:
            try:
                query = tree_sitter.Query(lang, q_str)
                cursor = tree_sitter.QueryCursor(query)

                for _, captures in cursor.matches(tree.root_node):
                    name_node = captures["name"][0]
                    params_node = captures.get("params", [None])[0]

                    func_name = name_node.text.decode("utf-8")
                    start_line = name_node.start_point[0] + 1

                    # Find the parent function node to get end_line
                    func_node = name_node.parent
                    end_line = func_node.end_point[0] + 1 if func_node else start_line

                    # Check if inside a class -> prefix with class name
                    class_name = self._find_enclosing_class(name_node, language)
                    qualified = f"{class_name}.{func_name}" if class_name else func_name

                    # Extract parameter names
                    params = self._extract_params(params_node, language) if params_node else []

                    results.append({
                        "name": qualified,
                        "start_line": start_line,
                        "end_line": end_line,
                        "params": params,
                    })
            except Exception as e:
                logger.debug("Query failed for %s [%s]: %s", language, q_str[:50], e)

        return results

    def extract_calls(self, tree: tree_sitter.Tree, source: bytes,
                      language: str = "python") -> list[dict]:
        """
        Extract all function calls from a tree.
        Returns: [{"name": str, "line": int, "in_function": str|None}]
        """
        lang = self._languages.get(language)
        if lang is None:
            return []

        queries = _CALL_QUERIES.get(language)
        if not queries:
            return []

        if isinstance(queries, str):
            queries = [queries]

        results = []
        for q_str in queries:
            try:
                query = tree_sitter.Query(lang, q_str)
                cursor = tree_sitter.QueryCursor(query)
                for _, captures in cursor.matches(tree.root_node):
                    node = captures["name"][0]
                    results.append({
                        "name": node.text.decode("utf-8"),
                        "line": node.start_point[0] + 1,
                        "in_function": self._find_enclosing_function(node, language),
                    })
            except Exception as e:
                logger.debug("Call query failed for %s: %s", language, e)

        return results

    def _find_enclosing_function(self, node: tree_sitter.Node,
                                  language: str = "python") -> Optional[str]:
        """Walk up the tree to find the enclosing function name."""
        func_types = set(_FUNC_NODE_TYPES.get(language, ["function_definition"]))
        p = node.parent
        while p is not None:
            if p.type in func_types:
                for child in p.children:
                    if child.type in ("identifier", "field_identifier", "property_identifier"):
                        class_name = self._find_enclosing_class(child, language)
                        fname = child.text.decode("utf-8")
                        return f"{class_name}.{fname}" if class_name else fname
            p = p.parent
        return None

    def _find_enclosing_class(self, node: tree_sitter.Node,
                               language: str = "python") -> Optional[str]:
        """Walk up the tree to find the enclosing class name."""
        class_type = _CLASS_NODE_TYPES.get(language)
        if not class_type:
            return None
        p = node.parent
        while p is not None:
            if p.type == class_type:
                for child in p.children:
                    if child.type in ("identifier", "type_identifier"):
                        return child.text.decode("utf-8")
            p = p.parent
        return None

    @staticmethod
    def _extract_params(params_node: tree_sitter.Node,
                        language: str = "python") -> list[str]:
        """Extract parameter names from a parameters node."""
        params = []
        if params_node is None:
            return params

        if language == "python":
            return TreeSitterParser._extract_params_python(params_node)
        elif language in ("javascript", "typescript"):
            return TreeSitterParser._extract_params_js(params_node)
        elif language == "go":
            return TreeSitterParser._extract_params_go(params_node)
        elif language == "rust":
            return TreeSitterParser._extract_params_rust(params_node)
        elif language == "java":
            return TreeSitterParser._extract_params_java(params_node)

        return params

    @staticmethod
    def _extract_params_python(params_node: tree_sitter.Node) -> list[str]:
        """Extract parameter names from Python parameters node."""
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
    def _extract_params_js(params_node: tree_sitter.Node) -> list[str]:
        """Extract parameter names from JS/TS formal_parameters node."""
        params = []
        for child in params_node.children:
            if child.type == "identifier":
                params.append(child.text.decode("utf-8"))
            elif child.type in ("required_parameter", "optional_parameter"):
                for c in child.children:
                    if c.type == "identifier":
                        params.append(c.text.decode("utf-8"))
                        break
            elif child.type == "rest_pattern":
                for c in child.children:
                    if c.type == "identifier":
                        params.append("..." + c.text.decode("utf-8"))
                        break
        return params

    @staticmethod
    def _extract_params_go(params_node: tree_sitter.Node) -> list[str]:
        """Extract parameter names from Go parameter_list node."""
        params = []
        for child in params_node.children:
            if child.type == "parameter_declaration":
                for c in child.children:
                    if c.type == "identifier":
                        params.append(c.text.decode("utf-8"))
        return params

    @staticmethod
    def _extract_params_rust(params_node: tree_sitter.Node) -> list[str]:
        """Extract parameter names from Rust parameters node."""
        params = []
        for child in params_node.children:
            if child.type == "parameter":
                for c in child.children:
                    if c.type == "identifier":
                        params.append(c.text.decode("utf-8"))
                        break
            elif child.type == "self_parameter":
                params.append("self")
        return params

    @staticmethod
    def _extract_params_java(params_node: tree_sitter.Node) -> list[str]:
        """Extract parameter names from Java formal_parameters node."""
        params = []
        for child in params_node.children:
            if child.type == "formal_parameter":
                for c in child.children:
                    if c.type == "identifier":
                        params.append(c.text.decode("utf-8"))
                        break
            elif child.type == "spread_parameter":
                for c in child.children:
                    if c.type == "identifier":
                        params.append("..." + c.text.decode("utf-8"))
                        break
        return params
