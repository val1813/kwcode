"""
Verifier expert: validates Generator patches via syntax check + test execution.
RED-2: Deterministic verification sequence (syntax → apply → test).
RED-3: Independent context window, does not inherit Generator history.
"""

import json
import logging
import os
import re
from typing import Optional

from kaiwu.core.context import TaskContext
from kaiwu.llm.llama_backend import LLMBackend
from kaiwu.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)

# 按优先级排序，首个匹配生效
TEST_RUNNERS = {
    "python":     [
        ("pytest", "python -m pytest tests/ --tb=short -q"),
        ("unittest", "python -m unittest discover -s tests -q"),
    ],
    "javascript": [("jest", "npx jest --ci --passWithNoTests")],
    "typescript": [("jest", "npx jest --ci --passWithNoTests")],
    "go":         [("go_test", "go test ./...")],
    "rust":       [("cargo_test", "cargo test 2>&1")],
    "java":       [
        ("maven", "mvn test -q"),
        ("gradle", "gradle test"),
    ],
    "csharp":     [("dotnet", "dotnet test --no-build -q")],
}

# 项目标记文件 → 语言检测
_PROJECT_MARKERS = {
    "go.mod":              "go",
    "Cargo.toml":          "rust",
    "pom.xml":             "java",
    "build.gradle":        "java",
    "build.gradle.kts":    "java",
    "package.json":        "javascript",
    "tsconfig.json":       "typescript",
}

# 按扩展名的语法检查命令
_SYNTAX_CHECKS = {
    ".py":   'python -m py_compile "{file}"',
    ".go":   'go vet "{file}"',
    ".rs":   "cargo check 2>&1",
    ".java": 'javac -d /tmp "{file}"',
}


def _detect_project_language(project_root: str, tool_executor: ToolExecutor) -> str:
    """Detect project language from marker files. Defaults to 'python'."""
    try:
        entries = tool_executor.list_dir(project_root)
        if isinstance(entries, list):
            for entry in entries:
                if entry in _PROJECT_MARKERS:
                    return _PROJECT_MARKERS[entry]
            # 检查tsconfig（覆盖package.json → typescript）
            if "tsconfig.json" in entries:
                return "typescript"
    except Exception:
        pass
    return "python"


_TOOLCHAIN_CMDS = {
    "go": ("go version", "apt-get install -y golang-go"),
    "typescript": ("npx --version", "apt-get install -y nodejs npm"),
    "javascript": ("node --version", "apt-get install -y nodejs"),
    "rust": ("cargo --version", "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y"),
    "java": ("javac -version", "apt-get install -y default-jdk"),
}


class VerifierExpert:
    """Deterministic verification: syntax check → apply patch → run tests."""

    def __init__(self, llm: LLMBackend, tool_executor: ToolExecutor):
        self.llm = llm
        self.tools = tool_executor

    def run_tests_only(self, ctx: TaskContext) -> dict:
        """
        只运行测试，不做patch/syntax。用于pre-test定位。
        返回 {"passed": int, "total": int, "output": str, "error_type": str}
        """
        project_lang = _detect_project_language(ctx.project_root, self.tools)

        # 先检测工具链
        toolchain_err = self._check_toolchain(project_lang, ctx.project_root)
        if toolchain_err:
            return {"passed": 0, "total": 0, "output": toolchain_err,
                    "error_type": "missing_toolchain"}

        passed, total, error = self._run_tests(ctx)
        return {"passed": passed, "total": total, "output": error,
                "error_type": self._classify_error(error)["error_type"] if error else ""}

    def _check_toolchain(self, lang: str, project_root: str) -> str:
        """检测工具链是否存在。返回错误信息或空字符串。"""
        check = _TOOLCHAIN_CMDS.get(lang)
        if not check:
            return ""
        cmd, install_cmd = check
        try:
            _, stderr, rc = self.tools.run_bash(cmd, cwd=project_root)
            if rc != 0:
                # 尝试自动安装
                logger.info("[verifier] %s not found, installing: %s", lang, install_cmd)
                _, install_err, install_rc = self.tools.run_bash(install_cmd, cwd=project_root, timeout=120)
                if install_rc != 0:
                    return f"Toolchain missing: {lang}. Install failed: {install_err[:200]}"
                # 验证安装成功
                _, _, rc2 = self.tools.run_bash(cmd, cwd=project_root)
                if rc2 != 0:
                    return f"Toolchain missing: {lang}. Install succeeded but still not found."
                logger.info("[verifier] %s installed successfully", lang)
        except Exception as e:
            logger.debug("[verifier] toolchain check failed: %s", e)
        return ""

    def run(self, ctx: TaskContext) -> Optional[dict]:
        """
        Verify Generator output. Fixed sequence:
        1. Syntax check (python -m py_compile)
        2. Apply patches (write_file)
        3. Run existing tests (pytest, if available)
        4. Return structured result
        """
        gen_output = ctx.generator_output
        if not gen_output or not gen_output.get("patches"):
            result = {
                "passed": False,
                "syntax_ok": False,
                "tests_passed": 0,
                "tests_total": 0,
                "error_detail": "No patches to verify",
            }
            ctx.verifier_output = result
            return result

        patches = gen_output["patches"]

        # 步骤1：备份原始文件
        backups = {}
        for patch in patches:
            fpath = patch["file"]
            original_content = self.tools.read_file(fpath)
            if not original_content.startswith("[ERROR]"):
                backups[fpath] = original_content

        # 步骤2：应用patch（精确匹配，original从文件读取）
        apply_ok = True
        applied_files = []
        for patch in patches:
            fpath = patch["file"]
            original = patch.get("original", "")
            modified = patch.get("modified", "")

            # whole_file模式：直接写入整个文件
            if patch.get("write_mode") == "whole_file":
                try:
                    os.makedirs(os.path.dirname(fpath) if os.path.dirname(fpath) else ".", exist_ok=True)
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(modified)
                    success = True
                except Exception as e:
                    logger.error("whole_file write failed for %s: %s", fpath, e)
                    success = False
            elif original and modified:
                success = self.tools.apply_patch(fpath, original, modified)
            elif modified:
                success = self.tools.write_file(fpath, modified)
            else:
                success = False

            if success:
                applied_files.append(fpath)
            else:
                apply_ok = False
                logger.warning("Patch apply failed for %s", fpath)

        if not apply_ok and not applied_files:
            self._rollback(backups)
            result = {
                "passed": False,
                "syntax_ok": False,
                "tests_passed": 0,
                "tests_total": 0,
                "error_detail": "All patches failed to apply",
                "error_type": "patch_apply",
                "error_file": "",
                "error_line": 0,
                "error_message": "All patches failed to apply",
                "failed_tests": [],
            }
            ctx.verifier_output = result
            return result

        # 步骤3：对修改文件做语法检查（多语言）
        syntax_ok = True
        syntax_errors = []
        for fpath in applied_files:
            err = self._syntax_check_file(fpath, ctx.project_root)
            if err:
                syntax_ok = False
                syntax_errors.append(err)

        if not syntax_ok:
            self._rollback(backups)
            error_msg = f"Syntax errors: {'; '.join(syntax_errors)}"
            error_info = self._classify_error(error_msg)
            result = {
                "passed": False,
                "syntax_ok": False,
                "tests_passed": 0,
                "tests_total": 0,
                "error_detail": error_msg,
                "error_type": "syntax",
                "error_file": error_info["error_file"],
                "error_line": error_info["error_line"],
                "error_message": error_msg[:200],
                "failed_tests": [],
            }
            ctx.verifier_output = result
            return result

        # 步骤4：运行测试（如果有测试基础设施）
        tests_passed, tests_total, test_error = self._run_tests(ctx)

        # 判定通过/失败
        passed = syntax_ok
        if tests_total > 0:
            passed = passed and (tests_passed == tests_total)
        elif syntax_ok and not test_error:
            # tests_total=0且没有报错：检查是否有测试文件但没跑到
            project_lang = _detect_project_language(ctx.project_root, self.tools)
            test_files = self._find_test_files(ctx.project_root, project_lang)
            if test_files:
                # 有测试文件但跑了0个测试 → 不算通过
                passed = False
                test_error = f"发现{len(test_files)}个测试文件但测试未执行，请检查测试路径"

        if not passed:
            self._rollback(backups)
            # WRONG_FILE确定性检测
            if self._detect_wrong_file(ctx, test_error):
                error_info = {
                    "error_type": "wrong_file",
                    "error_file": "",
                    "error_line": 0,
                    "error_message": "修改的文件与报错文件不匹配",
                    "failed_tests": [],
                }
                result = {
                    "passed": False,
                    "syntax_ok": syntax_ok,
                    "tests_passed": tests_passed,
                    "tests_total": tests_total,
                    "error_detail": test_error,
                    "error_type": "wrong_file",
                    "error_file": "",
                    "error_line": 0,
                    "error_message": "修改的文件与报错文件不匹配",
                    "failed_tests": [],
                }
                ctx.verifier_output = result
                return result

        error_info = self._classify_error(test_error) if not passed else {
            "error_type": "", "error_file": "", "error_line": 0,
            "error_message": "", "failed_tests": []}
        result = {
            "passed": passed,
            "syntax_ok": syntax_ok,
            "tests_passed": tests_passed,
            "tests_total": tests_total,
            "error_detail": test_error if not passed else "",
            "error_type": error_info["error_type"],
            "error_file": error_info["error_file"],
            "error_line": error_info["error_line"],
            "error_message": error_info["error_message"],
            "failed_tests": error_info["failed_tests"],
        }
        ctx.verifier_output = result
        return result

    def _syntax_check_file(self, fpath: str, project_root: str) -> Optional[str]:
        """Run syntax check for a single file. Returns error string or None."""
        ext = os.path.splitext(fpath)[1].lower()

        if ext == ".py":
            _, stderr, rc = self.tools.run_bash(
                f'python -m py_compile "{fpath}"',
                cwd=project_root,
            )
            if rc != 0:
                return f"{fpath}: {stderr.strip()}"

        elif ext == ".go":
            _, stderr, rc = self.tools.run_bash(
                f'go vet "{fpath}"',
                cwd=project_root,
            )
            if rc != 0:
                # 区分工具链缺失 vs 真实语法错误
                if "not found" in stderr.lower() or "no such file" in stderr.lower():
                    return None  # 工具链问题，不报语法错误
                return f"{fpath}: {stderr.strip()}"

        elif ext in (".ts", ".tsx"):
            # Only check if tsconfig.json exists
            tsconfig = os.path.join(project_root, "tsconfig.json")
            if os.path.exists(tsconfig):
                _, stderr, rc = self.tools.run_bash(
                    f'npx tsc --noEmit "{fpath}"',
                    cwd=project_root,
                )
                if rc != 0:
                    return f"{fpath}: {stderr.strip()[:200]}"

        elif ext == ".rs":
            # Rust checks at project level
            _, stderr, rc = self.tools.run_bash(
                "cargo check 2>&1",
                cwd=project_root,
            )
            if rc != 0:
                return f"{fpath}: {stderr.strip()[:200]}"

        elif ext == ".java":
            _, stderr, rc = self.tools.run_bash(
                f'javac -d /tmp "{fpath}"',
                cwd=project_root,
            )
            if rc != 0:
                return f"{fpath}: {stderr.strip()[:200]}"

        return None

    def _classify_error(self, error_detail: str) -> dict:
        """Extract structured error info from pytest/compile output. Pure regex, no LLM."""
        info = {"error_type": "unknown", "error_file": "", "error_line": 0,
                "error_message": "", "failed_tests": []}

        if not error_detail:
            return info

        # error_type classification
        if "SyntaxError" in error_detail:
            info["error_type"] = "syntax"
        elif "AssertionError" in error_detail:
            info["error_type"] = "assertion"
        elif "ModuleNotFoundError" in error_detail or "ImportError" in error_detail:
            info["error_type"] = "import"
        elif "patch" in error_detail.lower() and "failed" in error_detail.lower():
            info["error_type"] = "patch_apply"
        elif any(exc in error_detail for exc in ("TypeError", "ValueError", "KeyError",
                 "AttributeError", "NameError", "IndexError", "RuntimeError")):
            info["error_type"] = "runtime"
        # Go errors
        elif "undefined:" in error_detail or "cannot use" in error_detail:
            info["error_type"] = "syntax"
        # Rust errors
        elif "error[E" in error_detail:
            info["error_type"] = "syntax"
        # Java errors
        elif "error:" in error_detail and ".java:" in error_detail:
            info["error_type"] = "syntax"

        # Extract file and line: File "xxx.py", line 42
        file_match = re.search(r'File "([^"]+)", line (\d+)', error_detail)
        if file_match:
            info["error_file"] = file_match.group(1)
            info["error_line"] = int(file_match.group(2))
        else:
            # Go/Rust/Java format: file.go:42:5: error
            alt_match = re.search(r'([^\s:]+\.\w+):(\d+):', error_detail)
            if alt_match:
                info["error_file"] = alt_match.group(1)
                info["error_line"] = int(alt_match.group(2))

        # Extract failed test names: "FAILED tests/test_xxx.py::test_func"
        info["failed_tests"] = re.findall(r'FAILED\s+(\S+::\S+)', error_detail)
        # Go test failures: "--- FAIL: TestName"
        info["failed_tests"].extend(re.findall(r'--- FAIL:\s+(\S+)', error_detail))
        # Rust test failures: "test xxx ... FAILED"
        info["failed_tests"].extend(re.findall(r'test\s+(\S+)\s+\.\.\.\s+FAILED', error_detail))

        # Extract error message
        lines = [l.strip() for l in error_detail.splitlines() if l.strip()]
        if lines:
            for line in reversed(lines):
                exc_match = re.match(r'(\w+Error|\w+Exception):\s*(.+)', line)
                if exc_match:
                    info["error_message"] = exc_match.group(2)[:200]
                    break
            if not info["error_message"] and lines:
                info["error_message"] = lines[-1][:200]

        return info

    def _run_tests(self, ctx: TaskContext) -> tuple[int, int, str]:
        """Run project tests. Returns (passed, total, error_detail).
        Python default: python -m pytest tests/ --tb=short -q
        """
        # Detect project language
        project_lang = _detect_project_language(ctx.project_root, self.tools)

        # Get test runners for this language
        runners = TEST_RUNNERS.get(project_lang, TEST_RUNNERS["python"])

        # For Python: check if pytest is available
        if project_lang == "python":
            _, _, rc = self.tools.run_bash("python -m pytest --version", cwd=ctx.project_root)
            if rc != 0:
                runners = [("unittest", "python -m unittest discover -s tests -q")]

            # 扫描所有测试文件，不只看tests/目录
            test_files = self._find_test_files(ctx.project_root, "python")
            if not test_files:
                return 0, 0, ""  # 真的没有测试文件

            # 如果测试文件不在tests/目录，用文件路径直接跑
            test_dirs = self.tools.list_dir(ctx.project_root)
            has_tests_dir = any(d in ("tests", "test") for d in test_dirs if not d.startswith("[ERROR]"))
            if not has_tests_dir and test_files:
                # 测试文件在project_root（如 xxx_test.py），直接用文件路径
                test_paths = " ".join(f'"{f}"' for f in test_files[:10])
                runners = [("pytest_files", f"python -m pytest {test_paths} --tb=short -q")]

        elif project_lang == "go":
            # Go always has tests if go.mod exists
            pass

        elif project_lang == "rust":
            # Rust always has tests if Cargo.toml exists
            pass

        elif project_lang in ("javascript", "typescript"):
            # Check if package.json has test script
            pkg_json = os.path.join(ctx.project_root, "package.json")
            if os.path.exists(pkg_json):
                try:
                    with open(pkg_json, "r", encoding="utf-8") as f:
                        pkg = json.load(f)
                    if "test" in pkg.get("scripts", {}):
                        runners = [("npm_test", "npm test -- --ci")]
                except Exception:
                    pass
            else:
                return 0, 0, ""

        elif project_lang == "java":
            # Check for pom.xml or build.gradle
            has_maven = os.path.exists(os.path.join(ctx.project_root, "pom.xml"))
            has_gradle = os.path.exists(os.path.join(ctx.project_root, "build.gradle"))
            if not has_maven and not has_gradle:
                return 0, 0, ""
            if has_gradle:
                runners = [("gradle", "gradle test")]

        else:
            # Unknown language, check for Python tests as fallback
            test_dirs = self.tools.list_dir(ctx.project_root)
            has_tests = any(d in ("tests", "test") for d in test_dirs if not d.startswith("[ERROR]"))
            if not has_tests:
                return 0, 0, ""

        # Run test commands
        for name, cmd in runners:
            stdout, stderr, rc = self.tools.run_bash(cmd, cwd=ctx.project_root, timeout=120)
            output = stdout + "\n" + stderr
            if rc == 0:
                passed, total = self._parse_test_output(output, project_lang)
                return passed, total, ""
            else:
                passed, total = self._parse_test_output(output, project_lang)
                error = stderr.strip() or stdout.strip()
                return passed, total, error[:2000]

        return 0, 0, ""

    @staticmethod
    def _parse_test_output(output: str, language: str = "python") -> tuple[int, int]:
        """Parse test counts from test runner output."""

        if language == "python":
            return VerifierExpert._parse_python_test_output(output)
        elif language == "go":
            return VerifierExpert._parse_go_test_output(output)
        elif language == "rust":
            return VerifierExpert._parse_rust_test_output(output)
        elif language in ("javascript", "typescript"):
            return VerifierExpert._parse_jest_test_output(output)
        elif language == "java":
            return VerifierExpert._parse_java_test_output(output)

        # Fallback to Python parser
        return VerifierExpert._parse_python_test_output(output)

    @staticmethod
    def _parse_python_test_output(output: str) -> tuple[int, int]:
        """Parse pytest/unittest output."""
        # pytest format: "5 passed" or "3 passed, 2 failed"
        passed_match = re.search(r"(\d+) passed", output)
        failed_match = re.search(r"(\d+) failed", output)
        error_match = re.search(r"(\d+) error", output)

        passed = int(passed_match.group(1)) if passed_match else 0
        failed = int(failed_match.group(1)) if failed_match else 0
        errors = int(error_match.group(1)) if error_match else 0
        total = passed + failed + errors

        if total == 0:
            # unittest format: "Ran 5 tests"
            ran_match = re.search(r"Ran (\d+) test", output)
            if ran_match:
                total = int(ran_match.group(1))
                if "OK" in output:
                    passed = total
                else:
                    fail_match = re.search(r"failures=(\d+)", output)
                    err_match = re.search(r"errors=(\d+)", output)
                    f = int(fail_match.group(1)) if fail_match else 0
                    e = int(err_match.group(1)) if err_match else 0
                    passed = total - f - e

        return passed, total

    @staticmethod
    def _parse_go_test_output(output: str) -> tuple[int, int]:
        """Parse go test output."""
        # "ok" lines = passed packages, "FAIL" lines = failed packages
        # Individual: "--- PASS: TestName" / "--- FAIL: TestName"
        pass_count = len(re.findall(r'--- PASS:', output))
        fail_count = len(re.findall(r'--- FAIL:', output))
        total = pass_count + fail_count

        if total == 0:
            # Package-level: "ok  package 0.5s" / "FAIL package"
            ok_count = len(re.findall(r'^ok\s+', output, re.MULTILINE))
            fail_pkg = len(re.findall(r'^FAIL\s+', output, re.MULTILINE))
            if ok_count + fail_pkg > 0:
                return ok_count, ok_count + fail_pkg

        return pass_count, total

    @staticmethod
    def _parse_rust_test_output(output: str) -> tuple[int, int]:
        """Parse cargo test output."""
        # "test result: ok. 5 passed; 0 failed; 0 ignored"
        result_match = re.search(
            r'test result:.*?(\d+) passed.*?(\d+) failed', output
        )
        if result_match:
            passed = int(result_match.group(1))
            failed = int(result_match.group(2))
            return passed, passed + failed
        return 0, 0

    @staticmethod
    def _parse_jest_test_output(output: str) -> tuple[int, int]:
        """Parse Jest test output."""
        # "Tests: 2 failed, 5 passed, 7 total"
        tests_match = re.search(r'Tests:\s+(?:(\d+) failed,\s+)?(\d+) passed,\s+(\d+) total', output)
        if tests_match:
            passed = int(tests_match.group(2))
            total = int(tests_match.group(3))
            return passed, total

        # Alternative: "X passing" / "X failing"
        passing = re.search(r'(\d+) passing', output)
        failing = re.search(r'(\d+) failing', output)
        if passing:
            p = int(passing.group(1))
            f = int(failing.group(1)) if failing else 0
            return p, p + f

        return 0, 0

    @staticmethod
    def _parse_java_test_output(output: str) -> tuple[int, int]:
        """Parse Maven/Gradle test output."""
        # Maven: "Tests run: 10, Failures: 1, Errors: 0, Skipped: 0"
        maven_match = re.search(
            r'Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+)', output
        )
        if maven_match:
            total = int(maven_match.group(1))
            failures = int(maven_match.group(2))
            errors = int(maven_match.group(3))
            return total - failures - errors, total

        # Gradle: "X tests completed, Y failed"
        gradle_match = re.search(r'(\d+) tests? completed,\s*(\d+) failed', output)
        if gradle_match:
            total = int(gradle_match.group(1))
            failed = int(gradle_match.group(2))
            return total - failed, total

        return 0, 0

    def _find_test_files(self, project_root: str, language: str = "python") -> list[str]:
        """扫描project_root下所有测试文件，不只看tests/目录。"""
        import glob as _glob
        patterns_by_lang = {
            "python": ["**/test_*.py", "**/*_test.py"],
            "go": ["**/*_test.go"],
            "typescript": ["**/*.test.ts", "**/*.spec.ts"],
            "javascript": ["**/*.test.js", "**/*.spec.js"],
            "rust": [],  # Rust tests inline in src files
            "java": ["**/Test*.java", "**/*Test.java"],
        }
        patterns = patterns_by_lang.get(language, patterns_by_lang["python"])
        found = []
        for pattern in patterns:
            found.extend(_glob.glob(
                os.path.join(project_root, pattern),
                recursive=True,
            ))
        return found

    def _rollback(self, backups: dict[str, str]):
        """Restore original file contents."""
        for fpath, content in backups.items():
            self.tools.write_file(fpath, content)
            logger.info("Rolled back %s", fpath)

    def _detect_wrong_file(self, ctx: TaskContext, test_output: str) -> bool:
        """
        确定性检测：是否改了错误的文件。
        条件：测试失败 + 测试报错里提到的文件 ≠ 我们修改的文件
        """
        if not ctx.generator_output:
            return False

        modified_files = {
            os.path.basename(p["file"])
            for p in ctx.generator_output.get("patches", [])
            if p.get("file")
        }

        if not modified_files:
            return False

        # 从测试输出提取出错的文件
        error_files = set()
        # Python: File "xxx.py", line N
        error_files.update(
            os.path.basename(f) for f in re.findall(r'File "([^"]+\.py)"', test_output)
        )
        # Go: file.go:42:
        error_files.update(
            os.path.basename(f) for f in re.findall(r'(\S+\.go):\d+:', test_output)
        )
        # TS/JS: file.ts(42,5):
        error_files.update(
            os.path.basename(f) for f in re.findall(r'(\S+\.(?:ts|js|tsx|jsx))[:(]\d+', test_output)
        )

        # 过滤测试文件和标准库
        error_files = {
            f for f in error_files
            if not f.startswith('test_') and '_test.' not in f
            and 'site-packages' not in f
        }

        if not error_files:
            return False

        # 出错文件和修改文件没有交集 → 可能改错了
        return error_files.isdisjoint(modified_files)
