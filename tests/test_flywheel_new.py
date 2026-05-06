"""
Tests for new flywheel modules: StrategyStats, UserPatternMemory, SkillDrafter, TelemetryClient.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestStrategyStats(unittest.TestCase):
    """Test StrategyStats record/get_best_sequence/persistence."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.stats_file = Path(self.tmpdir) / "strategy_stats.json"

    def tearDown(self):
        if self.stats_file.exists():
            self.stats_file.unlink()
        os.rmdir(self.tmpdir)

    @patch("kaiwu.flywheel.strategy_stats.STATS_FILE")
    def test_record_and_persistence(self, mock_file):
        mock_file.__class__ = type(self.stats_file)
        mock_file.exists = self.stats_file.exists
        mock_file.parent = self.stats_file.parent

        from kaiwu.flywheel.strategy_stats import StrategyStats

        with patch("kaiwu.flywheel.strategy_stats.STATS_FILE", self.stats_file):
            stats = StrategyStats()
            stats.record("syntax", ["generator", "verifier"], True, 1)
            assert stats._stats["syntax"]["generator_verifier"]["attempts"] == 1
            assert stats._stats["syntax"]["generator_verifier"]["successes"] == 1
            assert stats._stats["syntax"]["generator_verifier"]["success_rate"] == 1.0

            # Verify persisted to disk
            assert self.stats_file.exists()
            data = json.loads(self.stats_file.read_text(encoding="utf-8"))
            assert data["syntax"]["generator_verifier"]["attempts"] == 1

    @patch("kaiwu.flywheel.strategy_stats.STATS_FILE")
    def test_get_best_sequence_insufficient_data(self, mock_file):
        from kaiwu.flywheel.strategy_stats import StrategyStats

        with patch("kaiwu.flywheel.strategy_stats.STATS_FILE", self.stats_file):
            stats = StrategyStats()
            # With no data, should return default
            default = ["locator", "generator", "verifier"]
            result = stats.get_best_sequence("runtime", default)
            assert result == default

    @patch("kaiwu.flywheel.strategy_stats.STATS_FILE")
    def test_get_best_sequence_with_data(self, mock_file):
        from kaiwu.flywheel.strategy_stats import StrategyStats

        with patch("kaiwu.flywheel.strategy_stats.STATS_FILE", self.stats_file):
            stats = StrategyStats()
            # Record 15 attempts: sequence A has 80% success, B has 50%
            for i in range(12):
                stats.record("syntax", ["gen", "ver"], True, 1)
            for i in range(3):
                stats.record("syntax", ["gen", "ver"], False, 2)
            for i in range(5):
                stats.record("syntax", ["loc", "gen", "ver"], True, 1)
            for i in range(5):
                stats.record("syntax", ["loc", "gen", "ver"], False, 2)

            result = stats.get_best_sequence("syntax", ["default"], min_attempts=10)
            assert result == ["gen", "ver"]  # 80% > 50%

    @patch("kaiwu.flywheel.strategy_stats.STATS_FILE")
    def test_get_summary(self, mock_file):
        from kaiwu.flywheel.strategy_stats import StrategyStats

        with patch("kaiwu.flywheel.strategy_stats.STATS_FILE", self.stats_file):
            stats = StrategyStats()
            stats.record("runtime", ["gen", "ver"], True, 0)
            stats.record("runtime", ["gen", "ver"], False, 1)
            summary = stats.get_summary()
            assert "runtime" in summary
            assert summary["runtime"]["total_attempts"] == 2

    @patch("kaiwu.flywheel.strategy_stats.STATS_FILE")
    def test_corrupted_file_recovery(self, mock_file):
        from kaiwu.flywheel.strategy_stats import StrategyStats

        self.stats_file.parent.mkdir(parents=True, exist_ok=True)
        self.stats_file.write_text("not json", encoding="utf-8")

        with patch("kaiwu.flywheel.strategy_stats.STATS_FILE", self.stats_file):
            stats = StrategyStats()
            assert stats._stats == {}  # Recovered to empty


class TestUserPatternMemory(unittest.TestCase):
    """Test UserPatternMemory record/warnings/threshold."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patterns_file = Path(self.tmpdir) / "user_patterns.json"

    def tearDown(self):
        if self.patterns_file.exists():
            self.patterns_file.unlink()
        os.rmdir(self.tmpdir)

    @patch("kaiwu.flywheel.user_pattern_memory.USER_PATTERNS_FILE")
    def test_record_task(self, mock_file):
        from kaiwu.flywheel.user_pattern_memory import UserPatternMemory

        with patch("kaiwu.flywheel.user_pattern_memory.USER_PATTERNS_FILE", self.patterns_file):
            mem = UserPatternMemory()
            mem.record_task(["syntax", "runtime"], True)
            assert mem._data["total_tasks"] == 1
            assert mem._data["error_frequency"]["syntax"] == 1
            assert mem._data["error_frequency"]["runtime"] == 1
            assert mem._data["success_rate"] == 1.0

    @patch("kaiwu.flywheel.user_pattern_memory.USER_PATTERNS_FILE")
    def test_warning_hint_below_threshold(self, mock_file):
        from kaiwu.flywheel.user_pattern_memory import UserPatternMemory

        with patch("kaiwu.flywheel.user_pattern_memory.USER_PATTERNS_FILE", self.patterns_file):
            mem = UserPatternMemory()
            # Below 20 tasks, should not generate warnings
            for i in range(10):
                mem.record_task(["syntax"], True)
            assert mem.get_warning_hint() == ""

    @patch("kaiwu.flywheel.user_pattern_memory.USER_PATTERNS_FILE")
    def test_warning_hint_above_threshold(self, mock_file):
        from kaiwu.flywheel.user_pattern_memory import UserPatternMemory

        with patch("kaiwu.flywheel.user_pattern_memory.USER_PATTERNS_FILE", self.patterns_file):
            mem = UserPatternMemory()
            # Record 25 tasks with syntax errors
            for i in range(25):
                mem.record_task(["syntax"], True)
            hint = mem.get_warning_hint()
            assert "语法错误" in hint

    @patch("kaiwu.flywheel.user_pattern_memory.USER_PATTERNS_FILE")
    def test_top_errors_sorted(self, mock_file):
        from kaiwu.flywheel.user_pattern_memory import UserPatternMemory

        with patch("kaiwu.flywheel.user_pattern_memory.USER_PATTERNS_FILE", self.patterns_file):
            mem = UserPatternMemory()
            for i in range(10):
                mem.record_task(["runtime"], True)
            for i in range(5):
                mem.record_task(["import"], True)
            for i in range(3):
                mem.record_task(["syntax"], True)
            # runtime(10) > import(5) > syntax(3)
            assert mem._data["top_errors"][0] == "runtime"
            assert mem._data["top_errors"][1] == "import"

    @patch("kaiwu.flywheel.user_pattern_memory.USER_PATTERNS_FILE")
    def test_get_summary(self, mock_file):
        from kaiwu.flywheel.user_pattern_memory import UserPatternMemory

        with patch("kaiwu.flywheel.user_pattern_memory.USER_PATTERNS_FILE", self.patterns_file):
            mem = UserPatternMemory()
            mem.record_task(["syntax"], False)
            summary = mem.get_summary()
            assert summary["total_tasks"] == 1
            assert summary["success_rate"] == "0.0%"

    @patch("kaiwu.flywheel.user_pattern_memory.USER_PATTERNS_FILE")
    def test_unknown_errors_ignored(self, mock_file):
        from kaiwu.flywheel.user_pattern_memory import UserPatternMemory

        with patch("kaiwu.flywheel.user_pattern_memory.USER_PATTERNS_FILE", self.patterns_file):
            mem = UserPatternMemory()
            mem.record_task(["unknown", ""], True)
            assert "unknown" not in mem._data["error_frequency"]
            assert "" not in mem._data["error_frequency"]


class TestSkillDrafter(unittest.TestCase):
    """Test SkillDrafter draft generation and file operations."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_should_generate_draft_insufficient(self):
        from kaiwu.flywheel.skill_drafter import SkillDrafter

        mock_stats = MagicMock()
        mock_collector = MagicMock()
        mock_collector.load_recent.return_value = [{"success": True}] * 10  # Only 10

        drafter = SkillDrafter(mock_stats, mock_collector)
        assert not drafter.should_generate_draft()

    def test_should_generate_draft_sufficient(self):
        from kaiwu.flywheel.skill_drafter import SkillDrafter

        mock_stats = MagicMock()
        mock_collector = MagicMock()
        mock_collector.load_recent.return_value = [{"success": True}] * 35

        drafter = SkillDrafter(mock_stats, mock_collector)
        assert drafter.should_generate_draft()

    def test_generate_draft_content(self):
        from kaiwu.flywheel.skill_drafter import SkillDrafter

        mock_stats = MagicMock()
        mock_stats.get_summary.return_value = {
            "syntax": {"best_sequence": "gen_ver", "best_success_rate": "80.0%", "total_attempts": 15}
        }
        mock_collector = MagicMock()
        mock_collector.load_recent.return_value = [{"success": True}] * 35

        drafter = SkillDrafter(mock_stats, mock_collector)
        draft = drafter.generate_draft("locator_repair")
        assert draft is not None
        assert "syntax" in draft
        assert "80.0%" in draft
        assert "kwcode skill accept" in draft

    def test_save_and_exists(self):
        from kaiwu.flywheel.skill_drafter import SkillDrafter

        drafter = SkillDrafter(MagicMock(), MagicMock())
        assert not drafter.draft_exists(self.tmpdir)

        drafter.save_draft("# test draft", self.tmpdir)
        assert drafter.draft_exists(self.tmpdir)

        content = (Path(self.tmpdir) / ".kaiwu" / "skill_draft.md").read_text(encoding="utf-8")
        assert content == "# test draft"


class TestTelemetryClient(unittest.TestCase):
    """Test TelemetryClient config reading and fire-and-forget behavior."""

    def test_disabled_by_default(self):
        from kaiwu.telemetry.client import TelemetryClient

        with patch("kaiwu.telemetry.client.CONFIG_PATH") as mock_path:
            mock_path.exists.return_value = False
            client = TelemetryClient()
            assert not client.is_enabled()

    def test_enabled_from_config(self):
        tmpdir = tempfile.mkdtemp()
        config_file = Path(tmpdir) / "config.yaml"
        config_file.write_text("telemetry_enabled: true\n", encoding="utf-8")

        from kaiwu.telemetry.client import TelemetryClient

        with patch("kaiwu.telemetry.client.CONFIG_PATH", config_file):
            client = TelemetryClient()
            assert client.is_enabled()

        config_file.unlink()
        os.rmdir(tmpdir)

    def test_report_does_not_block(self):
        """report() should return immediately even if upload would fail."""
        from kaiwu.telemetry.client import TelemetryClient

        with patch("kaiwu.telemetry.client.CONFIG_PATH") as mock_path:
            mock_path.exists.return_value = False
            client = TelemetryClient()
            client._enabled = True

            with patch("kaiwu.telemetry.client.httpx") as mock_httpx:
                mock_httpx.post.side_effect = Exception("network error")
                # Should not raise
                client.report("syntax", 1, True, "qwen3:8b")

    def test_report_skipped_when_disabled(self):
        from kaiwu.telemetry.client import TelemetryClient

        with patch("kaiwu.telemetry.client.CONFIG_PATH") as mock_path:
            mock_path.exists.return_value = False
            client = TelemetryClient()
            assert not client.is_enabled()

            with patch("kaiwu.telemetry.client.httpx") as mock_httpx:
                client.report("syntax", 1, True, "qwen3:8b")
                mock_httpx.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
