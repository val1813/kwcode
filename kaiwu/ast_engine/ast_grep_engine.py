"""
ast-grep engine: predefined query templates for multi-language code search.
LLM never generates ast-grep patterns directly — only fills parameters (function names, etc.).

Uses ast-grep CLI or ast-grep-py binding. Falls back gracefully when unavailable.
"""

import json
import logging
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

# Try to import ast-grep Python binding
try:
    from ast_grep_py import SgRoot
    AST_GREP_AVAILABLE = True
except ImportError:
    AST_GREP_AVAILABLE = False

# ── Predefined query templates ──────────────────────────────────
# LLM only fills $NAME, $MODULE etc. Never writes raw patterns.

QUERY_TEMPLATES = {
    "find_function": {
        "python":     "def $NAME($$$ARGS):\n    $$$BODY",
        "javascript": "function $NAME($$$ARGS) { $$$BODY }",
        "typescript": "function $NAME($$$ARGS): $RET { $$$BODY }",
        "go":         "func $NAME($$$ARGS) $$$RET { $$$BODY }",
        "rust":       "fn $NAME($$$ARGS) $$$RET { $$$BODY }",
        "java":       "$MOD $TYPE $NAME($$$ARGS) { $$$BODY }",
    },
    "find_class": {
        "python":     "class $NAME($$$PARENTS):\n    $$$BODY",
        "javascript": "class $NAME { $$$BODY }",
        "typescript": "class $NAME { $$$BODY }",
        "java":       "class $NAME { $$$BODY }",
        "csharp":     "class $NAME { $$$BODY }",
    },
    "find_imports": {
        "python":     "import $MODULE",
        "javascript": "import $WHAT from '$MODULE'",
        "typescript": "import $WHAT from '$MODULE'",
        "go":         '"$MODULE"',
        "rust":       "use $MODULE",
        "java":       "import $MODULE",
    },
    "find_from_import": {
        "python":     "from $MODULE import $WHAT",
    },
    "find_method_call": {
        "python":     "$OBJ.$METHOD($$$ARGS)",
        "javascript": "$OBJ.$METHOD($$$ARGS)",
        "typescript": "$OBJ.$METHOD($$$ARGS)",
        "go":         "$OBJ.$METHOD($$$ARGS)",
        "rust":       "$OBJ.$METHOD($$$ARGS)",
        "java":       "$OBJ.$METHOD($$$ARGS)",
    },
}

# Language name mapping for ast-grep CLI
_LANG_MAP = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "go": "go",
    "rust": "rust",
    "java": "java",
    "csharp": "csharp",
}


def _fill_template(template: str, params: Optional[dict] = None) -> str:
    """Fill a template with provided parameters. Unfilled params stay as meta-variables."""
    if not params:
        return template
    result = template
    for key, value in params.items():
        # Replace $KEY (single) and $$$KEY (variadic)
        result = result.replace(f"$$${key}", value)
        result = result.replace(f"${key}", value)
    return result


def query(pattern_key: str, lang: str, code: str,
          params: Optional[dict] = None) -> list[dict]:
    """
    Query code using a predefined template.

    Args:
        pattern_key: Template name (e.g., "find_function")
        lang: Language key (e.g., "python", "go")
        code: Source code to search
        params: Parameters to fill in template (e.g., {"NAME": "main"})

    Returns:
        List of matches: [{"text": str, "start": int, "end": int, "range": dict}]
    """
    templates = QUERY_TEMPLATES.get(pattern_key)
    if not templates:
        return []

    template = templates.get(lang)
    if not template:
        return []

    pattern = _fill_template(template, params)

    # Try Python binding first
    if AST_GREP_AVAILABLE:
        return _query_binding(pattern, lang, code)

    # Fall back to CLI
    return _query_cli(pattern, lang, code)


def _query_binding(pattern: str, lang: str, code: str) -> list[dict]:
    """Query using ast-grep-py binding."""
    try:
        sg_lang = _LANG_MAP.get(lang, lang)
        root = SgRoot(code, sg_lang)
        node = root.root()
        matches = node.find_all(pattern)
        results = []
        for m in matches:
            rng = m.range()
            results.append({
                "text": m.text(),
                "start_line": rng.start.line + 1,
                "end_line": rng.end.line + 1,
                "start_col": rng.start.column,
                "end_col": rng.end.column,
            })
        return results
    except Exception as e:
        logger.debug("ast-grep-py query failed: %s", e)
        return []


def _query_cli(pattern: str, lang: str, code: str) -> list[dict]:
    """Query using ast-grep CLI (subprocess)."""
    sg_lang = _LANG_MAP.get(lang, lang)
    try:
        result = subprocess.run(
            ["ast-grep", "run", "--pattern", pattern, "--lang", sg_lang,
             "--json", "--stdin"],
            input=code, capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        data = json.loads(result.stdout)
        results = []
        for item in data:
            rng = item.get("range", {})
            start = rng.get("start", {})
            end = rng.get("end", {})
            results.append({
                "text": item.get("text", ""),
                "start_line": start.get("line", 0) + 1,
                "end_line": end.get("line", 0) + 1,
                "start_col": start.get("column", 0),
                "end_col": end.get("column", 0),
            })
        return results
    except FileNotFoundError:
        logger.debug("ast-grep CLI not found")
        return []
    except subprocess.TimeoutExpired:
        logger.debug("ast-grep CLI timed out")
        return []
    except (json.JSONDecodeError, Exception) as e:
        logger.debug("ast-grep CLI error: %s", e)
        return []


def find_functions(file_path: str, language: str) -> list[dict]:
    """Find all function definitions in a file using ast-grep."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            code = f.read()
    except OSError:
        return []
    return query("find_function", language, code)


def find_classes(file_path: str, language: str) -> list[dict]:
    """Find all class definitions in a file using ast-grep."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            code = f.read()
    except OSError:
        return []
    return query("find_class", language, code)


def find_imports(file_path: str, language: str) -> list[dict]:
    """Find all import statements in a file using ast-grep."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            code = f.read()
    except OSError:
        return []
    results = query("find_imports", language, code)
    # Python also has "from X import Y"
    if language == "python":
        results.extend(query("find_from_import", language, code))
    return results


def is_available() -> bool:
    """Check if ast-grep is available (either binding or CLI)."""
    if AST_GREP_AVAILABLE:
        return True
    # Check CLI
    try:
        result = subprocess.run(
            ["ast-grep", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
