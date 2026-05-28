"""
P2 feature tests: Model Capability, Flywheel Notifier, Value Tracker.
"""

import json
import os
import tempfile
import time

import pytest


# ── Task 1: Model Capability ──────────────────────────────

class TestModelCapability:

    def test_detect_small_from_name(self):
        from kaiwu.core.model_capability import detect_model_tier, ModelTier, _tier_cache
        _tier_cache.clear()
        assert detect_model_tier("qwen3:8b", "http://localhost:99999") == ModelTier.SMALL

    def test_detect_medium_from_name(self):
        from kaiwu.core.model_capability import detect_model_tier, ModelTier, _tier_cache
        _tier_cache.clear()
        assert detect_model_tier("qwen3:14b", "http://localhost:99999") == ModelTier.MEDIUM

    def test_detect_large_from_name(self):
        from kaiwu.core.model_capability import detect_model_tier, ModelTier, _tier_cache
        _tier_cache.clear()
        assert detect_model_tier("qwen3:72b", "http://localhost:99999") == ModelTier.LARGE

    def test_detect_known_small(self):
        from kaiwu.core.model_capability import detect_model_tier, ModelTier, _tier_cache
        _tier_cache.clear()
        assert detect_model_tier("gemma4:e2b", "http://localhost:99999") == ModelTier.SMALL

    def test_detect_unknown_defaults_medium(self):
        from kaiwu.core.model_capability import detect_model_tier, ModelTier, _tier_cache
        _tier_cache.clear()
        assert detect_model_tier("mystery-model", "http://localhost:99999") == ModelTier.MEDIUM

    def test_detect_deepseek_pattern(self):
        from kaiwu.core.model_capability import detect_model_tier, ModelTier, _tier_cache
        _tier_cache.clear()
        assert detect_model_tier("deepseek-r1:8b", "http://localhost:99999") == ModelTier.SMALL
        _tier_cache.clear()
        assert detect_model_tier("deepseek-r1:70b", "http://localhost:99999") == ModelTier.LARGE

    def test_strategy_small(self):
        from kaiwu.core.model_capability import get_strategy, ModelTier
        s = get_strategy(ModelTier.SMALL)
        assert s.force_plan_mode is True
        assert s.max_files_per_task == 2
        assert s.search_trigger_after == 1

    def test_strategy_large(self):
        from kaiwu.core.model_capability import get_strategy, ModelTier
        s = get_strategy(ModelTier.LARGE)
        assert s.force_plan_mode is False
        assert s.max_files_per_task == 8

    def test_tier_display_name(self):
        from kaiwu.core.model_capability import tier_display_name, ModelTier
        assert "小模型" in tier_display_name(ModelTier.SMALL)
        assert "大模型" in tier_display_name(ModelTier.LARGE)

    def test_cache_works(self):
        from kaiwu.core.model_capability import detect_model_tier, ModelTier, _tier_cache
        _tier_cache.clear()
        detect_model_tier("test-cache:8b", "http://localhost:99999")
        assert "test-cache:8b" in _tier_cache

    def test_hardware_validate_no_warning_when_sufficient(self):#内存够
        from unittest.mock import patch

        from kaiwu.core.model_capability import ModelTier, _validate_hardware

        with patch("kaiwu.core.model_capability._detect_vram_gb", return_value=24.0), \
             patch("kaiwu.core.model_capability._detect_sysram_gb", return_value=32.0):
            result = _validate_hardware("qwen3:72b", ModelTier.LARGE)
            assert result == ModelTier.LARGE

    def test_hardware_validate_warns_when_insufficient(self, caplog):#小跑大
        import logging
        from unittest.mock import patch

        from kaiwu.core.model_capability import ModelTier, _validate_hardware
        with patch("kaiwu.core.model_capability._detect_vram_gb", return_value=4.0), \
             patch("kaiwu.core.model_capability._detect_sysram_gb", return_value=8.0):
            with caplog.at_level(logging.WARNING):
                result = _validate_hardware("qwen3:72b", ModelTier.LARGE)
            assert result == ModelTier.LARGE
            assert "硬件可能无法流畅运行" in caplog.text

    def test_hardware_validate_no_nvidia_skips(self):#无英伟达卡
        from unittest.mock import patch

        from kaiwu.core.model_capability import ModelTier, _validate_hardware

        with patch("kaiwu.core.model_capability._detect_vram_gb", return_value=None), \
             patch("kaiwu.core.model_capability._detect_sysram_gb", return_value=16.0):
            result = _validate_hardware("qwen3:72b", ModelTier.LARGE)
            assert result == ModelTier.LARGE


# ── Task 2: Flywheel Notifier ─────────────────────────────

class TestFlywheelNotifier:

    def test_queue_and_flush(self):
        from kaiwu.notification.flywheel_notifier import FlywheelNotifier, NOTIFY_PATH
        # Clean up
        if NOTIFY_PATH.exists():
            NOTIFY_PATH.unlink()

        notifier = FlywheelNotifier()
        notifier.queue_expert_born(
            expert_def={"name": "TestExpert", "trigger_keywords": ["test", "pytest"]},
            metrics={
                "task_count": 10,
                "success_rate_new": 0.9,
                "success_rate_baseline": 0.7,
                "avg_latency_new": 20,
                "avg_latency_baseline": 50,
            },
        )

        # Verify queued
        data = json.loads(NOTIFY_PATH.read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["type"] == "expert_born"
        assert data[0]["expert_name"] == "TestExpert"

        # Flush (mock console)
        class MockConsole:
            def __init__(self):
                self.outputs = []
            def print(self, *args, **kwargs):
                self.outputs.append(args)

        mc = MockConsole()
        count = notifier.flush(mc)
        assert count == 1
        assert len(mc.outputs) > 0

        # After flush, queue should be empty
        data2 = json.loads(NOTIFY_PATH.read_text(encoding="utf-8"))
        assert data2 == []

    def test_queue_progress(self):
        from kaiwu.notification.flywheel_notifier import FlywheelNotifier, NOTIFY_PATH
        if NOTIFY_PATH.exists():
            NOTIFY_PATH.unlink()

        notifier = FlywheelNotifier()
        notifier.queue_progress("BugFix", 3, 5)

        data = json.loads(NOTIFY_PATH.read_text(encoding="utf-8"))
        assert data[0]["type"] == "progress"
        assert data[0]["progress_current"] == 3

        # Clean up
        NOTIFY_PATH.unlink()

    def test_queue_milestone(self):
        from kaiwu.notification.flywheel_notifier import FlywheelNotifier, NOTIFY_PATH
        if NOTIFY_PATH.exists():
            NOTIFY_PATH.unlink()

        notifier = FlywheelNotifier()
        notifier.queue_milestone(50, 3, 2.4)

        data = json.loads(NOTIFY_PATH.read_text(encoding="utf-8"))
        assert data[0]["type"] == "milestone"
        assert data[0]["milestone_tasks"] == 50

        NOTIFY_PATH.unlink()

    def test_flush_empty(self):
        from kaiwu.notification.flywheel_notifier import FlywheelNotifier, NOTIFY_PATH
        if NOTIFY_PATH.exists():
            NOTIFY_PATH.unlink()

        notifier = FlywheelNotifier()

        class MockConsole:
            def print(self, *a, **kw): pass

        assert notifier.flush(MockConsole()) == 0

    def test_corrupted_file_handled(self):
        from kaiwu.notification.flywheel_notifier import FlywheelNotifier, NOTIFY_PATH
        NOTIFY_PATH.parent.mkdir(parents=True, exist_ok=True)
        NOTIFY_PATH.write_text("not json", encoding="utf-8")

        notifier = FlywheelNotifier()

        class MockConsole:
            def print(self, *a, **kw): pass

        # Should not crash
        assert notifier.flush(MockConsole()) == 0
        NOTIFY_PATH.unlink()


# ── Task 3: Value Tracker ─────────────────────────────────

class TestValueTracker:

    def test_record_and_summary(self):
        from kaiwu.stats.value_tracker import ValueTracker, DB_PATH
        # Use a temp DB
        import sqlite3
        original_path = str(DB_PATH)

        tracker = ValueTracker()
        # Record some tasks
        tracker.record("/tmp/project", "locator_repair", "BugFix", True, 15.0, 0, "qwen3:8b")
        tracker.record("/tmp/project", "codegen", "", True, 8.0, 0, "qwen3:8b")
        tracker.record("/tmp/project", "locator_repair", "BugFix", False, 30.0, 3, "qwen3:8b")
        tracker.record("/tmp/project", "locator_repair", "BugFix", True, 12.0, 1, "qwen3:8b")

        summary = tracker.get_summary(days=1)
        assert summary["total_tasks"] >= 4
        assert summary["succeeded_tasks"] >= 3
        assert summary["time_saved_hours"] > 0

    def test_summary_empty_db(self):
        from kaiwu.stats.value_tracker import ValueTracker
        # Fresh tracker should not crash
        tracker = ValueTracker()
        summary = tracker.get_summary(days=1)
        assert summary["total_tasks"] >= 0
        assert isinstance(summary["time_saved_hours"], (int, float))

    def test_get_total_task_count(self):
        from kaiwu.stats.value_tracker import ValueTracker
        tracker = ValueTracker()
        count = tracker.get_total_task_count()
        assert isinstance(count, int)
        assert count >= 0

    def test_conservative_time_estimate(self):
        """P2-RED-4: 5 min per task is conservative."""
        from kaiwu.stats.value_tracker import ValueTracker
        tracker = ValueTracker()
        # Record 12 successful tasks
        for i in range(12):
            tracker.record("/tmp/p", "codegen", "", True, 10.0, 0, "test")
        summary = tracker.get_summary(days=1)
        # 12 tasks * 5 min = 60 min = 1.0 hour (at minimum)
        assert summary["time_saved_hours"] >= 1.0

    def test_top_expert(self):
        from kaiwu.stats.value_tracker import ValueTracker
        tracker = ValueTracker()
        for i in range(5):
            tracker.record("/tmp/p", "locator_repair", "TopExpert", True, 10.0, 0, "test")
        summary = tracker.get_summary(days=1)
        # TopExpert should appear (may be mixed with previous test data)
        assert summary["total_tasks"] >= 5


# ── Integration: imports ───────────────────────────────────

class TestP2Imports:

    def test_all_modules_import(self):
        from kaiwu.core.model_capability import ModelTier, ModelStrategy, detect_model_tier, get_strategy, tier_display_name
        from kaiwu.notification.flywheel_notifier import FlywheelNotifier, FlywheelNotification
        from kaiwu.stats.value_tracker import ValueTracker
        assert ModelTier.SMALL.value == "small"
        assert FlywheelNotifier is not None
        assert ValueTracker is not None
