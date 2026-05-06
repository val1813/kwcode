"""
Unit tests for Kaiwu v3 core modules.
Tests Gate, Orchestrator, ToolExecutor, Memory without requiring a real LLM.
"""

import json
import os
import tempfile
import pytest

# ── Mock LLM Backend ──────────────────────────────────────────

class MockLLM:
    """Mock LLM that returns predefined responses based on prompt content."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.call_log = []

    def generate(self, prompt="", system="", max_tokens=1024,
                 temperature=0.0, stop=None, grammar_str=None):
        self.call_log.append({"prompt": prompt[:200], "temperature": temperature})

        # Check for matching response
        for key, value in self.responses.items():
            if key in prompt:
                return value

        # Default: return a valid Gate JSON
        return '{"expert_type": "codegen", "task_summary": "test", "difficulty": "easy"}'

    def chat(self, messages, **kwargs):
        prompt = " ".join(m.get("content", "") for m in messages)
        return self.generate(prompt=prompt, **kwargs)


# ── Test Gate ─────────────────────────────────────────────────

class TestGate:
    def test_classify_valid_json(self):
        from kaiwu.core.gate import Gate
        llm = MockLLM({
            "修复": '{"expert_type": "locator_repair", "task_summary": "修复bug", "difficulty": "easy"}',
        })
        gate = Gate(llm=llm)
        result = gate.classify("修复登录bug")
        assert result["expert_type"] == "locator_repair"
        assert result["difficulty"] == "easy"
        assert "_parse_error" not in result

    def test_classify_invalid_json_fallback(self):
        from kaiwu.core.gate import Gate
        llm = MockLLM({
            "测试": "这不是JSON，我来解释一下...",
        })
        gate = Gate(llm=llm)
        result = gate.classify("测试一下")
        # Should fallback to chat/easy (Gate parse failure → chat降级)
        assert result["expert_type"] == "chat"
        assert result["difficulty"] == "easy"
        assert "_parse_error" in result

    def test_classify_invalid_expert_type_fallback(self):
        from kaiwu.core.gate import Gate
        llm = MockLLM({
            "写诗": '{"expert_type": "poetry", "task_summary": "写诗", "difficulty": "easy"}',
        })
        gate = Gate(llm=llm)
        result = gate.classify("帮我写诗")
        assert result["expert_type"] == "chat"  # fallback
        assert "_parse_error" in result

    def test_classify_json_wrapped_in_text(self):
        from kaiwu.core.gate import Gate
        llm = MockLLM({
            "补全": '好的，分析结果如下：\n{"expert_type": "codegen", "task_summary": "补全函数", "difficulty": "easy"}\n以上是分类结果。',
        })
        gate = Gate(llm=llm)
        result = gate.classify("帮我补全这个函数")
        assert result["expert_type"] == "codegen"
        assert "_parse_error" not in result


# ── Test ToolExecutor ─────────────────────────────────────────

class TestToolExecutor:
    def test_read_write_file(self):
        from kaiwu.tools.executor import ToolExecutor
        with tempfile.TemporaryDirectory() as tmpdir:
            tools = ToolExecutor(project_root=tmpdir)
            # Write
            assert tools.write_file("test.txt", "hello kaiwu")
            # Read
            content = tools.read_file("test.txt")
            assert content == "hello kaiwu"

    def test_read_nonexistent(self):
        from kaiwu.tools.executor import ToolExecutor
        with tempfile.TemporaryDirectory() as tmpdir:
            tools = ToolExecutor(project_root=tmpdir)
            result = tools.read_file("nonexistent.txt")
            assert "[ERROR]" in result

    def test_list_dir(self):
        from kaiwu.tools.executor import ToolExecutor
        with tempfile.TemporaryDirectory() as tmpdir:
            tools = ToolExecutor(project_root=tmpdir)
            tools.write_file("a.py", "pass")
            tools.write_file("b.py", "pass")
            entries = tools.list_dir(".")
            assert "a.py" in entries
            assert "b.py" in entries

    def test_run_bash(self):
        from kaiwu.tools.executor import ToolExecutor
        with tempfile.TemporaryDirectory() as tmpdir:
            tools = ToolExecutor(project_root=tmpdir)
            stdout, stderr, rc = tools.run_bash("echo hello")
            assert "hello" in stdout
            assert rc == 0

    def test_run_bash_timeout(self):
        from kaiwu.tools.executor import ToolExecutor
        with tempfile.TemporaryDirectory() as tmpdir:
            tools = ToolExecutor(project_root=tmpdir)
            _, stderr, rc = tools.run_bash("sleep 10", timeout=1)
            assert rc == -1
            assert "timed out" in stderr.lower() or "timeout" in stderr.lower()

    def test_apply_patch(self):
        from kaiwu.tools.executor import ToolExecutor
        with tempfile.TemporaryDirectory() as tmpdir:
            tools = ToolExecutor(project_root=tmpdir)
            tools.write_file("code.py", "def foo():\n    return 1\n")
            assert tools.apply_patch("code.py", "return 1", "return 2")
            content = tools.read_file("code.py")
            assert "return 2" in content
            assert "return 1" not in content

    def test_get_file_tree(self):
        from kaiwu.tools.executor import ToolExecutor
        with tempfile.TemporaryDirectory() as tmpdir:
            tools = ToolExecutor(project_root=tmpdir)
            os.makedirs(os.path.join(tmpdir, "src"))
            tools.write_file("src/main.py", "pass")
            tree = tools.get_file_tree(".")
            assert "src" in tree
            assert "main.py" in tree


# ── Test KaiwuMemory ──────────────────────────────────────────

class TestKaiwuMemory:
    def test_init_creates_file(self):
        from kaiwu.memory.kaiwu_md import KaiwuMemory
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = KaiwuMemory()
            result = mem.init(tmpdir)
            assert "Created" in result
            assert os.path.exists(os.path.join(tmpdir, ".kaiwu", "PROJECT.md"))

    def test_init_no_overwrite(self):
        from kaiwu.memory.kaiwu_md import KaiwuMemory
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = KaiwuMemory()
            mem.init(tmpdir)
            result = mem.init(tmpdir)
            assert "already exists" in result

    def test_load_empty(self):
        from kaiwu.memory.kaiwu_md import KaiwuMemory
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = KaiwuMemory()
            result = mem.load(tmpdir)
            assert result == ""  # No KAIWU.md yet

    def test_load_after_init(self):
        from kaiwu.memory.kaiwu_md import KaiwuMemory
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = KaiwuMemory()
            mem.init(tmpdir)
            result = mem.load(tmpdir)
            assert "项目信息" in result

    def test_save_on_success(self):
        from kaiwu.memory.kaiwu_md import KaiwuMemory
        from kaiwu.core.context import TaskContext
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = KaiwuMemory()
            mem.init(tmpdir)

            ctx = TaskContext(
                user_input="修复bug",
                project_root=tmpdir,
                gate_result={"expert_type": "locator_repair"},
                locator_output={"relevant_files": ["src/main.py"], "relevant_functions": ["foo"]},
                verifier_output={"passed": True, "syntax_ok": True, "tests_passed": 1, "tests_total": 1},
            )
            mem.save(tmpdir, ctx)

            content = mem.show(tmpdir)
            assert "locator_repair" in content
            assert "src/main.py" in content

    def test_no_save_on_failure(self):
        from kaiwu.memory.kaiwu_md import KaiwuMemory
        from kaiwu.core.context import TaskContext
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = KaiwuMemory()
            mem.init(tmpdir)

            ctx = TaskContext(
                user_input="修复bug",
                project_root=tmpdir,
                gate_result={"expert_type": "locator_repair"},
                verifier_output={"passed": False},
            )
            mem.save(tmpdir, ctx)

            # PROJECT.md and EXPERT.md should NOT contain the failed task record
            from kaiwu.memory import project_md, expert_md
            proj_content = project_md.show(tmpdir)
            expert_content = expert_md.show(tmpdir)
            assert "locator_repair" not in proj_content
            assert "locator_repair" not in expert_content


# ── Test Orchestrator ─────────────────────────────────────────

class TestOrchestrator:
    def _make_orchestrator(self, locator_result=None, generator_result=None, verifier_result=None):
        """Create orchestrator with mock experts."""
        from kaiwu.core.orchestrator import PipelineOrchestrator
        from kaiwu.memory.kaiwu_md import KaiwuMemory

        class MockLocator:
            def run(self, ctx):
                if locator_result:
                    ctx.locator_output = locator_result
                    ctx.relevant_code_snippets = {"test.py": "def foo(): pass"}
                return locator_result

        class MockGenerator:
            def run(self, ctx):
                if generator_result:
                    ctx.generator_output = generator_result
                return generator_result

        class MockVerifier:
            def run(self, ctx):
                if verifier_result:
                    ctx.verifier_output = verifier_result
                return verifier_result

        class MockSearch:
            def search(self, ctx):
                return "mock search results"

        class MockOffice:
            def run(self, ctx):
                return {"passed": False, "error": "not implemented"}

        from kaiwu.tools.executor import ToolExecutor
        with tempfile.TemporaryDirectory() as tmpdir:
            tools = ToolExecutor(project_root=tmpdir)
            memory = KaiwuMemory()
            memory.init(tmpdir)

            orch = PipelineOrchestrator(
                locator=MockLocator(),
                generator=MockGenerator(),
                verifier=MockVerifier(),
                search_augmentor=MockSearch(),
                office_handler=MockOffice(),
                tool_executor=tools,
                memory=memory,
            )
            return orch, tmpdir

    def test_codegen_success(self):
        gen = {"patches": [{"file": "test.py", "original": "old", "modified": "new"}], "explanation": "fixed"}
        ver = {"passed": True, "syntax_ok": True, "tests_passed": 1, "tests_total": 1, "error_detail": ""}

        orch, tmpdir = self._make_orchestrator(generator_result=gen, verifier_result=ver)
        result = orch.run(
            user_input="写一个函数",
            gate_result={"expert_type": "codegen", "difficulty": "easy"},
            project_root=tmpdir,
        )
        assert result["success"] is True

    def test_locator_repair_success(self):
        loc = {"relevant_files": ["test.py"], "relevant_functions": ["foo"], "edit_locations": []}
        gen = {"patches": [{"file": "test.py", "original": "old", "modified": "new"}], "explanation": "fixed"}
        ver = {"passed": True, "syntax_ok": True, "tests_passed": 1, "tests_total": 1, "error_detail": ""}

        orch, tmpdir = self._make_orchestrator(locator_result=loc, generator_result=gen, verifier_result=ver)
        result = orch.run(
            user_input="修复bug",
            gate_result={"expert_type": "locator_repair", "difficulty": "easy"},
            project_root=tmpdir,
        )
        assert result["success"] is True

    def test_max_retries_exceeded(self):
        gen = {"patches": [{"file": "test.py", "original": "old", "modified": "new"}], "explanation": "fixed"}
        ver = {"passed": False, "syntax_ok": False, "tests_passed": 0, "tests_total": 1, "error_detail": "syntax error"}

        orch, tmpdir = self._make_orchestrator(generator_result=gen, verifier_result=ver)
        result = orch.run(
            user_input="写一个函数",
            gate_result={"expert_type": "codegen", "difficulty": "easy"},
            project_root=tmpdir,
        )
        assert result["success"] is False
        assert "Max retries" in result["error"]

    def test_search_triggered_on_hard_task(self):
        gen = {"patches": [{"file": "test.py", "original": "old", "modified": "new"}], "explanation": "fixed"}
        # First two calls fail, third succeeds
        call_count = {"n": 0}
        original_ver = {"passed": False, "syntax_ok": True, "tests_passed": 0, "tests_total": 1, "error_detail": "test failed", "error_type": "assertion"}

        class DynamicVerifier:
            def run(self, ctx):
                call_count["n"] += 1
                if call_count["n"] >= 3:
                    result = {"passed": True, "syntax_ok": True, "tests_passed": 1, "tests_total": 1, "error_detail": ""}
                else:
                    result = original_ver.copy()
                ctx.verifier_output = result
                return result

        from kaiwu.core.orchestrator import PipelineOrchestrator
        from kaiwu.memory.kaiwu_md import KaiwuMemory
        from kaiwu.tools.executor import ToolExecutor

        class MockGen:
            def run(self, ctx):
                ctx.generator_output = gen
                return gen

        class MockSearch:
            triggered = False
            def search(self, ctx):
                self.triggered = True
                return "search results"

        with tempfile.TemporaryDirectory() as tmpdir:
            tools = ToolExecutor(project_root=tmpdir)
            memory = KaiwuMemory()
            memory.init(tmpdir)
            search = MockSearch()

            orch = PipelineOrchestrator(
                locator=None,
                generator=MockGen(),
                verifier=DynamicVerifier(),
                search_augmentor=search,
                office_handler=None,
                tool_executor=tools,
                memory=memory,
            )
            result = orch.run(
                user_input="复杂任务",
                gate_result={"expert_type": "codegen", "difficulty": "hard"},
                project_root=tmpdir,
            )
            # Hard task: search triggered after first failure
            assert search.triggered is True
            assert result["success"] is True


# ── Test Expert Sequences ─────────────────────────────────────

class TestExpertSequences:
    def test_sequence_mapping(self):
        from kaiwu.core.orchestrator import EXPERT_SEQUENCES
        assert EXPERT_SEQUENCES["locator_repair"] == ["locator", "generator", "verifier"]
        assert EXPERT_SEQUENCES["codegen"] == ["generator", "verifier"]
        assert EXPERT_SEQUENCES["refactor"] == ["locator", "generator", "verifier"]
        assert EXPERT_SEQUENCES["doc"] == ["locator", "generator"]
        assert EXPERT_SEQUENCES["office"] == ["office"]


# ── Test Context ──────────────────────────────────────────────

class TestContext:
    def test_default_values(self):
        from kaiwu.core.context import TaskContext
        ctx = TaskContext()
        assert ctx.retry_count == 0
        assert ctx.search_triggered is False
        assert ctx.locator_output is None
        assert ctx.generator_output is None
        assert ctx.verifier_output is None

    def test_independent_contexts(self):
        """RED-3: Each expert must have independent context."""
        from kaiwu.core.context import TaskContext
        ctx1 = TaskContext(user_input="task1")
        ctx2 = TaskContext(user_input="task2")
        ctx1.locator_output = {"files": ["a.py"]}
        assert ctx2.locator_output is None  # Independent


# ── Test Generator filename extraction ───────────────────────

class TestExtractFilename:
    def _extract(self, user_input):
        from kaiwu.experts.generator import GeneratorExpert
        return GeneratorExpert._extract_filename(user_input)

    def test_explicit_filename(self):
        assert self._extract("帮我写个 login.py") == "login.py"
        assert self._extract("create server.js for me") == "server.js"
        assert self._extract("生成 config.yaml") == "config.yaml"

    def test_explicit_filename_with_path(self):
        # Should extract just the filename part from the regex
        result = self._extract("写个 utils.py 工具函数")
        assert result == "utils.py"

    def test_chinese_codegen_pattern(self):
        # English name after Chinese verb → extracted
        assert self._extract("写个sort函数") == "sort.py"
        assert self._extract("写一个calculator类") == "calculator.py"

    def test_english_create_pattern(self):
        assert self._extract("create a calculator") == "calculator.py"
        assert self._extract("write a parser") == "parser.py"
        assert self._extract("generate a scheduler") == "scheduler.py"

    def test_skip_generic_words(self):
        # "create a new function" → "new" and "function" are generic, fall through
        result = self._extract("create a new function")
        # Should not be "new.py" or "function.py"
        assert result == "output.py"

    def test_fallback_to_output(self):
        assert self._extract("帮我写段代码") == "output.py"
        assert self._extract("随便写点什么") == "output.py"

    def test_multiple_extensions(self):
        assert self._extract("写 main.go") == "main.go"
        assert self._extract("create index.html") == "index.html"
        assert self._extract("生成 Makefile.sh") == "Makefile.sh"

    def test_language_detection_html(self):
        # "写个HTML页面" → should detect .html extension
        assert self._extract("帮我写个html页面").endswith(".html")
        assert self._extract("写一个网页").endswith(".html")

    def test_language_detection_js(self):
        assert self._extract("写个javascript函数").endswith(".js")

    def test_language_detection_shell(self):
        assert self._extract("写个shell脚本").endswith(".sh")
        assert self._extract("写个bash脚本").endswith(".sh")


class TestCleanCodeOutput:
    def test_strip_tool_call_lines(self):
        from kaiwu.experts.generator import GeneratorExpert
        raw = "write_file output.html\n<html>\n<body>hello</body>\n</html>"
        result = GeneratorExpert._clean_code_output(raw)
        assert "write_file" not in result
        assert "<html>" in result

    def test_strip_markdown_blocks(self):
        from kaiwu.experts.generator import GeneratorExpert
        raw = "```html\n<h1>hello</h1>\n```"
        result = GeneratorExpert._clean_code_output(raw)
        assert "```" not in result
        assert "<h1>hello</h1>" in result


class TestCodegenOutput:
    """Test that _run_codegen uses real filename instead of new_code.py."""

    def test_codegen_uses_extracted_filename(self):
        from kaiwu.experts.generator import GeneratorExpert
        from kaiwu.core.context import TaskContext

        llm = MockLLM({"生成": "def hello():\n    print('hello')"})
        gen = GeneratorExpert(llm=llm, num_candidates=1)

        ctx = TaskContext(
            user_input="写个 hello.py",
            project_root="/tmp/test_project",
            gate_result={"expert_type": "codegen"},
        )
        result = gen._run_codegen(ctx)
        assert result is not None
        assert result["patches"][0]["file"] == "hello.py"
        assert "hello.py" in result["explanation"]

    def test_codegen_fallback_filename(self):
        from kaiwu.experts.generator import GeneratorExpert
        from kaiwu.core.context import TaskContext

        llm = MockLLM({"生成": "x = 1"})
        gen = GeneratorExpert(llm=llm, num_candidates=1)

        ctx = TaskContext(
            user_input="帮我写段代码",
            project_root="/tmp/test_project",
            gate_result={"expert_type": "codegen"},
        )
        result = gen._run_codegen(ctx)
        assert result is not None
        assert result["patches"][0]["file"] == "output.py"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
