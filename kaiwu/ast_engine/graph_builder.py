"""
Code graph builder: full + incremental build, persisted to SQLite.
LOC-RED-2: Graph data persisted to SQLite, survives restart.
LOC-RED-4: Incremental update for modified files only.
"""

import logging
import os
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Optional

from kaiwu.ast_engine.parser import TreeSitterParser

logger = logging.getLogger(__name__)

DB_PATH = Path.home() / ".kwcode" / "graph.db"

_BASE_EXTENSIONS = {".py"}  # Always supported (tree-sitter-python)

# Dynamically add extensions for available tree-sitter bindings
def _get_supported_extensions() -> set:
    """Return supported extensions based on installed tree-sitter bindings."""
    exts = set(_BASE_EXTENSIONS)
    try:
        from kaiwu.ast_engine.parser import TreeSitterParser
        _p = TreeSitterParser()
        lang_ext_map = {
            "javascript": {".js", ".mjs"},
            "typescript": {".ts", ".tsx"},
            "go": {".go"},
            "rust": {".rs"},
            "java": {".java"},
        }
        for lang in _p.supported_languages():
            if lang in lang_ext_map:
                exts.update(lang_ext_map[lang])
    except Exception:
        pass
    return exts

SUPPORTED_EXTENSIONS = _get_supported_extensions()

SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "env", "dist", "build", ".tox", "htmlcov", ".pytest_cache",
    ".eggs", ".mypy_cache",
}

SKIP_FILE_PATTERNS = {"test_", "_test.", "conftest."}


class GraphBuilder:
    """Builds and maintains a code call graph in SQLite."""

    def __init__(self, project_root: str):
        self.project_root = str(Path(project_root).resolve())
        self.db_path = DB_PATH
        self._parser = TreeSitterParser()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS nodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    qualified TEXT,
                    file_path TEXT NOT NULL,
                    start_line INTEGER,
                    end_line INTEGER,
                    node_type TEXT DEFAULT 'function',
                    docstring TEXT DEFAULT '',
                    search_text TEXT,
                    task_count INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    last_modified TEXT,
                    project_root TEXT NOT NULL,
                    UNIQUE(qualified, file_path, project_root)
                );

                CREATE TABLE IF NOT EXISTS edges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_id INTEGER NOT NULL REFERENCES nodes(id),
                    to_id INTEGER NOT NULL REFERENCES nodes(id),
                    edge_type TEXT NOT NULL,
                    project_root TEXT NOT NULL,
                    UNIQUE(from_id, to_id, edge_type)
                );

                CREATE TABLE IF NOT EXISTS graph_meta (
                    project_root TEXT PRIMARY KEY,
                    last_built TEXT,
                    last_commit TEXT,
                    file_count INTEGER,
                    node_count INTEGER,
                    edge_count INTEGER,
                    build_time_ms INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_nodes_project
                    ON nodes(project_root);
                CREATE INDEX IF NOT EXISTS idx_nodes_file
                    ON nodes(file_path, project_root);
                CREATE INDEX IF NOT EXISTS idx_nodes_name
                    ON nodes(name, project_root);
                CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_id);
                CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_id);
            """)

    def needs_rebuild(self) -> bool:
        current_commit = self._get_current_commit()
        if not current_commit:
            # Not a git repo — check if we ever built
            with self._get_conn() as conn:
                row = conn.execute(
                    "SELECT last_built FROM graph_meta WHERE project_root=?",
                    (self.project_root,)
                ).fetchone()
            return row is None

        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT last_commit FROM graph_meta WHERE project_root=?",
                (self.project_root,)
            ).fetchone()

        if not row:
            return True
        return row["last_commit"] != current_commit

    def get_last_commit(self) -> Optional[str]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT last_commit FROM graph_meta WHERE project_root=?",
                (self.project_root,)
            ).fetchone()
        return row["last_commit"] if row else None

    def build_full(self) -> dict:
        t0 = time.perf_counter()
        logger.info("[graph] full build: %s", self.project_root)

        with self._get_conn() as conn:
            conn.execute("DELETE FROM edges WHERE project_root=?", (self.project_root,))
            conn.execute("DELETE FROM nodes WHERE project_root=?", (self.project_root,))

        source_files = self._collect_source_files()
        logger.info("[graph] found %d source files", len(source_files))

        node_count = 0
        edge_count = 0
        for fpath in source_files:
            try:
                n, e = self._parse_file(fpath)
                node_count += n
                edge_count += e
            except Exception as ex:
                logger.warning("[graph] parse failed %s: %s", fpath, ex)

        current_commit = self._get_current_commit()
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO graph_meta
                (project_root, last_built, last_commit,
                 file_count, node_count, edge_count, build_time_ms)
                VALUES (?, datetime('now'), ?, ?, ?, ?, ?)
            """, (self.project_root, current_commit or "",
                  len(source_files), node_count, edge_count, elapsed_ms))

        logger.info("[graph] full build done: %d nodes %d edges %dms",
                    node_count, edge_count, elapsed_ms)
        return {
            "node_count": node_count,
            "edge_count": edge_count,
            "file_count": len(source_files),
            "elapsed_ms": elapsed_ms,
        }

    def update_files(self, file_paths: list[str]) -> dict:
        t0 = time.perf_counter()
        node_count = 0
        edge_count = 0

        for file_path in file_paths:
            try:
                rel_path = os.path.relpath(file_path, self.project_root).replace("\\", "/")
            except ValueError:
                continue

            # Delete old nodes/edges for this file
            with self._get_conn() as conn:
                old_ids = [row[0] for row in conn.execute(
                    "SELECT id FROM nodes WHERE file_path=? AND project_root=?",
                    (rel_path, self.project_root)
                ).fetchall()]
                if old_ids:
                    ph = ",".join("?" * len(old_ids))
                    conn.execute(
                        f"DELETE FROM edges WHERE from_id IN ({ph}) OR to_id IN ({ph})",
                        old_ids + old_ids
                    )
                    conn.execute(f"DELETE FROM nodes WHERE id IN ({ph})", old_ids)

            # Re-parse
            if os.path.exists(file_path):
                try:
                    n, e = self._parse_file(file_path)
                    node_count += n
                    edge_count += e
                except Exception as ex:
                    logger.warning("[graph] incremental update failed %s: %s", file_path, ex)

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        logger.info("[graph] incremental update: %d files %d nodes %d edges %dms",
                    len(file_paths), node_count, edge_count, elapsed_ms)
        return {
            "files": len(file_paths),
            "node_count": node_count,
            "edge_count": edge_count,
            "elapsed_ms": elapsed_ms,
        }

    def _collect_source_files(self) -> list[str]:
        files = []
        for dirpath, dirnames, filenames in os.walk(self.project_root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fname in sorted(filenames):
                ext = os.path.splitext(fname)[1].lower()
                if ext not in SUPPORTED_EXTENSIONS:
                    continue
                if any(fname.startswith(p) or p in fname for p in SKIP_FILE_PATTERNS):
                    continue
                files.append(os.path.join(dirpath, fname))
        return files

    def _parse_file(self, file_path: str) -> tuple[int, int]:
        rel_path = os.path.relpath(file_path, self.project_root).replace("\\", "/")

        tree = self._parser.parse_file(file_path)
        if tree is None:
            return 0, 0

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                source = f.read().encode("utf-8")
        except Exception:
            return 0, 0

        functions = self._parser.extract_functions(tree, source)
        calls = self._parser.extract_calls(tree, source)

        node_count = 0
        edge_count = 0

        with self._get_conn() as conn:
            # Insert nodes
            for func in functions:
                name = func["name"]
                # qualified = name (already includes Class.method from parser)
                # Rich search_text: name + path components + short name
                path_parts = rel_path.replace("/", " ").replace("\\", " ").replace(".", " ").replace("_", " ")
                short_name = name.split(".")[-1] if "." in name else name
                search_text = f"{name} {short_name} {rel_path} {path_parts}"
                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO nodes
                        (name, qualified, file_path, start_line, end_line,
                         node_type, search_text, project_root, last_modified)
                        VALUES (?, ?, ?, ?, ?, 'function', ?, ?, datetime('now'))
                    """, (name, name, rel_path,
                          func.get("start_line"), func.get("end_line"),
                          search_text, self.project_root))
                    node_count += 1
                except sqlite3.IntegrityError:
                    pass

            # Insert call edges
            for call in calls:
                caller = call.get("in_function")
                callee = call.get("name")
                if not caller or not callee:
                    continue

                caller_row = conn.execute(
                    "SELECT id FROM nodes WHERE name=? AND file_path=? AND project_root=? LIMIT 1",
                    (caller, rel_path, self.project_root)
                ).fetchone()

                # Callee could be in any file
                callee_row = conn.execute(
                    "SELECT id FROM nodes WHERE name=? AND project_root=? LIMIT 1",
                    (callee, self.project_root)
                ).fetchone()
                if not callee_row:
                    # Try short name match (e.g. "bar" -> "Foo.bar")
                    callee_row = conn.execute(
                        "SELECT id FROM nodes WHERE name LIKE ? AND project_root=? LIMIT 1",
                        (f"%.{callee}", self.project_root)
                    ).fetchone()

                if caller_row and callee_row:
                    try:
                        conn.execute("""
                            INSERT OR IGNORE INTO edges
                            (from_id, to_id, edge_type, project_root)
                            VALUES (?, ?, 'CALLS', ?)
                        """, (caller_row["id"], callee_row["id"], self.project_root))
                        edge_count += 1
                    except sqlite3.IntegrityError:
                        pass

        return node_count, edge_count

    def _get_current_commit(self) -> str:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.project_root,
                capture_output=True, text=True, timeout=3
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            return ""

    def export_rig(self) -> dict:
        """Scan project and output .kaiwu/rig.json with structure map."""
        import json
        import re

        rig = {
            "files": {},
            "api_routes": {},
            "test_coverage": {},
            "frontend_api_calls": {},
            "language_stats": {},
        }

        # --- Language stats ---
        try:
            from kaiwu.ast_engine.language_detector import detect_project_languages
            rig["language_stats"] = detect_project_languages(self.project_root)
        except Exception:
            pass

        # --- Collect all Python files (including test files) ---
        all_py_files = []
        for dirpath, dirnames, filenames in os.walk(self.project_root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fname in sorted(filenames):
                if fname.endswith(".py"):
                    all_py_files.append(os.path.join(dirpath, fname))

        # --- Parse each Python file for exports/imports/routes ---
        re_import = re.compile(r"^\s*import\s+([\w.]+)", re.MULTILINE)
        re_from_import = re.compile(r"^\s*from\s+([\w.]+)\s+import", re.MULTILINE)
        re_top_def = re.compile(r"^(?:def|class)\s+(\w+)", re.MULTILINE)
        re_route = re.compile(
            r"@(?:app|router)\.(route|get|post|put|delete|patch)\(\s*['\"]([^'\"]+)['\"]",
            re.MULTILINE,
        )

        for fpath in all_py_files:
            rel_path = os.path.relpath(fpath, self.project_root).replace("\\", "/")
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    source = f.read()
            except Exception:
                continue

            # Exports: top-level def/class names
            exports = re_top_def.findall(source)

            # Imports: module names
            imports = list(set(re_import.findall(source) + re_from_import.findall(source)))

            if exports or imports:
                rig["files"][rel_path] = {
                    "exports": exports,
                    "imports": imports,
                }

            # API routes
            for match in re_route.finditer(source):
                method_raw, path = match.group(1), match.group(2)
                method = method_raw.upper() if method_raw != "route" else "GET"
                # Find the function defined right after the decorator
                after = source[match.end():]
                func_match = re.search(r"^\s*def\s+(\w+)", after, re.MULTILINE)
                func_name = func_match.group(1) if func_match else "unknown"
                route_key = f"{method} {path}"
                rig["api_routes"][route_key] = f"{rel_path}:{func_name}"

        # --- Test coverage: match test_foo.py -> foo.py ---
        source_files_by_name = {}
        for rel_path in rig["files"]:
            basename = os.path.basename(rel_path)
            source_files_by_name[basename] = rel_path

        for fpath in all_py_files:
            rel_path = os.path.relpath(fpath, self.project_root).replace("\\", "/")
            basename = os.path.basename(rel_path)
            # Match test_foo.py -> foo.py
            if basename.startswith("test_"):
                target_name = basename[5:]  # strip "test_"
                if target_name in source_files_by_name:
                    target_rel = source_files_by_name[target_name]
                    rig["test_coverage"].setdefault(target_rel, []).append(rel_path)

        # --- Frontend API calls: scan .js/.ts files ---
        re_axios_method = re.compile(
            r"""axios\.(get|post|put|delete|patch)\(\s*[`'"]([^`'"]+)[`'"]""",
            re.MULTILINE,
        )
        re_fetch_method = re.compile(
            r"""fetch\(\s*[`'"]([^`'"]+)[`'"].*?method:\s*['\"](\w+)['\"]""",
            re.DOTALL,
        )

        for dirpath, dirnames, filenames in os.walk(self.project_root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fname in sorted(filenames):
                if not fname.endswith((".js", ".ts", ".jsx", ".tsx")):
                    continue
                fpath = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(fpath, self.project_root).replace("\\", "/")
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except Exception:
                    continue

                # axios.post("/path", ...) style
                for match in re_axios_method.finditer(content):
                    method = match.group(1).upper()
                    path = match.group(2)
                    # Find last function/const def before this call
                    before = content[:match.start()]
                    func_matches = re.findall(
                        r"(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(?)",
                        before,
                    )
                    func_name = "anonymous"
                    if func_matches:
                        last = func_matches[-1]
                        func_name = last[0] or last[1]
                    route_key = f"{method} {path}"
                    rig["frontend_api_calls"][route_key] = f"{rel_path}:{func_name}"

                # fetch("/path", {method: "POST"}) style
                for match in re_fetch_method.finditer(content):
                    path = match.group(1)
                    method = match.group(2).upper()
                    before = content[:match.start()]
                    func_matches = re.findall(
                        r"(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(?)",
                        before,
                    )
                    func_name = "anonymous"
                    if func_matches:
                        last = func_matches[-1]
                        func_name = last[0] or last[1]
                    route_key = f"{method} {path}"
                    rig["frontend_api_calls"][route_key] = f"{rel_path}:{func_name}"

        # --- Write output ---
        out_dir = os.path.join(self.project_root, ".kaiwu")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "rig.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(rig, f, indent=2, ensure_ascii=False)

        # --- Write summary (lightweight, <5KB, for Gate/Locator prompt injection) ---
        summary = self._build_rig_summary(rig)
        summary_path = os.path.join(out_dir, "rig_summary.json")
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        logger.info("[graph] exported rig.json: %d files, %d routes, %d test mappings",
                    len(rig["files"]), len(rig["api_routes"]), len(rig["test_coverage"]))
        return rig

    @staticmethod
    def _build_rig_summary(rig: dict) -> dict:
        """
        生成 Gate/Locator 可注入的精简骨架。
        只保留：api_routes、test_coverage、frontend_api_calls、文件路径列表（截断）。
        不含详细 exports/imports，目标 <5KB。
        """
        import json

        summary = {
            "api_routes": rig.get("api_routes", {}),
            "test_coverage": rig.get("test_coverage", {}),
            "frontend_api_calls": rig.get("frontend_api_calls", {}),
        }

        # File list: only include paths, cap to keep total <5KB
        all_files = sorted(rig.get("files", {}).keys())
        # Budget: 4096 bytes total, subtract non-file content
        non_file_size = len(json.dumps(summary, ensure_ascii=False))
        file_budget = 4096 - non_file_size - 50  # 50 bytes overhead for "files" key + brackets
        # Add files until budget exhausted
        included = []
        running = 0
        for f in all_files:
            entry_size = len(f) + 5  # quotes + comma + space
            if running + entry_size > file_budget:
                break
            included.append(f)
            running += entry_size
        summary["files"] = included
        if len(included) < len(all_files):
            summary["_files_truncated"] = len(all_files)

        return summary
