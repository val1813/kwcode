"""
Locator expert: BM25+graph primary path, LLM fallback.
LOC-RED-3: BM25+graph is the main path, LLM is fallback only.
LOC-RED-5: Total locator time must be under 3 seconds.
RED-2: Deterministic pipeline, no LLM self-decision on next step.
RED-3: Independent context window.
"""

import json
import logging
import os
import threading
from typing import Optional

from kaiwu.core.context import TaskContext
from kaiwu.llm.llama_backend import LLMBackend
from kaiwu.tools.executor import ToolExecutor
from kaiwu.tools.ast_utils import extract_symbols, format_symbol_list

try:
    from kaiwu.ast_engine.locator import ASTLocator
    _AST_ENGINE_AVAILABLE = True
except ImportError:
    _AST_ENGINE_AVAILABLE = False

try:
    from kaiwu.ast_engine.graph_builder import GraphBuilder
    from kaiwu.ast_engine.graph_retriever import GraphRetriever
    _GRAPH_ENGINE_AVAILABLE = True
except ImportError:
    _GRAPH_ENGINE_AVAILABLE = False

logger = logging.getLogger(__name__)

LOCATOR_FILE_PROMPT = """你是代码定位专家。根据任务描述，从文件列表中找出最相关的文件。

重要：首先查看.kaiwu/rig.json（如果存在），它包含项目的文件导出/导入关系、API路由映射和测试覆盖信息。优先利用rig.json中的依赖关系来定位相关文件。

{rig_context}

仓库文件结构：
{file_tree}

{symbol_index}

任务描述：{task_description}

返回JSON，只包含最相关的文件（最多5个），格式：
{{"relevant_files": ["path/to/file1.py", "path/to/file2.py"]}}

只返回JSON，不要解释。"""

LOCATOR_FUNC_PROMPT = """你是代码定位专家。根据任务描述，从候选函数列表中选出需要修改的函数。

文件路径：{file_path}

候选函数/类列表（AST提取，保证存在）：
{symbol_list}

任务描述：{task_description}

从上面的候选列表中选出最相关的1-3个函数名。
注意：只能选列表中存在的名字，不要编造。

返回JSON：
{{"relevant_functions": ["函数名1"], "edit_locations": ["{file_path}:函数名1"]}}

只返回JSON，不要解释。"""


class LocatorExpert:
    """Two-phase locator: BM25+graph primary, LLM fallback."""

    def __init__(self, llm: LLMBackend, tool_executor: ToolExecutor):
        self.llm = llm
        self.tools = tool_executor
        self._ast_locator = ASTLocator() if _AST_ENGINE_AVAILABLE else None
        # Graph engine (lazy init per project)
        self._builder: Optional[GraphBuilder] = None
        self._retriever: Optional[GraphRetriever] = None
        self._graph_project: Optional[str] = None

    def _ensure_graph(self, project_root: str):
        """Ensure graph is built for this project. Non-blocking on first build (FLEX-3)."""
        if not _GRAPH_ENGINE_AVAILABLE:
            return

        if self._retriever and self._graph_project == project_root:
            return  # Already initialized for this project

        self._graph_project = project_root
        self._builder = GraphBuilder(project_root)
        self._retriever = GraphRetriever(project_root)

        if self._builder.needs_rebuild():
            if self._retriever.has_graph():
                # Graph exists but outdated — rebuild in background, use stale graph now
                logger.info("[locator] graph outdated, background rebuild")
                threading.Thread(
                    target=self._builder.build_full,
                    daemon=True,
                    name="graph-builder"
                ).start()
            else:
                # No graph at all — try quick synchronous build (FLEX-3: async if too slow)
                logger.info("[locator] no graph, attempting sync build")
                try:
                    result = self._builder.build_full()
                    logger.info("[locator] sync build done: %d nodes %dms",
                                result["node_count"], result["elapsed_ms"])
                except Exception as e:
                    logger.warning("[locator] sync build failed: %s", e)

    def run(self, ctx: TaskContext) -> Optional[dict]:
        """
        Main entry: BM25+graph primary path, LLM fallback.
        """
        task_desc = f"{ctx.user_input}"
        if ctx.search_results:
            task_desc += f"\n\n参考信息：\n{ctx.search_results}"

        # Ensure graph is ready
        self._ensure_graph(ctx.project_root)

        # ── Primary path: BM25 + graph traversal ──────────────────
        graph_result = self._graph_locate(ctx, task_desc)
        if graph_result:
            return graph_result

        # ── Fallback: LLM file tree + AST ─────────────────────────
        logger.info("[locator] graph path returned nothing, falling back to LLM")
        return self._llm_locate(ctx, task_desc)

    def _graph_locate(self, ctx: TaskContext, task_desc: str) -> Optional[dict]:
        """BM25+graph retrieval (no LLM calls)."""
        if not self._retriever:
            return None

        try:
            results = self._retriever.retrieve(
                query=task_desc,
                top_k_bm25=20,
                graph_hops=2,
                max_results=10,
            )
        except Exception as e:
            logger.warning("[locator] graph retrieval failed: %s", e)
            return None

        if not results:
            return None

        # Filter out results with missing keys (defensive against malformed graph data)
        results = [r for r in results if r.get("file_path") and r.get("name")]
        if not results:
            return None

        relevant_files = list(dict.fromkeys(r["file_path"] for r in results))
        relevant_functions = [r["name"] for r in results[:5]]

        logger.info("[locator] BM25+graph: %d files %d functions",
                    len(relevant_files), len(relevant_functions))

        # Store node IDs for post-task stats update
        ctx._locator_node_ids = [r["id"] for r in results]

        result = {
            "relevant_files": relevant_files[:5],
            "relevant_functions": relevant_functions,
            "edit_locations": [
                f"{r['file_path']}:L{r['start_line']}-{r['end_line']}"
                for r in results[:5]
                if r.get("start_line")
            ],
            "method": "bm25_graph",
        }

        # Extract code snippets for Generator
        code_snippets = {}
        for fpath in relevant_files[:5]:
            content = self.tools.read_file(fpath)
            if content.startswith("[ERROR]"):
                continue
            snippet = self._extract_snippet(content, relevant_functions, file_path=fpath)
            if snippet:
                code_snippets[fpath] = snippet

        ctx.locator_output = result
        ctx.relevant_code_snippets = code_snippets

        # ── DocReader: inject relevant document paragraphs ──
        self._inject_doc_context(ctx)

        # ── Speculative Prefetch: 后台预读文件到缓存 ──
        self._prefetch(relevant_files[:5])

        return result

    def _llm_locate(self, ctx: TaskContext, task_desc: str) -> Optional[dict]:
        """Fallback: LLM file tree guessing + AST/LLM function location."""
        # Phase 1: File-level location
        file_tree = self.tools.get_file_tree(ctx.project_root)
        symbol_index = self._build_symbol_index(ctx.project_root)
        files = self._locate_files(file_tree, task_desc, symbol_index, ctx=ctx)
        if not files:
            logger.warning("Locator: no files found")
            return None

        # Phase 2: Function-level location
        all_functions = []
        all_locations = []
        code_snippets = {}
        ast_used = False

        if self._ast_locator:
            try:
                ast_result = self._ast_locator.locate(ctx.project_root, task_desc)
                ast_funcs = ast_result.get("relevant_functions", [])
                if ast_funcs:
                    all_functions = ast_funcs
                    all_locations = [f"{c['file']}:{c['name']}" for c in ast_result.get("candidates", [])]
                    ast_files = ast_result.get("relevant_files", [])
                    if ast_files:
                        files = list(dict.fromkeys(files + ast_files))[:5]
                    ast_used = True
                    logger.info("AST locator found %d functions", len(ast_funcs))
            except Exception as e:
                logger.debug("AST locator failed, falling back to LLM: %s", e)

        if not ast_used:
            for fpath in files[:5]:
                content = self.tools.read_file(fpath)
                if content.startswith("[ERROR]"):
                    continue
                funcs, locs = self._locate_functions(fpath, content, task_desc, ctx=ctx)
                all_functions.extend(funcs)
                all_locations.extend(locs)

        # Extract code snippets for Generator
        for fpath in files[:5]:
            content = self.tools.read_file(fpath)
            if content.startswith("[ERROR]"):
                continue
            snippet = self._extract_snippet(content, all_functions, file_path=fpath)
            if snippet:
                code_snippets[fpath] = snippet

        result = {
            "relevant_files": files,
            "relevant_functions": all_functions,
            "edit_locations": all_locations,
            "method": "llm_fallback",
        }

        ctx.locator_output = result
        ctx.relevant_code_snippets = code_snippets

        # ── DocReader: inject relevant document paragraphs ──
        self._inject_doc_context(ctx)

        return result

    def _prefetch(self, files: list[str]):
        """Speculative Prefetch: Locator完成后后台预读文件到内存，减少Generator等待IO。"""
        import threading

        def _do():
            for f in files[:5]:
                try:
                    self.tools.read_file(f)
                except Exception:
                    pass

        threading.Thread(target=_do, daemon=True, name="prefetch").start()

    def notify_task_result(self, ctx: TaskContext, success: bool):
        """
        Post-task callback:
        1. Update node task stats (flywheel data)
        2. Incremental graph update for modified files
        """
        node_ids = getattr(ctx, "_locator_node_ids", [])
        if node_ids and self._retriever:
            try:
                self._retriever.update_task_stats(node_ids, success)
            except Exception as e:
                logger.debug("[locator] update_task_stats failed: %s", e)

        # Incremental update for modified files
        if self._builder and ctx.generator_output:
            modified_files = [
                os.path.join(ctx.project_root, p["file"])
                for p in ctx.generator_output.get("patches", [])
                if p.get("file")
            ]
            if modified_files:
                threading.Thread(
                    target=self._builder.update_files,
                    args=(modified_files,),
                    daemon=True,
                    name="graph-updater"
                ).start()

    def _build_system(self, ctx: TaskContext) -> str:
        """Return expert_system_prompt if available."""
        return ctx.expert_system_prompt or ""

    def _inject_doc_context(self, ctx: TaskContext):
        """Read project docs (PDF/Word/MD) and inject relevant paragraphs. P1-RED-4: never raises."""
        try:
            from kaiwu.knowledge.doc_reader import DocReader
            doc_reader = DocReader(ctx.project_root)
            doc_context = doc_reader.find_relevant(
                query=ctx.user_input,
                max_paragraphs=3,
                max_tokens=800,
            )
            if doc_context:
                ctx.doc_context = doc_context
                logger.info("[locator] doc_reader found %d chars", len(doc_context))
        except Exception as e:
            logger.debug("[locator] doc_reader skipped: %s", e)

    def _load_rig_context(self, project_root: str) -> str:
        """Load rig_summary.json for prompt injection. Returns empty string if unavailable."""
        rig_path = os.path.join(project_root, ".kaiwu", "rig_summary.json")
        if not os.path.exists(rig_path):
            return ""
        try:
            import json
            with open(rig_path, "r", encoding="utf-8") as f:
                rig = json.load(f)
            # Build compact summary: routes + key file exports
            parts = []
            routes = rig.get("api_routes", {})
            if routes:
                parts.append("API路由:")
                for route, loc in list(routes.items())[:20]:
                    parts.append(f"  {route} → {loc}")
            frontend = rig.get("frontend_api_calls", {})
            if frontend:
                parts.append("前端调用:")
                for route, loc in list(frontend.items())[:20]:
                    parts.append(f"  {route} → {loc}")
            test_cov = rig.get("test_coverage", {})
            if test_cov:
                parts.append("测试覆盖:")
                for src, tests in list(test_cov.items())[:10]:
                    parts.append(f"  {src} ← {', '.join(tests)}")
            return "\n".join(parts) if parts else ""
        except Exception:
            return ""

    def _locate_files(self, file_tree: str, task_desc: str, symbol_index: str = "", ctx: TaskContext = None) -> list[str]:
        """Phase 1: LLM call to find relevant files from tree + symbol index."""
        si_section = ""
        if symbol_index:
            si_section = f"各文件的函数/类定义：\n{symbol_index}"

        # Load rig.json context for better file location
        rig_context = ""
        if ctx:
            rig_context = self._load_rig_context(ctx.project_root)
        if rig_context:
            rig_context = f"项目结构索引(rig.json):\n{rig_context}"

        prompt = LOCATOR_FILE_PROMPT.format(
            file_tree=file_tree[:3000],
            symbol_index=si_section[:2000],
            task_description=task_desc,
            rig_context=rig_context[:2000],
        )
        system = self._build_system(ctx) if ctx else ""
        raw = self.llm.generate(prompt=prompt, system=system, max_tokens=300, temperature=0.0)
        return self._parse_file_list(raw)

    def _locate_functions(self, file_path: str, content: str, task_desc: str, ctx: TaskContext = None) -> tuple[list, list]:
        """Phase 2: AST extract candidates -> LLM select."""
        lang = "python" if file_path.endswith(".py") else "other"
        symbols = extract_symbols(content, language=lang)

        if not symbols:
            logger.warning("No symbols found in %s, skipping function location", file_path)
            return [], []

        func_symbols = [s for s in symbols if s["type"] in ("function", "method")]
        if len(func_symbols) == 1:
            name = func_symbols[0]["name"]
            return [name], [f"{file_path}:{name}"]

        symbol_list = format_symbol_list(symbols)
        prompt = LOCATOR_FUNC_PROMPT.format(
            file_path=file_path,
            symbol_list=symbol_list,
            task_description=task_desc,
        )
        system = self._build_system(ctx) if ctx else ""
        raw = self.llm.generate(prompt=prompt, system=system, max_tokens=300, temperature=0.0)
        funcs, locs = self._parse_func_result(raw)

        valid_names = {s["name"] for s in symbols}
        for s in symbols:
            if "." in s["name"]:
                valid_names.add(s["name"].split(".")[-1])

        verified_funcs = [f for f in funcs if f in valid_names]
        if not verified_funcs and funcs:
            logger.warning("LLM returned functions not in AST: %s, falling back", funcs)
            verified_funcs = [
                s["name"] for s in func_symbols
                if not s["name"].startswith("_") or s["name"].startswith("__") is False
            ][:3]

        verified_locs = [f"{file_path}:{f}" for f in verified_funcs]
        return verified_funcs, verified_locs

    def _extract_snippet(self, content: str, functions: list[str], file_path: str = "") -> str:
        """Extract minimal context around target functions. Identifies function boundaries,
        strips comments/docstrings, caps at 60 lines per function."""
        if not functions:
            return content[:2000]

        lines = content.split("\n")
        collected = set()

        for func_name in functions:
            short_name = func_name.split(".")[-1] if "." in func_name else func_name
            func_start = None
            func_indent = -1

            for i, line in enumerate(lines):
                stripped = line.lstrip()
                if (f"def {short_name}" in line or f"class {short_name}" in line) and stripped.startswith(("def ", "class ")):
                    func_start = i
                    func_indent = len(line) - len(stripped)
                    break

            if func_start is None:
                # Fallback: grab +-20 lines around keyword match
                for i, line in enumerate(lines):
                    if short_name in line:
                        start = max(0, i - 5)
                        end = min(len(lines), i + 25)
                        for j in range(start, end):
                            collected.add(j)
                        break
                continue

            # Find function end: next def/class at same or lower indent level
            func_end = len(lines)
            for i in range(func_start + 1, len(lines)):
                line = lines[i]
                if not line.strip():
                    continue
                current_indent = len(line) - len(line.lstrip())
                if current_indent <= func_indent and line.lstrip().startswith(("def ", "class ", "@")):
                    func_end = i
                    break

            # Include decorators above function (up to 3 lines)
            decorator_start = func_start
            for i in range(func_start - 1, max(func_start - 4, -1), -1):
                if i >= 0 and lines[i].lstrip().startswith("@"):
                    decorator_start = i
                else:
                    break

            # Collect lines, skip pure comment blocks in the middle
            start = decorator_start
            end = min(func_end, func_start + 60)  # Cap at 60 lines
            in_docstring = False
            docstring_lines = 0

            for j in range(start, end):
                line = lines[j]
                stripped = line.strip()

                # Track docstring boundaries
                if '"""' in stripped or "'''" in stripped:
                    if in_docstring:
                        in_docstring = False
                        docstring_lines += 1
                        if docstring_lines <= 3:
                            collected.add(j)
                        continue
                    else:
                        in_docstring = True
                        docstring_lines = 0
                        if stripped.endswith('"""') and stripped.count('"""') == 2:
                            # Single-line docstring
                            in_docstring = False
                            collected.add(j)
                            continue
                        collected.add(j)
                        continue

                if in_docstring:
                    docstring_lines += 1
                    if docstring_lines <= 3:
                        collected.add(j)
                    continue

                # Skip pure comment lines (but keep inline comments)
                if stripped.startswith("#") and j > func_start + 1:
                    # Keep comments that look structural
                    if any(kw in stripped for kw in ("TODO", "FIXME", "NOTE", "HACK", "──")):
                        collected.add(j)
                    continue

                collected.add(j)

        if not collected:
            return content[:2000]

        sorted_lines = sorted(collected)
        result = []
        prev_idx = -2
        for idx in sorted_lines:
            if idx - prev_idx > 1 and prev_idx >= 0:
                result.append("     | ...")  # Gap marker
            result.append(f"{idx + 1:4d} | {lines[idx]}")
            prev_idx = idx

        header = f"# {file_path}\n" if file_path else ""
        return header + "\n".join(result)

    @staticmethod
    def _parse_file_list(raw: str) -> list[str]:
        try:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end > start:
                data = json.loads(raw[start:end + 1])
                return data.get("relevant_files", [])
        except (json.JSONDecodeError, KeyError):
            pass
        logger.warning("Locator file parse failed: %s", raw[:200])
        return []

    @staticmethod
    def _parse_func_result(raw: str) -> tuple[list, list]:
        try:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end > start:
                data = json.loads(raw[start:end + 1])
                return (
                    data.get("relevant_functions", []),
                    data.get("edit_locations", []),
                )
        except (json.JSONDecodeError, KeyError):
            pass
        logger.warning("Locator func parse failed: %s", raw[:200])
        return [], []

    def _build_symbol_index(self, project_root: str, max_files: int = 30) -> str:
        index_lines = []
        count = 0
        skip_dirs = {".git", "__pycache__", "node_modules", "venv", ".venv", ".eggs"}

        for dirpath, dirnames, filenames in os.walk(project_root):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
            for fname in sorted(filenames):
                if not fname.endswith((".py", ".js", ".ts", ".go", ".rs")):
                    continue
                if fname.startswith("test_") or fname == "conftest.py":
                    continue
                if count >= max_files:
                    break

                fpath = os.path.join(dirpath, fname)
                rel = os.path.relpath(fpath, project_root).replace("\\", "/")
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        source = f.read()
                    lang = "python" if fname.endswith(".py") else "other"
                    symbols = extract_symbols(source, language=lang)
                    if symbols:
                        names = [s["name"] for s in symbols if s["type"] in ("function", "class", "method")]
                        if names:
                            index_lines.append(f"  {rel}: {', '.join(names[:8])}")
                            count += 1
                except Exception:
                    pass

        return "\n".join(index_lines) if index_lines else ""
