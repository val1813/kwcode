"""
Tests for audit logger, model commands, and _align_indentation fix.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestAuditLogger(unittest.TestCase):
    """Test AuditLogger write/list/show/clear."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.logs_dir = Path(self.tmpdir) / "logs"

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    @patch("kaiwu.audit.logger.LOGS_DIR")
    def test_write_creates_log(self, mock_dir):
        mock_dir.__class__ = type(self.logs_dir)
        from kaiwu.audit.logger import AuditLogger
        with patch("kaiwu.audit.logger.LOGS_DIR", self.logs_dir):
            logger = AuditLogger()
            logger.start()
            logger.log("gate", "locator_repair | 难度：easy")
            logger.log("locator", "读取 test.py")

            # Mock context
            ctx = MagicMock()
            ctx.user_input = "修复login函数"
            ctx.gate_result = {"expert_type": "locator_repair", "difficulty": "easy"}
            ctx.generator_output = {"patches": [{"file": "test.py", "original": "old", "modified": "new"}]}
            ctx.verifier_output = {"tests_passed": 3, "tests_total": 3}
            ctx.retry_count = 0
            ctx.search_triggered = False

            logger.write(ctx, 5.2, True, "qwen3:8b")

            # Verify log file created
            logs = list(self.logs_dir.glob("*.json"))
            assert len(logs) == 1

            data = json.loads(logs[0].read_text(encoding="utf-8"))
            assert data["task"] == "修复login函数"
            assert data["success"] is True
            assert data["model"] == "qwen3:8b"
            assert len(data["events"]) == 2
            assert data["files_modified"] == ["test.py"]

    @patch("kaiwu.audit.logger.LOGS_DIR")
    def test_list_logs(self, mock_dir):
        from kaiwu.audit.logger import list_logs
        with patch("kaiwu.audit.logger.LOGS_DIR", self.logs_dir):
            self.logs_dir.mkdir(parents=True, exist_ok=True)

            # Write 3 log files with distinct names
            for i in range(3):
                record = {"task": f"task {i}", "success": True, "elapsed_s": 1.0,
                           "timestamp": f"2026-05-06T10:00:0{i}", "model": "test"}
                (self.logs_dir / f"2026-05-06_10000{i}_codegen.json").write_text(
                    json.dumps(record), encoding="utf-8"
                )

            logs = list_logs(limit=10)
            assert len(logs) == 3

    @patch("kaiwu.audit.logger.LOGS_DIR")
    def test_clear_logs(self, mock_dir):
        from kaiwu.audit.logger import AuditLogger, clear_logs
        with patch("kaiwu.audit.logger.LOGS_DIR", self.logs_dir):
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            (self.logs_dir / "test.json").write_text("{}", encoding="utf-8")
            count = clear_logs()
            assert count == 1
            assert len(list(self.logs_dir.glob("*.json"))) == 0

    @patch("kaiwu.audit.logger.LOGS_DIR")
    def test_max_logs_cleanup(self, mock_dir):
        from kaiwu.audit.logger import AuditLogger, MAX_LOGS
        with patch("kaiwu.audit.logger.LOGS_DIR", self.logs_dir):
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            # Create MAX_LOGS + 5 files
            for i in range(MAX_LOGS + 5):
                (self.logs_dir / f"2026-01-01_{i:06d}_test.json").write_text("{}", encoding="utf-8")

            al = AuditLogger()
            al._cleanup()

            remaining = list(self.logs_dir.glob("*.json"))
            assert len(remaining) == MAX_LOGS


class TestAlignIndentation(unittest.TestCase):
    """Test Generator._align_indentation — the class method indentation bug fix."""

    def test_class_method_indent_fix(self):
        from kaiwu.experts.generator import GeneratorExpert
        # Original is class method (4-space indent)
        original = "    def login(self, user):\n        return True"
        # LLM returns without class indent
        modified = "def login(self, user):\n    if not user:\n        return False\n    return True"

        result = GeneratorExpert._align_indentation(original, modified)
        # Should add 4 spaces to all non-empty lines
        lines = result.split("\n")
        assert lines[0] == "    def login(self, user):"
        assert lines[1] == "        if not user:"
        assert lines[2] == "            return False"
        assert lines[3] == "        return True"

    def test_no_change_needed(self):
        from kaiwu.experts.generator import GeneratorExpert
        original = "def foo():\n    return 1"
        modified = "def foo():\n    return 2"
        result = GeneratorExpert._align_indentation(original, modified)
        assert result == modified

    def test_already_more_indented(self):
        from kaiwu.experts.generator import GeneratorExpert
        original = "def foo():\n    return 1"
        modified = "    def foo():\n        return 2"
        result = GeneratorExpert._align_indentation(original, modified)
        # Should not change — modified already more indented
        assert result == modified

    def test_empty_lines_preserved(self):
        from kaiwu.experts.generator import GeneratorExpert
        original = "    def foo():\n\n        return 1"
        modified = "def foo():\n\n    return 2"
        result = GeneratorExpert._align_indentation(original, modified)
        lines = result.split("\n")
        assert lines[0] == "    def foo():"
        assert lines[1] == ""  # Empty line stays empty
        assert lines[2] == "        return 2"

    def test_8_space_indent(self):
        from kaiwu.experts.generator import GeneratorExpert
        # Nested class method (8-space indent)
        original = "        def inner(self):\n            pass"
        modified = "def inner(self):\n    return 42"
        result = GeneratorExpert._align_indentation(original, modified)
        assert result.startswith("        def inner(self):")
        assert "            return 42" in result


class TestHashlinePrompt(unittest.TestCase):
    """Verify HASHLINE_PROMPT exists and has required format markers."""

    def test_prompt_exists(self):
        from kaiwu.experts.generator import HASHLINE_PROMPT
        assert "{task_description}" in HASHLINE_PROMPT
        assert "{anchored_code}" in HASHLINE_PROMPT
        assert "EDIT" in HASHLINE_PROMPT
        assert "DELETE" in HASHLINE_PROMPT
        assert "INSERT_AFTER" in HASHLINE_PROMPT


if __name__ == "__main__":
    unittest.main()
