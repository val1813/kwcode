"""
SearchSubagent: isolated context window for code search.
Wraps LocatorExpert but runs in its own context — the main Generator
never sees search intermediate states, only precise results.

Key design:
- Independent context: search noise never pollutes Generator's working memory
- Parallel execution: ThreadPoolExecutor, up to 8 concurrent file reads
- Returns only: {file, start_line, end_line, content} — minimal, precise
- Orchestrator sees clean results, not search process

Theory: WarpGrep (arXiv) — isolated search subagent reduces context rot 70%,
improves end-to-end completion speed 40%.
"""

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from kaiwu.core.context import TaskContext
from kaiwu.core.upstream_manifest import UpstreamManifest
from kaiwu.experts.locator import LocatorExpert
from kaiwu.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)

# Max parallel file reads per batch
MAX_PARALLEL_READS = 8
# Max lines per snippet returned to Generator
MAX_SNIPPET_LINES = 80


class SearchResult:
    """A single precise code location returned by SearchSubagent."""

    __slots__ = ("file", "start_line", "end_line", "content", "function_name")

    def __init__(self, file: str, start_line: int, end_line: int,
                 content: str, function_name: str = ""):
        self.file = file
        self.start_line = start_line
        self.end_line = end_line
        self.content = content
        self.function_name = function_name

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "content": self.content,
            "function_name": self.function_name,
        }


class SearchSubagent:
    """
    Isolated search subagent. Wraps LocatorExpert but:
    1. Runs in its own context (no shared state with Generator)
    2. Parallelizes file reads (ThreadPoolExecutor)
    3. Returns only precise {file, start_line, end_line, content}
    4. Main orchestrator never sees search intermediates
    """

    def __init__(self, locator: LocatorExpert, tool_executor: ToolExecutor):
        self._locator = locator
        self._tools = tool_executor

    def search(self, ctx: TaskContext, manifest: Optional[UpstreamManifest] = None) -> dict:
        """
        Execute search in isolated context. Returns clean results for Generator.

        Returns:
            {
                "relevant_files": [str, ...],
                "relevant_functions": [str, ...],
                "edit_locations": [str, ...],
                "code_snippets": {file_path: snippet_str, ...},
                "method": str,
                "upstream_constraints": str,  # From manifest, if available
            }
        """
        # ── Phase 1: Run locator (in its own context, results stay here) ──
        # Create a lightweight shadow context — locator writes to this, not the real ctx
        shadow_ctx = TaskContext(
            user_input=ctx.user_input,
            project_root=ctx.project_root,
            gate_result=ctx.gate_result,
            expert_system_prompt=ctx.expert_system_prompt,
            search_results=ctx.search_results,
            retry_count=ctx.retry_count,
            previous_failure=ctx.previous_failure,
            reflection=ctx.reflection,
        )

        # Run locator on shadow context
        locator_result = self._locator.run(shadow_ctx)

        if not locator_result:
            return {
                "relevant_files": [],
                "relevant_functions": [],
                "edit_locations": [],
                "code_snippets": {},
                "method": "none",
                "upstream_constraints": "",
            }

        # ── Phase 2: Parallel file reading for precise snippets ──
        files = locator_result.get("relevant_files", [])[:5]
        functions = locator_result.get("relevant_functions", [])

        # Parallel read: up to 8 files at once
        code_snippets = self._parallel_read_snippets(
            files, functions, ctx.project_root
        )

        # ── Phase 3: Get upstream constraints from manifest ──
        upstream_constraints = ""
        if manifest and files:
            constraint_parts = []
            for fpath in files:
                c = manifest.get_constraints_for_file(fpath)
                if c:
                    constraint_parts.append(c)
            upstream_constraints = "\n".join(constraint_parts)

        # ── Phase 4: Return clean, precise results ──
        # Only the structured output crosses the boundary to Generator
        return {
            "relevant_files": files,
            "relevant_functions": functions,
            "edit_locations": locator_result.get("edit_locations", []),
            "code_snippets": code_snippets,
            "method": locator_result.get("method", "unknown"),
            "upstream_constraints": upstream_constraints,
        }

    def _parallel_read_snippets(
        self,
        files: list[str],
        functions: list[str],
        project_root: str,
    ) -> dict[str, str]:
        """
        Read files in parallel and extract precise snippets.
        Returns {file_path: snippet_content} — only what Generator needs.
        """
        if not files:
            return {}

        results: dict[str, str] = {}
        lock = threading.Lock()

        def _read_and_extract(fpath: str) -> tuple[str, str]:
            """Read one file and extract relevant snippet."""
            content = self._tools.read_file(fpath)
            if not content or content.startswith("[ERROR]"):
                return fpath, ""

            # Extract precise snippet around target functions
            snippet = self._extract_precise_snippet(content, functions, fpath)
            return fpath, snippet

        # Parallel execution
        pool_size = min(len(files), MAX_PARALLEL_READS)
        with ThreadPoolExecutor(
            max_workers=pool_size,
            thread_name_prefix="search_subagent",
        ) as pool:
            futures = {
                pool.submit(_read_and_extract, fpath): fpath
                for fpath in files
            }
            for future in as_completed(futures):
                try:
                    fpath, snippet = future.result()
                    if snippet:
                        with lock:
                            results[fpath] = snippet
                except Exception as e:
                    logger.debug("[search_subagent] read failed: %s", e)

        return results

    def _extract_precise_snippet(
        self, content: str, functions: list[str], file_path: str
    ) -> str:
        """
        Extract minimal, precise code snippet around target functions.
        Includes line numbers for Generator reference.
        Cap at MAX_SNIPPET_LINES per file.
        """
        if not functions:
            # No specific functions — return first MAX_SNIPPET_LINES lines
            lines = content.split("\n")[:MAX_SNIPPET_LINES]
            return self._format_with_line_numbers(lines, 1, file_path)

        lines = content.split("\n")
        collected_ranges: list[tuple[int, int]] = []

        for func_name in functions:
            short_name = func_name.split(".")[-1] if "." in func_name else func_name
            start, end = self._find_function_range(lines, short_name)
            if start >= 0:
                collected_ranges.append((start, min(end, start + MAX_SNIPPET_LINES)))

        if not collected_ranges:
            # Fallback: keyword search
            for func_name in functions:
                short_name = func_name.split(".")[-1] if "." in func_name else func_name
                for i, line in enumerate(lines):
                    if short_name in line:
                        start = max(0, i - 3)
                        end = min(len(lines), i + 30)
                        collected_ranges.append((start, end))
                        break

        if not collected_ranges:
            return self._format_with_line_numbers(
                lines[:MAX_SNIPPET_LINES], 1, file_path
            )

        # Merge overlapping ranges
        merged = self._merge_ranges(collected_ranges)

        # Build output with gap markers
        output_parts = [f"# {file_path}"]
        total_lines = 0
        for start, end in merged:
            if total_lines > 0:
                output_parts.append("     | ...")
            for i in range(start, end):
                if i < len(lines) and total_lines < MAX_SNIPPET_LINES:
                    output_parts.append(f"{i + 1:4d} | {lines[i]}")
                    total_lines += 1

        return "\n".join(output_parts)

    @staticmethod
    def _find_function_range(lines: list[str], func_name: str) -> tuple[int, int]:
        """Find start and end line of a function definition."""
        start_idx = -1
        indent_level = -1

        for i, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith(f"def {func_name}") or stripped.startswith(f"class {func_name}"):
                start_idx = i
                indent_level = len(line) - len(stripped)
                break

        if start_idx == -1:
            return -1, -1

        # Find end: next definition at same or lower indent
        end_idx = start_idx + 1
        while end_idx < len(lines):
            line = lines[end_idx]
            if line.strip() == "":
                end_idx += 1
                continue
            current_indent = len(line) - len(line.lstrip())
            if current_indent <= indent_level and line.lstrip().startswith(("def ", "class ", "@")):
                break
            end_idx += 1

        # Include decorators above (up to 3)
        decorator_start = start_idx
        for i in range(start_idx - 1, max(start_idx - 4, -1), -1):
            if i >= 0 and lines[i].lstrip().startswith("@"):
                decorator_start = i
            else:
                break

        return decorator_start, end_idx

    @staticmethod
    def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
        """Merge overlapping line ranges."""
        if not ranges:
            return []
        sorted_ranges = sorted(ranges, key=lambda r: r[0])
        merged = [sorted_ranges[0]]
        for start, end in sorted_ranges[1:]:
            prev_start, prev_end = merged[-1]
            if start <= prev_end + 3:  # Allow 3-line gap for merging
                merged[-1] = (prev_start, max(prev_end, end))
            else:
                merged.append((start, end))
        return merged

    @staticmethod
    def _format_with_line_numbers(lines: list[str], start_line: int, file_path: str) -> str:
        """Format lines with line numbers and file header."""
        parts = [f"# {file_path}"]
        for i, line in enumerate(lines):
            parts.append(f"{start_line + i:4d} | {line}")
        return "\n".join(parts)
