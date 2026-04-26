"""
Locator expert: hierarchical code location (file → function).
RED-2: Deterministic pipeline, no LLM self-decision on next step.
RED-3: Independent context window.
"""

import json
import logging
import os
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

logger = logging.getLogger(__name__)

LOCATOR_FILE_PROMPT = """你是代码定位专家。根据任务描述，从文件列表中找出最相关的文件。

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
    """Two-phase locator: file-level → function-level. Each phase is one LLM call."""

    def __init__(self, llm: LLMBackend, tool_executor: ToolExecutor):
        self.llm = llm
        self.tools = tool_executor
        self._ast_locator = ASTLocator() if _AST_ENGINE_AVAILABLE else None

    def run(self, ctx: TaskContext) -> Optional[dict]:
        """
        Phase 1: Locate relevant files (file tree + symbol index).
        Phase 2: Locate relevant functions (AST candidates → LLM select).
        """
        task_desc = f"{ctx.user_input}"
        if ctx.search_results:
            task_desc += f"\n\n参考信息：\n{ctx.search_results}"

        # Phase 1: File-level location (tree + symbol index)
        file_tree = self.tools.get_file_tree(ctx.project_root)
        symbol_index = self._build_symbol_index(ctx.project_root)
        files = self._locate_files(file_tree, task_desc, symbol_index)
        if not files:
            logger.warning("Locator: no files found")
            return None

        # Phase 2: Function-level location
        # Try AST call graph first (fast, accurate), fall back to LLM
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
                    # Use AST-located files if they overlap with LLM files
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
                funcs, locs = self._locate_functions(fpath, content, task_desc)
                all_functions.extend(funcs)
                all_locations.extend(locs)

        # Extract code snippets for Generator
        for fpath in files[:5]:
            content = self.tools.read_file(fpath)
            if content.startswith("[ERROR]"):
                continue
            snippet = self._extract_snippet(content, all_functions)
            if snippet:
                code_snippets[fpath] = snippet

        result = {
            "relevant_files": files,
            "relevant_functions": all_functions,
            "edit_locations": all_locations,
        }

        # Store snippets in context for Generator
        ctx.locator_output = result
        ctx.relevant_code_snippets = code_snippets
        return result

    def _locate_files(self, file_tree: str, task_desc: str, symbol_index: str = "") -> list[str]:
        """Phase 1: LLM call to find relevant files from tree + symbol index."""
        si_section = ""
        if symbol_index:
            si_section = f"各文件的函数/类定义：\n{symbol_index}"

        prompt = LOCATOR_FILE_PROMPT.format(
            file_tree=file_tree[:3000],
            symbol_index=si_section[:2000],
            task_description=task_desc,
        )
        raw = self.llm.generate(prompt=prompt, max_tokens=300, temperature=0.0)
        return self._parse_file_list(raw)

    def _locate_functions(self, file_path: str, content: str, task_desc: str) -> tuple[list, list]:
        """Phase 2: AST 提取候选列表 → LLM 从中选择。"""
        # 检测语言
        lang = "python" if file_path.endswith(".py") else "other"
        symbols = extract_symbols(content, language=lang)

        if not symbols:
            logger.warning("No symbols found in %s, skipping function location", file_path)
            return [], []

        # 如果只有 1-2 个函数，直接返回不浪费 LLM 调用
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
        raw = self.llm.generate(prompt=prompt, max_tokens=300, temperature=0.0)
        funcs, locs = self._parse_func_result(raw)

        # 验证 LLM 返回的函数名确实在候选列表中
        valid_names = {s["name"] for s in symbols}
        # 也接受不带类名前缀的方法名
        for s in symbols:
            if "." in s["name"]:
                valid_names.add(s["name"].split(".")[-1])

        verified_funcs = [f for f in funcs if f in valid_names]
        if not verified_funcs and funcs:
            logger.warning("LLM returned functions not in AST: %s, falling back", funcs)
            # 降级：返回所有非 dunder 函数
            verified_funcs = [
                s["name"] for s in func_symbols
                if not s["name"].startswith("_") or s["name"].startswith("__") is False
            ][:3]

        verified_locs = [f"{file_path}:{f}" for f in verified_funcs]
        return verified_funcs, verified_locs

    def _extract_snippet(self, content: str, functions: list[str]) -> str:
        """Extract code around target functions (±20 lines)."""
        if not functions:
            return content[:2000]  # Fallback: first 2000 chars

        lines = content.split("\n")
        collected = set()

        for func_name in functions:
            for i, line in enumerate(lines):
                if f"def {func_name}" in line or f"class {func_name}" in line:
                    start = max(0, i - 5)
                    end = min(len(lines), i + 40)
                    for j in range(start, end):
                        collected.add(j)

        if not collected:
            return content[:2000]

        sorted_lines = sorted(collected)
        result = []
        for idx in sorted_lines:
            result.append(f"{idx + 1:4d} | {lines[idx]}")
        return "\n".join(result)

    @staticmethod
    def _parse_file_list(raw: str) -> list[str]:
        """Parse file list JSON from LLM output."""
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
        """Parse function location JSON from LLM output."""
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
        """扫描项目源文件，用 AST 提取每个文件的函数/类名，构建符号索引。"""
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
