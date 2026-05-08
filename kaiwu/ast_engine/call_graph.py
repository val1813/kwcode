"""
Call graph built from tree-sitter analysis.
Uses simple dicts instead of networkx — the graph is small enough.
"""

import logging
import os
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)

SKIP_DIRS = {".git", "__pycache__", "node_modules", "venv", ".venv", ".eggs", ".tox", "dist", "build"}


class CallGraph:
    """Function call graph built from tree-sitter analysis."""

    def __init__(self):
        self._callers: dict[str, set[str]] = {}   # func -> set of functions it calls
        self._callees: dict[str, set[str]] = {}    # func -> set of functions that call it
        self._locations: dict[str, dict] = {}       # func -> {"file": str, "start_line": int, "end_line": int}

    def add_function(self, name: str, file: str, start_line: int, end_line: int):
        """Register a function definition."""
        self._callers.setdefault(name, set())
        self._callees.setdefault(name, set())
        self._locations[name] = {
            "file": file,
            "start_line": start_line,
            "end_line": end_line,
        }

    def add_call(self, caller: str, callee: str):
        """Register a function call relationship."""
        self._callers.setdefault(caller, set()).add(callee)
        self._callees.setdefault(callee, set()).add(caller)

    @property
    def functions(self) -> list[str]:
        return list(self._locations.keys())

    def get_location(self, name: str) -> Optional[dict]:
        return self._locations.get(name)

    def get_related(self, entry_func: str, depth: int = 2) -> list[dict]:
        """
        Get functions related to entry_func within `depth` hops.
        Expands both callers (who calls entry) and callees (what entry calls).
        Returns: [{"name": str, "file": str, "start_line": int, "relation": str}]
        """
        if entry_func not in self._locations:
            return []

        visited: dict[str, str] = {entry_func: "entry"}
        queue: deque[tuple[str, int]] = deque([(entry_func, 0)])

        while queue:
            current, d = queue.popleft()
            if d >= depth:
                continue

            # Functions that current calls
            for callee in self._callers.get(current, ()):
                if callee not in visited and callee in self._locations:
                    visited[callee] = "callee"
                    queue.append((callee, d + 1))

            # Functions that call current
            for caller in self._callees.get(current, ()):
                if caller not in visited and caller in self._locations:
                    visited[caller] = "caller"
                    queue.append((caller, d + 1))

        results = []
        for name, relation in visited.items():
            loc = self._locations.get(name)
            if loc:
                results.append({
                    "name": name,
                    "file": loc["file"],
                    "start_line": loc["start_line"],
                    "relation": relation,
                })
        return results

    def find_by_keyword(self, keyword: str) -> list[str]:
        """Find functions whose name contains the keyword (case-insensitive)."""
        kw = keyword.lower()
        return [name for name in self._locations if kw in name.lower()]

    @classmethod
    def build_from_project(cls, project_root: str, parser, max_files: int = 50) -> "CallGraph":
        """
        Build call graph for an entire project.
        Walks project directory, parses supported files, extracts functions and calls.
        """
        graph = cls()
        file_count = 0

        for dirpath, dirnames, filenames in os.walk(project_root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fname in sorted(filenames):
                if file_count >= max_files:
                    break
                language = parser.detect_language(fname)
                if language is None:
                    continue

                fpath = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(fpath, project_root).replace("\\", "/")

                tree = parser.parse_file(fpath)
                if tree is None:
                    continue

                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        source = f.read().encode("utf-8")
                except Exception:
                    continue

                file_count += 1

                # Extract and register functions
                functions = parser.extract_functions(tree, source, language)
                for func in functions:
                    graph.add_function(
                        name=func["name"],
                        file=rel_path,
                        start_line=func["start_line"],
                        end_line=func["end_line"],
                    )

                # Extract calls and build edges
                calls = parser.extract_calls(tree, source, language)
                for call in calls:
                    caller = call["in_function"]
                    callee = call["name"]
                    if caller:
                        graph.add_call(caller, callee)

        # Resolve unqualified call names to qualified names where possible
        graph._resolve_calls()

        logger.debug(
            "CallGraph built: %d functions, %d files",
            len(graph._locations), file_count,
        )
        return graph

    def _resolve_calls(self):
        """
        Resolve unqualified callee names to qualified names.
        E.g., a call to 'bar' might actually be 'Foo.bar' if that's the only 'bar' defined.
        """
        # Build short_name -> [qualified_names] index
        short_to_qualified: dict[str, list[str]] = {}
        for name in self._locations:
            short = name.split(".")[-1] if "." in name else name
            short_to_qualified.setdefault(short, []).append(name)

        # For each caller's callee set, try to resolve short names
        new_callers: dict[str, set[str]] = {}
        for caller, callees in self._callers.items():
            resolved = set()
            for callee in callees:
                if callee in self._locations:
                    resolved.add(callee)
                else:
                    # Try to resolve
                    candidates = short_to_qualified.get(callee, [])
                    if len(candidates) == 1:
                        resolved.add(candidates[0])
                    else:
                        resolved.add(callee)
            new_callers[caller] = resolved
        self._callers = new_callers

        # Rebuild callees index
        self._callees = {}
        for caller, callees in self._callers.items():
            for callee in callees:
                self._callees.setdefault(callee, set()).add(caller)
