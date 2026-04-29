"""
Lightweight DAG Task Compiler.
Accepts task definitions with dependencies, builds a DAG, executes via
ThreadPoolExecutor with topological ordering.

Zero new pip dependencies (ThreadPoolExecutor is stdlib).
Each task gets its own TaskContext (RED-3: independent context).
"""

import logging
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kaiwu.core.orchestrator import PipelineOrchestrator
    from kaiwu.core.gate import Gate

logger = logging.getLogger(__name__)

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

        # Validate and build graph
        task_map = {t["id"]: t for t in tasks}
        self._validate_graph(task_map)

        # Topological layers (groups of tasks that can run in parallel)
        layers = self._topological_layers(task_map)

        results: dict[str, dict] = {}
        all_success = True

        for layer in layers:
            if len(layer) == 1:
                # Single task — run directly, no thread overhead
                task_id = layer[0]
                task_def = task_map[task_id]
                result = self._execute_task(task_def, results, on_status)
                results[task_id] = result
                if not result["success"]:
                    all_success = False
            else:
                # Parallel execution
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

        # Inject dependency context: append completed task outputs to input
        deps = task_def.get("depends_on", [])
        if deps:
            dep_context = self._build_dependency_context(deps, completed)
            if dep_context:
                user_input = f"{user_input}\n\n[前置任务结果]\n{dep_context}"

        # Gate classification (use override or auto-classify)
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

        return self.orchestrator.run(
            user_input=user_input,
            gate_result=gate_result,
            project_root=self.project_root,
            on_status=on_status,
            skip_checkpoint=True,  # 问题4修复：多任务时跳过子任务级checkpoint，避免并行竞态
        )

    @staticmethod
    def _build_dependency_context(dep_ids: list[str], completed: dict) -> str:
        """Build context string from completed dependency results."""
        parts = []
        for dep_id in dep_ids:
            result = completed.get(dep_id)
            if not result or not result.get("context"):
                continue
            ctx = result["context"]
            # Extract explanation from generator output
            gen = ctx.generator_output
            if gen and gen.get("explanation"):
                parts.append(f"任务{dep_id}: {gen['explanation'][:200]}")
            elif gen and gen.get("patches"):
                files = [p.get("file", "") for p in gen["patches"]]
                parts.append(f"任务{dep_id}: 修改了 {', '.join(files)}")
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
