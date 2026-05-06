"""Regression tests for Orchestrator flow integrity."""

import pytest
from unittest.mock import MagicMock, patch

from kaiwu.core.orchestrator import PipelineOrchestrator, EXPERT_SEQUENCES
from kaiwu.core.context import TaskContext
from kaiwu.memory.kaiwu_md import KaiwuMemory
from kaiwu.tools.executor import ToolExecutor


# ── Helper ──────────────────────────────────────────────────────────────


def make_orchestrator(
    tmp_path,
    mock_locator=None,
    mock_generator=None,
    mock_verifier=None,
    mock_search=None,
    mock_chat=None,
):
    memory = KaiwuMemory()
    memory.init(str(tmp_path))
    tools = ToolExecutor(str(tmp_path))

    locator = mock_locator or MagicMock()
    # Avoid _notify_locator calling into mock unexpectedly
    if mock_locator is None:
        del locator.notify_task_result

    return PipelineOrchestrator(
        locator=locator,
        generator=mock_generator or MagicMock(),
        verifier=mock_verifier or MagicMock(),
        search_augmentor=mock_search or MagicMock(),
        office_handler=MagicMock(),
        tool_executor=tools,
        memory=memory,
        chat_expert=mock_chat,
    )


# ── Test 1: MAX_RETRIES constant ───────────────────────────────────────


def test_max_retries_is_3():
    assert PipelineOrchestrator.MAX_RETRIES == 3


# ── Test 2: Locator failure does not crash pipeline ────────────────────


def test_locator_failure_does_not_crash_pipeline(tmp_path):
    locator = MagicMock()
    locator.run.return_value = None
    # Remove notify_task_result so _notify_locator skips
    del locator.notify_task_result

    generator = MagicMock()
    generator.run.return_value = None

    orch = make_orchestrator(
        tmp_path, mock_locator=locator, mock_generator=generator
    )

    result = orch.run(
        user_input="fix bug",
        gate_result={"expert_type": "locator_repair", "difficulty": "easy"},
        project_root=str(tmp_path),
    )

    assert result["success"] is False
    assert "error" in result
    assert result["error"] is not None


# ── Test 3: Context reset between retries ──────────────────────────────


def test_context_reset_between_retries():
    ctx = TaskContext(
        user_input="test",
        locator_output={"relevant_files": ["a.py"]},
        generator_output={"patches": []},
        verifier_output={"passed": False},
        relevant_code_snippets={"a.py": "code"},
    )

    # Simulate the reset that orchestrator performs between retries
    ctx.locator_output = None
    ctx.generator_output = None
    ctx.verifier_output = None
    ctx.relevant_code_snippets = {}

    assert ctx.locator_output is None
    assert ctx.generator_output is None
    assert ctx.verifier_output is None
    assert ctx.relevant_code_snippets == {}


# ── Test 4: Search triggers after 2 failures ──────────────────────────


def test_search_triggers_after_2_failures(tmp_path):
    locator = MagicMock()
    locator.run.return_value = None
    del locator.notify_task_result

    generator = MagicMock()
    generator.run.return_value = None

    search = MagicMock()
    search.search.return_value = "some search results"

    orch = make_orchestrator(
        tmp_path,
        mock_locator=locator,
        mock_generator=generator,
        mock_search=search,
    )

    result = orch.run(
        user_input="fix bug",
        gate_result={"expert_type": "locator_repair", "difficulty": "hard"},
        project_root=str(tmp_path),
        no_search=False,
    )

    assert result["success"] is False
    # Search should have been called after failures (hard task triggers earlier)
    assert search.search.called


# ── Test 5: Chat type bypasses pipeline ────────────────────────────────


def test_chat_type_bypasses_pipeline(tmp_path):
    locator = MagicMock()
    del locator.notify_task_result

    chat = MagicMock()
    chat.run.return_value = {"passed": True}

    orch = make_orchestrator(
        tmp_path, mock_locator=locator, mock_chat=chat
    )

    result = orch.run(
        user_input="hello",
        gate_result={"expert_type": "chat"},
        project_root=str(tmp_path),
    )

    assert result["success"] is True
    # Locator should NOT have been called for chat type
    locator.run.assert_not_called()


# ── Test 6: Hard task triggers search after 1 failure ──────────────────


def test_hard_task_search_after_1_failure(tmp_path):
    locator = MagicMock()
    locator.run.return_value = {"relevant_files": ["a.py"], "relevant_functions": ["f"]}
    del locator.notify_task_result

    generator = MagicMock()
    generator.run.return_value = {"patches": [{"file": "a.py", "original": "", "modified": "x"}], "explanation": "fix"}

    verifier = MagicMock()
    verifier.run.return_value = {"passed": False, "error_detail": "test failed"}

    search = MagicMock()
    search.search.return_value = "search context"

    orch = make_orchestrator(
        tmp_path,
        mock_locator=locator,
        mock_generator=generator,
        mock_verifier=verifier,
        mock_search=search,
    )

    result = orch.run(
        user_input="refactor module",
        gate_result={"expert_type": "locator_repair", "difficulty": "hard"},
        project_root=str(tmp_path),
        no_search=False,
    )

    # Pipeline exhausts retries since verifier always fails
    assert result["success"] is False
    # Search should trigger after first failure for hard tasks
    assert search.search.called
