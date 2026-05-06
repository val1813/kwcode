"""
UpstreamManifest: deterministic cross-file contract tracking.
Extracts function signatures, constants, and imports from patches.
Zero LLM calls — pure AST/regex extraction.

Used by:
- TaskCompiler: update() after each subtask completes
- Generator: get_constraints_for_file() injected into prompt
- Verifier: check_consistency() validates cross-file contracts
"""

import ast
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

__all__ = ["UpstreamManifest"]


class UpstreamManifest:
    """
    Cross-file contract table. All subtask outputs converge here.
    Pure deterministic, zero LLM calls.
    """

    def __init__(self):
        # {file_path: {func_name: signature_str}}
        self._signatures: dict[str, dict[str, str]] = {}
        # {file_path: {const_name: const_value}}
        self._constants: dict[str, dict[str, str]] = {}
        # {file_path: [import_line, ...]}
        self._imports: dict[str, list[str]] = {}
        # {file_path: [file_it_depends_on, ...]}
        self._dependency_graph: dict[str, list[str]] = {}

    def update(self, patches: list[dict]):
        """
        Update manifest from a list of patches.
        Each patch: {"file": str, "original": str, "modified": str}
        """
        for patch in patches:
            file_path = patch.get("file", "")
            modified = patch.get("modified", "")
            if not file_path or not modified:
                continue
            self._extract_from_code(file_path, modified)

    def _extract_from_code(self, file_path: str, code: str):
        """Extract signatures, constants, and imports from code string."""
        # 优先AST解析（最准确）
        if file_path.endswith(".py"):
            self._extract_python_ast(file_path, code)
        else:
            # 降级：非Python用正则提取
            self._extract_regex(file_path, code)

    def _extract_python_ast(self, file_path: str, code: str):
        """AST-based extraction for Python files."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            # AST失败时降级到正则
            self._extract_regex(file_path, code)
            return

        sigs: dict[str, str] = {}
        consts: dict[str, str] = {}
        imports: list[str] = []

        for node in ast.walk(tree):
            # 函数/方法签名
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sig = self._format_func_signature(node)
                sigs[node.name] = sig

            # 顶层常量（大写赋值）
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        # 尝试获取值的字符串表示
                        try:
                            value = ast.literal_eval(node.value)
                            consts[target.id] = repr(value)
                        except (ValueError, TypeError):
                            consts[target.id] = "<complex>"

            # import语句
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(f"import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = ", ".join(a.name for a in node.names)
                imports.append(f"from {module} import {names}")

        if sigs:
            self._signatures[file_path] = sigs
        if consts:
            self._constants[file_path] = consts
        if imports:
            self._imports[file_path] = imports
            # Build dependency graph from imports
            self._update_deps(file_path, imports)

    def _extract_regex(self, file_path: str, code: str):
        """Regex fallback for non-Python or unparseable code."""
        sigs: dict[str, str] = {}
        consts: dict[str, str] = {}

        # Function signatures: def/func/fn name(params)
        for m in re.finditer(
            r'^(?:(?:async\s+)?def|func|fn|function)\s+(\w+)\s*\(([^)]*)\)',
            code, re.MULTILINE
        ):
            name = m.group(1)
            params = m.group(2).strip()
            sigs[name] = f"{name}({params})"

        # Constants: UPPER_CASE = value
        for m in re.finditer(
            r'^([A-Z][A-Z_0-9]+)\s*[:=]\s*(.+?)$',
            code, re.MULTILINE
        ):
            consts[m.group(1)] = m.group(2).strip()[:100]

        if sigs:
            self._signatures[file_path] = sigs
        if consts:
            self._constants[file_path] = consts

    @staticmethod
    def _format_func_signature(node: ast.FunctionDef) -> str:
        """Format a function AST node into a readable signature string."""
        args = []
        for arg in node.args.args:
            name = arg.arg
            annotation = ""
            if arg.annotation:
                try:
                    annotation = f": {ast.unparse(arg.annotation)}"
                except Exception:
                    pass
            args.append(f"{name}{annotation}")

        # Return annotation
        returns = ""
        if node.returns:
            try:
                returns = f" -> {ast.unparse(node.returns)}"
            except Exception:
                pass

        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        return f"{prefix} {node.name}({', '.join(args)}){returns}"

    def _update_deps(self, file_path: str, imports: list[str]):
        """Infer file dependencies from import statements."""
        deps = []
        for imp in imports:
            # from kaiwu.core.context import TaskContext → kaiwu/core/context.py
            m = re.match(r'from\s+([\w.]+)\s+import', imp)
            if m:
                module_path = m.group(1).replace(".", "/") + ".py"
                deps.append(module_path)
        if deps:
            self._dependency_graph[file_path] = deps

    def get_constraints_for_file(self, file_path: str) -> str:
        """
        Return cross-file constraints that this file must respect.
        Injected into Generator prompt.
        """
        lines = []

        # 1. Signatures from OTHER files that this file might call
        deps = self._dependency_graph.get(file_path, [])
        for dep_file in deps:
            dep_sigs = self._signatures.get(dep_file, {})
            for func_name, sig in dep_sigs.items():
                lines.append(f"[契约] {dep_file} 提供: {sig}")

        # 2. Constants from other files
        for dep_file in deps:
            dep_consts = self._constants.get(dep_file, {})
            for const_name, const_val in dep_consts.items():
                lines.append(f"[常量] {dep_file}: {const_name} = {const_val}")

        # 3. This file's own exported signatures (for callers to respect)
        own_sigs = self._signatures.get(file_path, {})
        if own_sigs:
            lines.append(f"[本文件导出]")
            for func_name, sig in own_sigs.items():
                lines.append(f"  {sig}")

        return "\n".join(lines) if lines else ""

    def get_all_signatures(self) -> dict[str, dict[str, str]]:
        """Return all tracked signatures. Used by Verifier for consistency check."""
        return dict(self._signatures)

    def get_all_constants(self) -> dict[str, dict[str, str]]:
        """Return all tracked constants."""
        return dict(self._constants)

    def check_consistency(self, file_path: str, code: str) -> list[str]:
        """
        Check if code in file_path is consistent with manifest contracts.
        Returns list of violation descriptions (empty = all good).
        Pure deterministic, zero LLM.
        """
        violations = []

        # Extract function calls from the code
        calls = set(re.findall(r'(\w+)\s*\(', code))

        # Check: does this file call functions with wrong argument count?
        deps = self._dependency_graph.get(file_path, [])
        for dep_file in deps:
            dep_sigs = self._signatures.get(dep_file, {})
            for func_name, sig in dep_sigs.items():
                if func_name not in calls:
                    continue
                # Count expected params (rough: count commas + 1, minus self)
                expected_params = self._count_params(sig)
                # Find actual call sites and count args
                for m in re.finditer(
                    rf'{func_name}\s*\(([^)]*)\)', code
                ):
                    actual_args = self._count_args(m.group(1))
                    if expected_params is not None and actual_args > expected_params:
                        violations.append(
                            f"{file_path}: {func_name}() 调用传了{actual_args}个参数，"
                            f"但签名只接受{expected_params}个 (来自 {dep_file})"
                        )

        # Check: constants used but with wrong value?
        for dep_file in deps:
            dep_consts = self._constants.get(dep_file, {})
            for const_name, expected_val in dep_consts.items():
                if const_name in code and expected_val != "<complex>":
                    # Check if code redefines the constant with a different value
                    redefs = re.findall(
                        rf'^{const_name}\s*=\s*(.+?)$', code, re.MULTILINE
                    )
                    for redef in redefs:
                        if redef.strip() != expected_val:
                            violations.append(
                                f"{file_path}: {const_name} 重定义为 {redef.strip()}，"
                                f"但上游 {dep_file} 定义为 {expected_val}"
                            )

        return violations

    @staticmethod
    def _count_params(sig: str) -> Optional[int]:
        """Count parameters in a signature string, excluding self/cls."""
        m = re.search(r'\(([^)]*)\)', sig)
        if not m:
            return None
        params_str = m.group(1).strip()
        if not params_str:
            return 0
        params = [p.strip() for p in params_str.split(",")]
        # Exclude self, cls
        params = [p for p in params if p and p.split(":")[0].strip() not in ("self", "cls")]
        # Exclude *args, **kwargs from count (they accept any number)
        if any(p.startswith("*") for p in params):
            return None  # Can't determine exact count
        return len(params)

    @staticmethod
    def _count_args(args_str: str) -> int:
        """Count arguments in a function call."""
        args_str = args_str.strip()
        if not args_str:
            return 0
        # Simple comma counting (doesn't handle nested calls perfectly, but good enough)
        depth = 0
        count = 1
        for ch in args_str:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == "," and depth == 0:
                count += 1
        return count

    def to_compact_summary(self) -> dict:
        """
        PENCIL-style compression: return only what downstream tasks need.
        Discards reasoning chains, keeps structured artifacts.
        """
        return {
            "signatures": self._signatures,
            "constants": self._constants,
            "imports": self._imports,
        }

    def clear(self):
        """Reset manifest (e.g., between independent task groups)."""
        self._signatures.clear()
        self._constants.clear()
        self._imports.clear()
        self._dependency_graph.clear()
