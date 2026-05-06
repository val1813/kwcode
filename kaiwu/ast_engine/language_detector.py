"""
Multi-language project detector.
Scans project files by extension to determine primary language(s).
Results cached in .kaiwu/rig.json under "language_stats".
"""

import os
from typing import Optional

from kaiwu.ast_engine.graph_builder import SKIP_DIRS

SUPPORTED_LANGUAGES = {
    "python":     {".py"},
    "javascript": {".js", ".mjs"},
    "typescript": {".ts", ".tsx"},
    "go":         {".go"},
    "rust":       {".rs"},
    "java":       {".java"},
    "csharp":     {".cs"},
}

# Reverse map: extension -> language
_EXT_TO_LANG: dict[str, str] = {}
for _lang, _exts in SUPPORTED_LANGUAGES.items():
    for _ext in _exts:
        _EXT_TO_LANG[_ext] = _lang

# Project marker files that confirm a language
PROJECT_MARKERS = {
    "python":     {"pyproject.toml", "setup.py", "requirements.txt", "Pipfile"},
    "javascript": {"package.json"},
    "typescript": {"tsconfig.json"},
    "go":         {"go.mod"},
    "rust":       {"Cargo.toml"},
    "java":       {"pom.xml", "build.gradle", "build.gradle.kts"},
    "csharp":     {"*.csproj", "*.sln"},
}

# Test commands per language
TEST_COMMANDS = {
    "python":     "python -m pytest tests/ --tb=short -q",
    "javascript": "npx jest --ci --passWithNoTests",
    "typescript": "npx jest --ci --passWithNoTests",
    "go":         "go test ./...",
    "rust":       "cargo test 2>&1",
    "java":       "mvn test -q",
    "csharp":     "dotnet test --no-build -q",
}

# Syntax check commands per language
SYNTAX_CHECK_COMMANDS = {
    "python":     'python -m py_compile "{file}"',
    "go":         'go vet "{file}"',
    "rust":       "cargo check",
    "java":       'javac -d /tmp "{file}"',
}


def detect_project_languages(project_root: str) -> dict[str, int]:
    """
    Count files per language in the project.
    Returns: {"python": 42, "javascript": 15, ...} sorted by count descending.
    """
    counts: dict[str, int] = {}

    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            lang = _EXT_TO_LANG.get(ext)
            if lang:
                counts[lang] = counts.get(lang, 0) + 1

    # Sort by count descending
    return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))


def get_primary_language(project_root: str) -> str:
    """Return the dominant language in the project. Defaults to 'python'."""
    counts = detect_project_languages(project_root)
    if not counts:
        return "python"
    return next(iter(counts))


def detect_language_for_file(file_path: str) -> Optional[str]:
    """Detect language for a single file by extension."""
    ext = os.path.splitext(file_path)[1].lower()
    return _EXT_TO_LANG.get(ext)


def get_test_command(language: str) -> Optional[str]:
    """Return the test command for a given language."""
    return TEST_COMMANDS.get(language)


def get_syntax_check_command(language: str, file_path: str = "") -> Optional[str]:
    """Return the syntax check command for a given language."""
    template = SYNTAX_CHECK_COMMANDS.get(language)
    if template and "{file}" in template:
        return template.format(file=file_path)
    return template


def detect_project_marker(project_root: str) -> Optional[str]:
    """
    Detect project language from marker files (go.mod, Cargo.toml, etc.).
    More reliable than file counting for mixed-language projects.
    """
    root_files = set()
    try:
        root_files = set(os.listdir(project_root))
    except OSError:
        return None

    for lang, markers in PROJECT_MARKERS.items():
        for marker in markers:
            if "*" in marker:
                # Glob pattern (e.g., *.csproj)
                ext = marker.replace("*", "")
                if any(f.endswith(ext) for f in root_files):
                    return lang
            elif marker in root_files:
                return lang

    return None
