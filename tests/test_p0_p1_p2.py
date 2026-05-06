"""
Tests for P0 Hashline, P1 AdaptThink, P2 Fast/Slow.
"""

import unittest


class TestHashline(unittest.TestCase):
    """P0: Hashline anchor editing."""

    def test_add_anchors(self):
        from kaiwu.tools.hashline import add_anchors
        code = "def hello():\n    return 42"
        result = add_anchors(code)
        lines = result.split("\n")
        assert len(lines) == 2
        assert lines[0].startswith("1|")
        assert "| def hello():" in lines[0]
        # Hash is 6 chars hex
        import re
        assert re.match(r'1\|[a-f0-9]{6}\| def hello\(\):', lines[0])

    def test_strip_anchors(self):
        from kaiwu.tools.hashline import add_anchors, strip_anchors
        original = "def foo():\n    x = 1\n    return x"
        anchored = add_anchors(original)
        restored = strip_anchors(anchored)
        assert restored == original

    def test_parse_edit_instruction(self):
        from kaiwu.tools.hashline import parse_anchor_edits
        raw = "EDIT 3|abc123| → return x + 1"
        edits = parse_anchor_edits(raw)
        assert len(edits) == 1
        assert edits[0]["action"] == "edit"
        assert edits[0]["line"] == 3
        assert edits[0]["hash"] == "abc123"
        assert edits[0]["content"] == "return x + 1"

    def test_parse_delete_instruction(self):
        from kaiwu.tools.hashline import parse_anchor_edits
        raw = "DELETE 5|def456|"
        edits = parse_anchor_edits(raw)
        assert len(edits) == 1
        assert edits[0]["action"] == "delete"

    def test_parse_insert_after(self):
        from kaiwu.tools.hashline import parse_anchor_edits
        raw = "INSERT_AFTER 7|aaa111| → new_line = True"
        edits = parse_anchor_edits(raw)
        assert len(edits) == 1
        assert edits[0]["action"] == "insert_after"

    def test_apply_edit_success(self):
        from kaiwu.tools.hashline import apply_anchor_edits, _line_hash
        code = "def foo():\n    return 1"
        lines = code.split("\n")
        h = _line_hash(lines[1])  # hash of "    return 1"
        edits = [{"action": "edit", "line": 2, "hash": h, "content": "    return 42"}]
        result, errors = apply_anchor_edits(code, edits)
        assert not errors
        assert "return 42" in result
        assert "return 1" not in result

    def test_apply_edit_hash_mismatch(self):
        from kaiwu.tools.hashline import apply_anchor_edits
        code = "def foo():\n    return 1"
        edits = [{"action": "edit", "line": 2, "hash": "wrong!", "content": "    return 42"}]
        result, errors = apply_anchor_edits(code, edits)
        assert len(errors) > 0
        assert "mismatch" in errors[0].lower() or "Hash" in errors[0]
        assert result == code  # No changes applied

    def test_apply_delete(self):
        from kaiwu.tools.hashline import apply_anchor_edits, _line_hash
        code = "line1\nline2\nline3"
        h = _line_hash("line2")
        edits = [{"action": "delete", "line": 2, "hash": h, "content": ""}]
        result, errors = apply_anchor_edits(code, edits)
        assert not errors
        assert result == "line1\nline3"

    def test_apply_insert_after(self):
        from kaiwu.tools.hashline import apply_anchor_edits, _line_hash
        code = "line1\nline3"
        h = _line_hash("line1")
        edits = [{"action": "insert_after", "line": 1, "hash": h, "content": "line2"}]
        result, errors = apply_anchor_edits(code, edits)
        assert not errors
        assert result == "line1\nline2\nline3"

    def test_multiple_edits(self):
        from kaiwu.tools.hashline import parse_anchor_edits
        raw = "EDIT 1|aaa111| → new_line1\nDELETE 3|bbb222|\nINSERT_AFTER 5|ccc333| → extra"
        edits = parse_anchor_edits(raw)
        assert len(edits) == 3

    def test_roundtrip_identity(self):
        """add_anchors → strip_anchors should be identity."""
        from kaiwu.tools.hashline import add_anchors, strip_anchors
        code = "import os\n\ndef main():\n    print('hello')\n\nif __name__ == '__main__':\n    main()"
        assert strip_anchors(add_anchors(code)) == code


class TestThinkConfig(unittest.TestCase):
    """P1: AdaptThink configuration."""

    def test_easy_task_no_think(self):
        from kaiwu.core.think_config import get_think_config
        cfg = get_think_config("codegen", "easy")
        assert not cfg["think"]
        assert cfg["budget"] == 0

    def test_hard_refactor_full_think(self):
        from kaiwu.core.think_config import get_think_config
        cfg = get_think_config("refactor", "hard")
        assert cfg["think"]
        assert cfg["budget"] == 4096

    def test_medium_locator_repair(self):
        from kaiwu.core.think_config import get_think_config
        cfg = get_think_config("locator_repair", "medium")
        assert cfg["think"]
        assert cfg["budget"] == 512

    def test_chat_always_no_think(self):
        from kaiwu.core.think_config import get_think_config
        for diff in ["easy", "medium", "hard"]:
            cfg = get_think_config("chat", diff)
            assert not cfg["think"], f"chat/{diff} should not think"

    def test_unknown_type_defaults_off(self):
        from kaiwu.core.think_config import get_think_config
        cfg = get_think_config("nonexistent", "hard")
        assert not cfg["think"]

    def test_apply_think_to_max_tokens_non_reasoning(self):
        from kaiwu.core.think_config import apply_think_to_max_tokens
        # Non-reasoning model: tokens unchanged regardless of config
        result = apply_think_to_max_tokens(2048, {"think": True, "budget": 4096}, False)
        assert result == 2048

    def test_apply_think_to_max_tokens_reasoning_with_budget(self):
        from kaiwu.core.think_config import apply_think_to_max_tokens
        result = apply_think_to_max_tokens(2048, {"think": True, "budget": 2048}, True)
        assert result == 4096

    def test_apply_think_to_max_tokens_reasoning_no_think(self):
        from kaiwu.core.think_config import apply_think_to_max_tokens
        result = apply_think_to_max_tokens(2048, {"think": False, "budget": 0}, True)
        assert result == 2048


class TestTaskContextThinkConfig(unittest.TestCase):
    """P2: TaskContext has think_config field."""

    def test_default_think_config(self):
        from kaiwu.core.context import TaskContext
        ctx = TaskContext()
        assert ctx.think_config == {"think": False, "budget": 0}

    def test_think_escalation_pattern(self):
        """Simulate Fast/Slow escalation."""
        from kaiwu.core.context import TaskContext
        ctx = TaskContext()
        # Phase 1: fast (default)
        assert not ctx.think_config["think"]
        # Phase 2: slow escalation after failure
        ctx.think_config = {"think": True, "budget": 2048}
        assert ctx.think_config["think"]
        assert ctx.think_config["budget"] == 2048
        # Phase 3: max budget
        ctx.think_config = {"think": True, "budget": 4096}
        assert ctx.think_config["budget"] == 4096


if __name__ == "__main__":
    unittest.main()
