"""
Tests for TaskCompiler: lightweight DAG task scheduler.
Tests serial (dependency chain) and parallel (independent tasks) scenarios.
Uses mock orchestrator since Ollama may not be running.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from kaiwu.core.task_compiler import TaskCompiler, CycleError


# ── Fixtures ──

def _make_mock_orchestrator():
    """Create a mock orchestrator that returns success with realistic context."""
    orch = MagicMock()

    def mock_run(user_input, gate_result, project_root, on_status=None, no_search=False, skip_checkpoint=False):
        # Simulate some work
        time.sleep(0.05)
        ctx = MagicMock()
        ctx.generator_output = {
            "patches": [{"file": "src/main.py", "original": "old", "modified": "new"}],
            "explanation": f"Completed: {user_input[:30]}",
        }
        ctx.user_input = user_input
        return {
            "success": True,
            "context": ctx,
            "error": None,
            "elapsed": 0.05,
        }

    orch.run = MagicMock(side_effect=mock_run)
    return orch


def _make_mock_gate():
    """Create a mock gate that classifies everything as codegen."""
    gate = MagicMock()
    gate.classify = MagicMock(return_value={
        "expert_type": "codegen",
        "task_summary": "test task",
        "difficulty": "easy",
    })
    return gate


# ── Serial Tests ──

class TestTaskCompilerSerial:
    """Serial scenario: refactor → write tests (t2 depends on t1)."""

    def test_serial_execution_order(self):
        """t2 must execute after t1 completes."""
        orch = _make_mock_orchestrator()
        gate = _make_mock_gate()
        compiler = TaskCompiler(orchestrator=orch, gate=gate, project_root="/tmp/test")

        tasks = [
            {"id": "t1", "input": "refactor extract_data into two functions", "depends_on": []},
            {"id": "t2", "input": "write tests for the new functions", "depends_on": ["t1"]},
        ]

        result = compiler.compile_and_run(tasks)

        assert result["success"] is True
        assert "t1" in result["results"]
        assert "t2" in result["results"]
        assert result["results"]["t1"]["success"] is True
        assert result["results"]["t2"]["success"] is True

        # Verify t1 was called before t2 (check call order)
        calls = orch.run.call_args_list
        assert len(calls) == 2
        # First call should be t1's input
        assert "refactor" in calls[0].kwargs.get("user_input", calls[0][1]["user_input"] if len(calls[0]) > 1 else calls[0][0][0])

    def test_serial_context_injection(self):
        """t2 should receive t1's output in its input."""
        orch = _make_mock_orchestrator()
        gate = _make_mock_gate()
        compiler = TaskCompiler(orchestrator=orch, gate=gate, project_root="/tmp/test")

        tasks = [
            {"id": "t1", "input": "refactor extract_data", "depends_on": []},
            {"id": "t2", "input": "write tests", "depends_on": ["t1"]},
        ]

        result = compiler.compile_and_run(tasks)
        assert result["success"] is True

        # t2's user_input should contain dependency context
        calls = orch.run.call_args_list
        t2_input = calls[1][1]["user_input"] if "user_input" in (calls[1][1] if len(calls[1]) > 1 else {}) else calls[1].kwargs.get("user_input", "")
        assert "前置任务结果" in t2_input

    def test_serial_failure_propagation(self):
        """If t1 fails, t2 still runs but without dependency context."""
        orch = MagicMock()
        call_count = [0]

        def mock_run(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # t1 fails
                ctx = MagicMock()
                ctx.generator_output = None
                return {"success": False, "context": ctx, "error": "failed", "elapsed": 0.1}
            else:
                # t2 succeeds
                ctx = MagicMock()
                ctx.generator_output = {"patches": [], "explanation": "done"}
                return {"success": True, "context": ctx, "error": None, "elapsed": 0.1}

        orch.run = MagicMock(side_effect=mock_run)
        gate = _make_mock_gate()
        compiler = TaskCompiler(orchestrator=orch, gate=gate, project_root="/tmp/test")

        tasks = [
            {"id": "t1", "input": "task1", "depends_on": []},
            {"id": "t2", "input": "task2", "depends_on": ["t1"]},
        ]

        result = compiler.compile_and_run(tasks)
        # Overall should be False because t1 failed
        assert result["success"] is False
        assert result["results"]["t1"]["success"] is False
        assert result["results"]["t2"]["success"] is True


# ── Parallel Tests ──

class TestTaskCompilerParallel:
    """Parallel scenario: 3 independent tasks run concurrently."""

    def test_parallel_all_succeed(self):
        """Three independent tasks should all succeed."""
        orch = _make_mock_orchestrator()
        gate = _make_mock_gate()
        compiler = TaskCompiler(orchestrator=orch, gate=gate, project_root="/tmp/test")

        tasks = [
            {"id": "t1", "input": "add comments to function_a", "depends_on": []},
            {"id": "t2", "input": "add comments to function_b", "depends_on": []},
            {"id": "t3", "input": "add comments to function_c", "depends_on": []},
        ]

        result = compiler.compile_and_run(tasks)

        assert result["success"] is True
        assert len(result["results"]) == 3
        for tid in ["t1", "t2", "t3"]:
            assert result["results"][tid]["success"] is True

    def test_parallel_faster_than_serial(self):
        """Parallel execution should be faster than serial (3 * 50ms > total)."""
        orch = _make_mock_orchestrator()
        gate = _make_mock_gate()
        compiler = TaskCompiler(orchestrator=orch, gate=gate, project_root="/tmp/test")

        tasks = [
            {"id": "t1", "input": "task a", "depends_on": []},
            {"id": "t2", "input": "task b", "depends_on": []},
            {"id": "t3", "input": "task c", "depends_on": []},
        ]

        result = compiler.compile_and_run(tasks)

        # Each task takes ~50ms. Serial would be ~150ms. Parallel should be ~50-80ms.
        assert result["elapsed"] < 0.15, f"Parallel took {result['elapsed']}s, expected < 0.15s"

    def test_parallel_with_expert_type_override(self):
        """Tasks with explicit expert_type should skip gate classification."""
        orch = _make_mock_orchestrator()
        gate = _make_mock_gate()
        compiler = TaskCompiler(orchestrator=orch, gate=gate, project_root="/tmp/test")

        tasks = [
            {"id": "t1", "input": "add docstring", "expert_type": "doc", "depends_on": []},
            {"id": "t2", "input": "fix bug", "expert_type": "locator_repair", "depends_on": []},
        ]

        result = compiler.compile_and_run(tasks)
        assert result["success"] is True

        # Gate should NOT have been called (expert_type was pre-specified)
        gate.classify.assert_not_called()


# ── Validation Tests ──

class TestTaskCompilerValidation:
    """Edge cases and error handling."""

    def test_empty_task_list(self):
        """Empty task list should return immediately."""
        orch = _make_mock_orchestrator()
        gate = _make_mock_gate()
        compiler = TaskCompiler(orchestrator=orch, gate=gate, project_root="/tmp/test")

        result = compiler.compile_and_run([])
        assert result["success"] is True
        assert result["results"] == {}

    def test_single_task(self):
        """Single task with no dependencies."""
        orch = _make_mock_orchestrator()
        gate = _make_mock_gate()
        compiler = TaskCompiler(orchestrator=orch, gate=gate, project_root="/tmp/test")

        tasks = [{"id": "only", "input": "do something", "depends_on": []}]
        result = compiler.compile_and_run(tasks)
        assert result["success"] is True
        assert "only" in result["results"]

    def test_missing_dependency_raises(self):
        """Referencing a non-existent dependency should raise ValueError."""
        orch = _make_mock_orchestrator()
        gate = _make_mock_gate()
        compiler = TaskCompiler(orchestrator=orch, gate=gate, project_root="/tmp/test")

        tasks = [
            {"id": "t1", "input": "task", "depends_on": ["nonexistent"]},
        ]

        with pytest.raises(ValueError, match="does not exist"):
            compiler.compile_and_run(tasks)

    def test_cycle_detection(self):
        """Circular dependencies should raise CycleError."""
        orch = _make_mock_orchestrator()
        gate = _make_mock_gate()
        compiler = TaskCompiler(orchestrator=orch, gate=gate, project_root="/tmp/test")

        tasks = [
            {"id": "t1", "input": "task1", "depends_on": ["t2"]},
            {"id": "t2", "input": "task2", "depends_on": ["t1"]},
        ]

        with pytest.raises(CycleError):
            compiler.compile_and_run(tasks)

    def test_gate_auto_classify(self):
        """Tasks without expert_type should use gate.classify()."""
        orch = _make_mock_orchestrator()
        gate = _make_mock_gate()
        compiler = TaskCompiler(orchestrator=orch, gate=gate, project_root="/tmp/test")

        tasks = [{"id": "t1", "input": "fix the bug in login", "depends_on": []}]
        result = compiler.compile_and_run(tasks)
        assert result["success"] is True
        gate.classify.assert_called_once()

    def test_diamond_dependency(self):
        """Diamond DAG: t1 → t2, t1 → t3, t2+t3 → t4."""
        orch = _make_mock_orchestrator()
        gate = _make_mock_gate()
        compiler = TaskCompiler(orchestrator=orch, gate=gate, project_root="/tmp/test")

        tasks = [
            {"id": "t1", "input": "setup", "depends_on": []},
            {"id": "t2", "input": "branch a", "depends_on": ["t1"]},
            {"id": "t3", "input": "branch b", "depends_on": ["t1"]},
            {"id": "t4", "input": "merge", "depends_on": ["t2", "t3"]},
        ]

        result = compiler.compile_and_run(tasks)
        assert result["success"] is True
        assert len(result["results"]) == 4
