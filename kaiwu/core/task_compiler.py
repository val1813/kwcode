"""
Lightweight DAG Task Compiler.
Accepts task definitions with dependencies, builds a DAG, executes via
ThreadPoolExecutor with topological ordering.

Zero new pip dependencies (ThreadPoolExecutor is stdlib).
Each task gets its own TaskContext (RED-3: independent context).
"""

import logging
import re
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kaiwu.core.orchestrator import PipelineOrchestrator
    from kaiwu.core.gate import Gate

logger = logging.getLogger(__name__)

__all__ = ["TaskCompiler", "WorktreeManager", "CycleError"]

MAX_PARALLEL_WORKERS = 4


class CycleError(Exception):
    """Raised when the task DAG contains a cycle."""
    pass


class TaskCompiler:
    """
    Lightweight DAG task scheduler.
    Wraps PipelineOrchestrator.run() — each task in the DAG calls
    orchestrator.run() with appropriate parameters.
    """

    def __init__(
        self,
        orchestrator: "PipelineOrchestrator",
        gate: "Gate",
        project_root: str,
    ):
        self.orchestrator = orchestrator
        self.gate = gate
        self.project_root = project_root

    def compile_and_run(
        self,
        tasks: list[dict],
        on_status=None,
    ) -> dict:
        """
        Execute a DAG of tasks.

        Args:
            tasks: list of task dicts, each with:
                - "id": unique task identifier (str)
                - "input": user_input string for the task
                - "expert_type": (optional) gate classification override
                - "depends_on": list of task IDs this task depends on
            on_status: optional callback(stage, detail)

        Returns:
            {
                "results": {task_id: orchestrator_result_dict},
                "success": bool,  # True if ALL tasks succeeded
                "elapsed": float,
            }
        """
        start = time.time()

        if not tasks:
            return {"results": {}, "success": True, "elapsed": 0.0}

        # 验证并构建图
        task_map = {t["id"]: t for t in tasks}
        self._validate_graph(task_map)

        # 拓扑层（可并行执行的任务组）
        layers = self._topological_layers(task_map)

        results: dict[str, dict] = {}
        all_success = True

        for layer in layers:
            if len(layer) == 1:
                # 单任务，直接执行，无线程开销
                task_id = layer[0]
                task_def = task_map[task_id]
                result = self._execute_task(task_def, results, on_status)
                results[task_id] = result
                if not result["success"]:
                    all_success = False
            else:
                # 并行执行
                pool_size = min(len(layer), MAX_PARALLEL_WORKERS)
                with ThreadPoolExecutor(
                    max_workers=pool_size,
                    thread_name_prefix="task_compiler",
                ) as pool:
                    futures = {}
                    for task_id in layer:
                        task_def = task_map[task_id]
                        future = pool.submit(
                            self._execute_task, task_def, results, on_status
                        )
                        futures[future] = task_id

                    for future in as_completed(futures):
                        task_id = futures[future]
                        try:
                            result = future.result()
                        except Exception as e:
                            logger.error("Task %s raised: %s", task_id, e)
                            result = {
                                "success": False,
                                "context": None,
                                "error": str(e),
                                "elapsed": 0.0,
                            }
                        results[task_id] = result
                        if not result["success"]:
                            all_success = False

        elapsed = time.time() - start
        return {
            "results": results,
            "success": all_success,
            "elapsed": round(elapsed, 2),
        }

    def _execute_task(self, task_def: dict, completed: dict, on_status) -> dict:
        """Execute a single task via orchestrator.run()."""
        task_id = task_def["id"]
        user_input = task_def["input"]

        # 注入依赖上下文：将已完成任务输出追加到输入
        upstream_dict: dict = {}
        deps = task_def.get("depends_on", [])
        if deps:
            upstream_dict = self._build_dependency_context(deps, completed)
            if upstream_dict.get("modified_files"):
                upstream_text = self._format_upstream_text(upstream_dict)
                user_input = f"{user_input}\n\n[前置任务结果]\n{upstream_text}"

            # PENCIL式：用上游patch更新orchestrator的manifest
            self._update_manifest_from_deps(deps, completed)

        # Gate分类（使用覆盖值或自动分类）
        expert_type = task_def.get("expert_type")
        if expert_type:
            gate_result = {
                "expert_type": expert_type,
                "task_summary": user_input[:20],
                "difficulty": "easy",
            }
        else:
            gate_result = self.gate.classify(user_input)

        logger.info("[task_compiler] Executing task %s: %s", task_id, user_input[:50])

        result = self.orchestrator.run(
            user_input=user_input,
            gate_result=gate_result,
            project_root=self.project_root,
            on_status=on_status,
            skip_checkpoint=True,  # 问题4修复：多任务时跳过子任务级checkpoint，避免并行竞态
        )

        # 在context上存储结构化upstream_summary供下游访问
        if result.get("context") and upstream_dict:
            result["context"].upstream_summary = upstream_dict

        # PENCIL式压缩：只保留结构化产物给下游
        if result.get("success") and result.get("context"):
            result["_compact"] = self._compact_subtask_result(result)

        return result

    def _compact_subtask_result(self, result: dict) -> dict:
        """
        PENCIL-style compression: only keep structured artifacts for downstream.
        Discard: full reasoning chains, intermediate code snippets, debug logs.
        Keep: function signatures, constants, file paths, test status.
        """
        ctx = result.get("context")
        if not ctx:
            return {}

        patches = []
        if ctx.generator_output:
            patches = ctx.generator_output.get("patches", [])

        modified_files = [p.get("file", "") for p in patches if p.get("file")]

        # 从修改后的代码提取签名
        signatures = {}
        for patch in patches:
            modified = patch.get("modified", "")
            if modified:
                import re
                for m in re.finditer(r'def\s+(\w+)\s*\(([^)]*)\)', modified):
                    signatures[m.group(1)] = f"def {m.group(1)}({m.group(2)})"

        # Extract constants
        constants = {}
        for patch in patches:
            modified = patch.get("modified", "")
            if modified:
                import re
                for m in re.finditer(r'^([A-Z][A-Z_0-9]+)\s*=\s*(.+?)$', modified, re.MULTILINE):
                    constants[m.group(1)] = m.group(2).strip()[:80]

        test_status = ""
        if ctx.verifier_output:
            if ctx.verifier_output.get("passed"):
                tp = ctx.verifier_output.get("tests_passed", 0)
                tt = ctx.verifier_output.get("tests_total", 0)
                test_status = f"passed ({tp}/{tt})"
            else:
                test_status = "failed"

        return {
            "modified_files": modified_files,
            "signatures": signatures,
            "constants": constants,
            "test_status": test_status,
        }

    def _update_manifest_from_deps(self, dep_ids: list[str], completed: dict):
        """Update orchestrator's UpstreamManifest with patches from completed deps."""
        try:
            manifest = self.orchestrator._manifest
            for dep_id in dep_ids:
                result = completed.get(dep_id)
                if not result or not result.get("context"):
                    continue
                ctx = result["context"]
                if ctx.generator_output and ctx.generator_output.get("patches"):
                    manifest.update(ctx.generator_output["patches"])
        except Exception as e:
            logger.debug("[task_compiler] manifest update failed (non-blocking): %s", e)

    @staticmethod
    def _build_dependency_context(dep_ids: list[str], completed: dict) -> dict:
        """Build structured context dict from completed dependency results."""
        modified_files: list[str] = []
        diffs: dict[str, str] = {}
        new_symbols: list[str] = []
        broken_interfaces: list[str] = []

        for dep_id in dep_ids:
            result = completed.get(dep_id)
            if not result or not result.get("context"):
                continue
            ctx = result["context"]
            gen = ctx.generator_output
            if not gen or not gen.get("patches"):
                continue
            for patch in gen["patches"]:
                file_path = patch.get("file", "")
                if not file_path:
                    continue
                if file_path not in modified_files:
                    modified_files.append(file_path)
                # Collect diff (truncate to 200 lines)
                modified_code = patch.get("modified", "")
                if modified_code and file_path not in diffs:
                    lines = modified_code.splitlines()
                    if len(lines) > 200:
                        lines = lines[:200]
                        lines.append("... (truncated)")
                    diffs[file_path] = "\n".join(lines)
                # Extract new function/method symbols from modified code
                if modified_code:
                    for match in re.finditer(r"def\s+(\w+)\s*\(", modified_code):
                        symbol = match.group(1)
                        if symbol not in new_symbols:
                            new_symbols.append(symbol)

        return {
            "modified_files": modified_files,
            "diffs": diffs,
            "new_symbols": new_symbols,
            "broken_interfaces": broken_interfaces,
        }

    @staticmethod
    def _format_upstream_text(upstream_dict: dict) -> str:
        """Convert structured upstream dict to readable text for LLM injection."""
        parts = []
        modified = upstream_dict.get("modified_files", [])
        if modified:
            parts.append(f"修改文件: {', '.join(modified)}")
        new_symbols = upstream_dict.get("new_symbols", [])
        if new_symbols:
            parts.append(f"新增符号: {', '.join(new_symbols)}")
        broken = upstream_dict.get("broken_interfaces", [])
        if broken:
            parts.append(f"破坏接口: {', '.join(broken)}")
        diffs = upstream_dict.get("diffs", {})
        if diffs:
            parts.append("--- Diffs ---")
            for file_path, diff_text in diffs.items():
                parts.append(f"[{file_path}]\n{diff_text}")
        return "\n".join(parts)

    @staticmethod
    def _validate_graph(task_map: dict):
        """Validate task graph: check for missing dependencies."""
        for task_id, task_def in task_map.items():
            for dep in task_def.get("depends_on", []):
                if dep not in task_map:
                    raise ValueError(
                        f"Task '{task_id}' depends on '{dep}' which does not exist"
                    )

    @staticmethod
    def _topological_layers(task_map: dict) -> list[list[str]]:
        """
        Kahn's algorithm producing layers of parallel-executable tasks.
        Each layer contains tasks whose dependencies are all in previous layers.
        Raises CycleError if the graph has a cycle.
        """
        # Build adjacency and in-degree
        in_degree = {tid: 0 for tid in task_map}
        dependents = {tid: [] for tid in task_map}  # tid -> list of tasks that depend on it

        for tid, task_def in task_map.items():
            for dep in task_def.get("depends_on", []):
                in_degree[tid] += 1
                dependents[dep].append(tid)

        # Start with zero in-degree nodes
        queue = deque(tid for tid, deg in in_degree.items() if deg == 0)
        layers = []
        processed = 0

        while queue:
            # Current layer: all nodes with in_degree == 0
            layer = list(queue)
            queue.clear()
            layers.append(layer)
            processed += len(layer)

            for tid in layer:
                for dependent in dependents[tid]:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

        if processed != len(task_map):
            raise CycleError("Task DAG contains a cycle")

        return layers


# ── Worktree 隔离：/multi 并行任务文件隔离 ──
# 理论来源：CC Worktree isolation（arXiv:2604.14228）

class WorktreeManager:
    """
    并行任务文件隔离：每个子任务在独立工作目录执行，避免互相覆盖。
    - Git 项目：使用 git worktree
    - 非 Git 项目：使用 tempdir + copytree
    """

    def __init__(self, project_root: str):
        import os
        from pathlib import Path
        self.root = os.path.abspath(project_root)
        self._is_git = (Path(self.root) / ".git").exists()
        self._trees: dict[str, str] = {}

    def create(self, task_id: str) -> str:
        """为任务创建隔离工作目录，返回路径。"""
        import subprocess
        import shutil
        import tempfile
        from pathlib import Path

        short_id = task_id[:8]

        if not self._is_git:
            # 非 Git：复制到临时目录
            tmp = tempfile.mkdtemp(prefix=f"kwcode_{short_id}_")
            shutil.copytree(self.root, tmp, dirs_exist_ok=True)
            self._trees[task_id] = tmp
            return tmp

        # Git：使用 worktree
        branch = f"kwcode-{short_id}"
        path = str(Path(self.root).parent / f".kwcode_wt_{short_id}")
        try:
            subprocess.run(
                ["git", "worktree", "add", "-b", branch, path],
                cwd=self.root, check=True, capture_output=True,
            )
            self._trees[task_id] = path
            return path
        except subprocess.CalledProcessError:
            # worktree 失败时 fallback 到 copytree
            tmp = tempfile.mkdtemp(prefix=f"kwcode_{short_id}_")
            shutil.copytree(self.root, tmp, dirs_exist_ok=True)
            self._trees[task_id] = tmp
            return tmp

    def cleanup(self, task_id: str, merge: bool = False):
        """清理工作目录。merge=True 时合并变更回主分支。"""
        import subprocess
        import shutil

        path = self._trees.pop(task_id, None)
        if not path:
            return

        short_id = task_id[:8]

        if self._is_git:
            if merge:
                branch = f"kwcode-{short_id}"
                subprocess.run(
                    ["git", "merge", "--no-ff", branch],
                    cwd=self.root, capture_output=True,
                )
            subprocess.run(
                ["git", "worktree", "remove", "--force", path],
                cwd=self.root, capture_output=True,
            )
            # 清理分支
            if not merge:
                subprocess.run(
                    ["git", "branch", "-D", f"kwcode-{short_id}"],
                    cwd=self.root, capture_output=True,
                )
        else:
            shutil.rmtree(path, ignore_errors=True)

    def cleanup_all(self, merge: bool = False):
        """清理所有工作目录。"""
        for task_id in list(self._trees.keys()):
            self.cleanup(task_id, merge=merge)

    @property
    def active_count(self) -> int:
        return len(self._trees)

