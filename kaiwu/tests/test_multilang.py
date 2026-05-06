"""
Tests for multi-language support modules:
- language_detector: project language detection
- ast_grep_engine: predefined template queries
- parser: multi-language tree-sitter (graceful fallback)
- verifier: multi-language test runner selection
"""

import json
import os
import tempfile
import shutil
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ═══════════════════════════════════════════════════════════════════
# Language Detector Tests
# ═══════════════════════════════════════════════════════════════════

class TestLanguageDetector:
    """Tests for kaiwu.ast_engine.language_detector."""

    def _make_project(self, tmp_path, files: dict):
        """Create a temp project with given files (content doesn't matter)."""
        for rel_path, content in files.items():
            fpath = os.path.join(str(tmp_path), rel_path.replace("/", os.sep))
            os.makedirs(os.path.dirname(fpath), exist_ok=True)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)
        return str(tmp_path)

    def test_detect_python_project(self, tmp_path):
        from kaiwu.ast_engine.language_detector import detect_project_languages
        project = self._make_project(tmp_path, {
            "src/main.py": "",
            "src/utils.py": "",
            "src/models.py": "",
            "tests/test_main.py": "",
        })
        result = detect_project_languages(project)
        assert result["python"] == 4
        assert "javascript" not in result

    def test_detect_mixed_project(self, tmp_path):
        from kaiwu.ast_engine.language_detector import detect_project_languages
        project = self._make_project(tmp_path, {
            "backend/main.py": "",
            "backend/api.py": "",
            "frontend/app.ts": "",
            "frontend/utils.ts": "",
            "frontend/index.tsx": "",
        })
        result = detect_project_languages(project)
        assert result["typescript"] == 3
        assert result["python"] == 2

    def test_detect_go_project(self, tmp_path):
        from kaiwu.ast_engine.language_detector import detect_project_languages
        project = self._make_project(tmp_path, {
            "main.go": "",
            "handler.go": "",
            "handler_test.go": "",
            "go.mod": "",
        })
        result = detect_project_languages(project)
        assert result["go"] == 3  # .go files only, go.mod not counted

    def test_get_primary_language(self, tmp_path):
        from kaiwu.ast_engine.language_detector import get_primary_language
        project = self._make_project(tmp_path, {
            "a.py": "", "b.py": "", "c.py": "",
            "d.js": "",
        })
        assert get_primary_language(project) == "python"

    def test_get_primary_language_empty(self, tmp_path):
        from kaiwu.ast_engine.language_detector import get_primary_language
        assert get_primary_language(str(tmp_path)) == "python"  # default

    def test_detect_language_for_file(self):
        from kaiwu.ast_engine.language_detector import detect_language_for_file
        assert detect_language_for_file("main.py") == "python"
        assert detect_language_for_file("app.ts") == "typescript"
        assert detect_language_for_file("main.go") == "go"
        assert detect_language_for_file("lib.rs") == "rust"
        assert detect_language_for_file("App.java") == "java"
        assert detect_language_for_file("readme.md") is None

    def test_detect_project_marker(self, tmp_path):
        from kaiwu.ast_engine.language_detector import detect_project_marker
        # Go project
        project = self._make_project(tmp_path, {"go.mod": "", "main.go": ""})
        assert detect_project_marker(project) == "go"

    def test_detect_project_marker_rust(self, tmp_path):
        from kaiwu.ast_engine.language_detector import detect_project_marker
        project = self._make_project(tmp_path, {"Cargo.toml": "", "src/main.rs": ""})
        assert detect_project_marker(project) == "rust"

    def test_detect_project_marker_none(self, tmp_path):
        from kaiwu.ast_engine.language_detector import detect_project_marker
        project = self._make_project(tmp_path, {"readme.md": ""})
        assert detect_project_marker(project) is None

    def test_get_test_command(self):
        from kaiwu.ast_engine.language_detector import get_test_command
        assert "pytest" in get_test_command("python")
        assert "go test" in get_test_command("go")
        assert "cargo test" in get_test_command("rust")
        assert get_test_command("unknown") is None

    def test_skip_dirs(self, tmp_path):
        from kaiwu.ast_engine.language_detector import detect_project_languages
        project = self._make_project(tmp_path, {
            "src/main.py": "",
            "node_modules/pkg/index.js": "",
            ".venv/lib/site.py": "",
        })
        result = detect_project_languages(project)
        assert result.get("python", 0) == 1
        assert "javascript" not in result


# ═══════════════════════════════════════════════════════════════════
# AST-Grep Engine Tests
# ═══════════════════════════════════════════════════════════════════

class TestAstGrepEngine:
    """Tests for kaiwu.ast_engine.ast_grep_engine."""

    def test_fill_template_basic(self):
        from kaiwu.ast_engine.ast_grep_engine import _fill_template
        template = "def $NAME($$$ARGS):\n    $$$BODY"
        result = _fill_template(template, {"NAME": "hello"})
        assert "def hello(" in result
        assert "$$$ARGS" in result  # unfilled stays

    def test_fill_template_no_params(self):
        from kaiwu.ast_engine.ast_grep_engine import _fill_template
        template = "def $NAME($$$ARGS):\n    $$$BODY"
        assert _fill_template(template, None) == template

    def test_query_unknown_pattern(self):
        from kaiwu.ast_engine.ast_grep_engine import query
        result = query("nonexistent_pattern", "python", "def foo(): pass")
        assert result == []

    def test_query_unknown_language(self):
        from kaiwu.ast_engine.ast_grep_engine import query
        result = query("find_function", "cobol", "def foo(): pass")
        assert result == []

    def test_query_templates_exist(self):
        from kaiwu.ast_engine.ast_grep_engine import QUERY_TEMPLATES
        assert "find_function" in QUERY_TEMPLATES
        assert "find_class" in QUERY_TEMPLATES
        assert "find_imports" in QUERY_TEMPLATES
        # Each template has Python at minimum
        for key, templates in QUERY_TEMPLATES.items():
            assert "python" in templates, f"{key} missing python template"

    def test_query_templates_languages(self):
        from kaiwu.ast_engine.ast_grep_engine import QUERY_TEMPLATES
        # find_function should cover all major languages
        func_templates = QUERY_TEMPLATES["find_function"]
        for lang in ("python", "javascript", "typescript", "go", "rust", "java"):
            assert lang in func_templates, f"find_function missing {lang}"

    @patch("kaiwu.ast_engine.ast_grep_engine.AST_GREP_AVAILABLE", False)
    @patch("kaiwu.ast_engine.ast_grep_engine._query_cli")
    def test_query_falls_back_to_cli(self, mock_cli):
        from kaiwu.ast_engine.ast_grep_engine import query
        mock_cli.return_value = [{"text": "def foo():", "start_line": 1, "end_line": 1}]
        result = query("find_function", "python", "def foo(): pass", {"NAME": "foo"})
        assert mock_cli.called
        assert len(result) == 1

    @patch("kaiwu.ast_engine.ast_grep_engine.AST_GREP_AVAILABLE", False)
    @patch("subprocess.run")
    def test_query_cli_not_found(self, mock_run):
        from kaiwu.ast_engine.ast_grep_engine import _query_cli
        mock_run.side_effect = FileNotFoundError()
        result = _query_cli("def $NAME(): pass", "python", "def foo(): pass")
        assert result == []

    @patch("kaiwu.ast_engine.ast_grep_engine.AST_GREP_AVAILABLE", False)
    @patch("subprocess.run")
    def test_query_cli_timeout(self, mock_run):
        import subprocess
        from kaiwu.ast_engine.ast_grep_engine import _query_cli
        mock_run.side_effect = subprocess.TimeoutExpired("ast-grep", 10)
        result = _query_cli("def $NAME(): pass", "python", "def foo(): pass")
        assert result == []

    @patch("kaiwu.ast_engine.ast_grep_engine.AST_GREP_AVAILABLE", False)
    @patch("subprocess.run")
    def test_query_cli_success(self, mock_run):
        from kaiwu.ast_engine.ast_grep_engine import _query_cli
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([{
                "text": "def hello(x):",
                "range": {"start": {"line": 0, "column": 0}, "end": {"line": 0, "column": 13}},
            }])
        )
        result = _query_cli("def $NAME($$$ARGS):", "python", "def hello(x): pass")
        assert len(result) == 1
        assert result[0]["text"] == "def hello(x):"
        assert result[0]["start_line"] == 1

    def test_is_available(self):
        from kaiwu.ast_engine.ast_grep_engine import is_available
        # Should not crash regardless of whether ast-grep is installed
        result = is_available()
        assert isinstance(result, bool)

    def test_find_functions_nonexistent_file(self):
        from kaiwu.ast_engine.ast_grep_engine import find_functions
        result = find_functions("/nonexistent/path.py", "python")
        assert result == []


# ═══════════════════════════════════════════════════════════════════
# Parser Multi-Language Tests
# ═══════════════════════════════════════════════════════════════════

class TestParserMultiLang:
    """Tests for multi-language TreeSitterParser."""

    def test_python_always_supported(self):
        from kaiwu.ast_engine.parser import TreeSitterParser
        parser = TreeSitterParser()
        assert "python" in parser.supported_languages()

    def test_parse_python_file(self, tmp_path):
        from kaiwu.ast_engine.parser import TreeSitterParser
        parser = TreeSitterParser()
        fpath = os.path.join(str(tmp_path), "test.py")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write("def hello(name):\n    return f'Hello {name}'\n")
        tree = parser.parse_file(fpath)
        assert tree is not None

    def test_parse_unsupported_extension(self, tmp_path):
        from kaiwu.ast_engine.parser import TreeSitterParser
        parser = TreeSitterParser()
        fpath = os.path.join(str(tmp_path), "test.xyz")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write("some content")
        tree = parser.parse_file(fpath)
        assert tree is None

    def test_extract_functions_python(self):
        from kaiwu.ast_engine.parser import TreeSitterParser
        parser = TreeSitterParser()
        code = b"def foo(x, y):\n    return x + y\n\ndef bar():\n    pass\n"
        tree = parser.parse_bytes(code, "python")
        assert tree is not None
        funcs = parser.extract_functions(tree, code, "python")
        names = [f["name"] for f in funcs]
        assert "foo" in names
        assert "bar" in names

    def test_extract_functions_unsupported_lang(self):
        from kaiwu.ast_engine.parser import TreeSitterParser
        parser = TreeSitterParser()
        code = b"fn main() {}"
        tree = parser.parse_bytes(code, "cobol")
        # parse_bytes returns None for unsupported
        assert tree is None

    def test_detect_file_language(self):
        from kaiwu.ast_engine.parser import TreeSitterParser
        parser = TreeSitterParser()
        # Python always works
        assert parser.detect_file_language("main.py") == "python"
        # Others depend on installed bindings
        # Should not crash
        result = parser.detect_file_language("main.go")
        assert result is None or result == "go"

    def test_ext_map_coverage(self):
        from kaiwu.ast_engine.parser import TreeSitterParser
        parser = TreeSitterParser()
        # EXT_MAP should have entries for all major extensions
        assert ".py" in parser.EXT_MAP
        assert ".js" in parser.EXT_MAP
        assert ".ts" in parser.EXT_MAP
        assert ".go" in parser.EXT_MAP
        assert ".rs" in parser.EXT_MAP
        assert ".java" in parser.EXT_MAP

    def test_parse_bytes_python(self):
        from kaiwu.ast_engine.parser import TreeSitterParser
        parser = TreeSitterParser()
        tree = parser.parse_bytes(b"x = 1", "python")
        assert tree is not None

    def test_extract_calls_python(self):
        from kaiwu.ast_engine.parser import TreeSitterParser
        parser = TreeSitterParser()
        code = b"def main():\n    foo()\n    bar.baz()\n"
        tree = parser.parse_bytes(code, "python")
        calls = parser.extract_calls(tree, code, "python")
        names = [c["name"] for c in calls]
        assert "foo" in names
        assert "baz" in names


# ═══════════════════════════════════════════════════════════════════
# Verifier Multi-Language Tests
# ═══════════════════════════════════════════════════════════════════

class TestVerifierMultiLang:
    """Tests for multi-language verifier."""

    def test_parse_go_test_output(self):
        from kaiwu.experts.verifier import VerifierExpert
        output = """--- PASS: TestAdd (0.00s)
--- PASS: TestSub (0.00s)
--- FAIL: TestMul (0.01s)
FAIL
"""
        passed, total = VerifierExpert._parse_go_test_output(output)
        assert passed == 2
        assert total == 3

    def test_parse_go_test_output_package_level(self):
        from kaiwu.experts.verifier import VerifierExpert
        output = """ok  	mypackage	0.5s
ok  	mypackage/sub	0.3s
FAIL	mypackage/broken	0.1s
"""
        passed, total = VerifierExpert._parse_go_test_output(output)
        assert passed == 2
        assert total == 3

    def test_parse_rust_test_output(self):
        from kaiwu.experts.verifier import VerifierExpert
        output = """running 5 tests
test tests::test_add ... ok
test tests::test_sub ... ok
test tests::test_mul ... FAILED
test tests::test_div ... ok
test tests::test_mod ... ok

test result: FAILED. 4 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out
"""
        passed, total = VerifierExpert._parse_rust_test_output(output)
        assert passed == 4
        assert total == 5

    def test_parse_jest_test_output(self):
        from kaiwu.experts.verifier import VerifierExpert
        output = """Tests:  1 failed, 4 passed, 5 total
Time:   2.5s
"""
        passed, total = VerifierExpert._parse_jest_test_output(output)
        assert passed == 4
        assert total == 5

    def test_parse_jest_test_output_all_pass(self):
        from kaiwu.experts.verifier import VerifierExpert
        output = """Tests:  10 passed, 10 total
Time:   1.2s
"""
        passed, total = VerifierExpert._parse_jest_test_output(output)
        assert passed == 10
        assert total == 10

    def test_parse_java_maven_output(self):
        from kaiwu.experts.verifier import VerifierExpert
        output = """Tests run: 15, Failures: 2, Errors: 1, Skipped: 0
"""
        passed, total = VerifierExpert._parse_java_test_output(output)
        assert passed == 12
        assert total == 15

    def test_parse_java_gradle_output(self):
        from kaiwu.experts.verifier import VerifierExpert
        output = """8 tests completed, 1 failed
"""
        passed, total = VerifierExpert._parse_java_test_output(output)
        assert passed == 7
        assert total == 8

    def test_parse_python_test_output_pytest(self):
        from kaiwu.experts.verifier import VerifierExpert
        output = "5 passed, 2 failed in 1.5s"
        passed, total = VerifierExpert._parse_python_test_output(output)
        assert passed == 5
        assert total == 7

    def test_parse_python_test_output_unittest(self):
        from kaiwu.experts.verifier import VerifierExpert
        output = "Ran 10 tests in 0.5s\n\nOK"
        passed, total = VerifierExpert._parse_python_test_output(output)
        assert passed == 10
        assert total == 10

    def test_classify_error_go(self):
        from kaiwu.experts.verifier import VerifierExpert
        v = VerifierExpert(llm=MagicMock(), tool_executor=MagicMock())
        info = v._classify_error("main.go:15:5: undefined: someFunc")
        assert info["error_type"] == "syntax"
        assert info["error_file"] == "main.go"
        assert info["error_line"] == 15

    def test_classify_error_rust(self):
        from kaiwu.experts.verifier import VerifierExpert
        v = VerifierExpert(llm=MagicMock(), tool_executor=MagicMock())
        info = v._classify_error("error[E0425]: cannot find value `x` in this scope")
        assert info["error_type"] == "syntax"

    def test_classify_error_go_test_fail(self):
        from kaiwu.experts.verifier import VerifierExpert
        v = VerifierExpert(llm=MagicMock(), tool_executor=MagicMock())
        info = v._classify_error("--- FAIL: TestAdd (0.00s)\n    add_test.go:10: expected 5, got 3")
        assert "TestAdd" in info["failed_tests"]

    def test_classify_error_rust_test_fail(self):
        from kaiwu.experts.verifier import VerifierExpert
        v = VerifierExpert(llm=MagicMock(), tool_executor=MagicMock())
        info = v._classify_error("test tests::test_add ... FAILED")
        assert "tests::test_add" in info["failed_tests"]

    def test_detect_project_language_go(self, tmp_path):
        from kaiwu.experts.verifier import _detect_project_language
        # Create go.mod
        go_mod = os.path.join(str(tmp_path), "go.mod")
        with open(go_mod, "w") as f:
            f.write("module example.com/myapp")

        mock_tools = MagicMock()
        mock_tools.list_dir.return_value = ["go.mod", "main.go", "handler.go"]
        assert _detect_project_language(str(tmp_path), mock_tools) == "go"

    def test_detect_project_language_rust(self):
        from kaiwu.experts.verifier import _detect_project_language
        mock_tools = MagicMock()
        mock_tools.list_dir.return_value = ["Cargo.toml", "src", "tests"]
        assert _detect_project_language("/fake", mock_tools) == "rust"

    def test_detect_project_language_default_python(self):
        from kaiwu.experts.verifier import _detect_project_language
        mock_tools = MagicMock()
        mock_tools.list_dir.return_value = ["src", "tests", "README.md"]
        assert _detect_project_language("/fake", mock_tools) == "python"

    def test_test_runners_all_languages(self):
        from kaiwu.experts.verifier import TEST_RUNNERS
        for lang in ("python", "javascript", "typescript", "go", "rust", "java"):
            assert lang in TEST_RUNNERS, f"Missing test runner for {lang}"
            assert len(TEST_RUNNERS[lang]) > 0


# ═══════════════════════════════════════════════════════════════════
# Graph Builder Extension Tests
# ═══════════════════════════════════════════════════════════════════

class TestGraphBuilderMultiLang:
    """Tests for graph_builder multi-language extensions."""

    def test_supported_extensions_includes_python(self):
        from kaiwu.ast_engine.graph_builder import SUPPORTED_EXTENSIONS
        assert ".py" in SUPPORTED_EXTENSIONS

    def test_export_rig_has_language_stats(self, tmp_path):
        from kaiwu.ast_engine.graph_builder import GraphBuilder
        project = str(tmp_path)
        # Create a Python file
        src_dir = os.path.join(project, "src")
        os.makedirs(src_dir)
        with open(os.path.join(src_dir, "main.py"), "w", encoding="utf-8") as f:
            f.write("def hello(): pass\n")

        gb = GraphBuilder(project)
        rig = gb.export_rig()
        assert "language_stats" in rig
        assert isinstance(rig["language_stats"], dict)
