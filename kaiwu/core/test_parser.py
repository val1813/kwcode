"""
测试输出解析器：从pytest/go/jest输出提取失败和通过的测试名。
纯正则匹配，零LLM调用。供GapDetector、ExecutionStateTracker、Orchestrator共用。
"""

import re

__all__ = ["extract_failing_tests", "extract_passing_tests", "parse_test_failures"]


def _unique(items: list[str]) -> list[str]:
    """Deduplicate while preserving runner output order."""
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _new_failure(test_name: str) -> dict:
    return {
        "test_name": test_name.strip(),
        "expected": "",
        "actual": "",
        "error_type": "",
        "file": "",
        "line": 0,
        "snippet": "",
    }


def extract_failing_tests(output: str) -> list[str]:
    """从pytest/go/jest输出里提取失败的测试名。"""
    if not output:
        return []

    failing = []

    # pytest: "FAILED test_foo.py::TestBar::test_baz[param]"
    failing += re.findall(r'FAILED\s+(\S+)', output)

    # go: "--- FAIL: TestFoo (0.00s)"
    failing += re.findall(r'--- FAIL:\s+(\w+)', output)

    # jest: "✕ should do something (5 ms)" or "× should do something"
    failing += [
        m.strip()
        for m in re.findall(r'[✕×]\s+(.+?)(?:\s+\(\d+\s*ms\)|\s*$)', output, re.MULTILINE)
    ]

    # rust: "test xxx ... FAILED"
    failing += re.findall(r'test\s+(\S+)\s+\.\.\.\s+FAILED', output)

    return _unique(failing)


def extract_passing_tests(output: str) -> list[str]:
    """从输出里提取通过的测试名。"""
    if not output:
        return []

    passing = []

    # pytest: "PASSED test_foo.py::TestBar::test_baz[param]"
    passing += re.findall(r'PASSED\s+(\S+)', output)

    # go: "--- PASS: TestFoo (0.00s)"
    passing += re.findall(r'--- PASS:\s+(\w+)', output)

    # jest: "✓ should do something (5 ms)" or "✔ should do something"
    passing += [
        m.strip()
        for m in re.findall(r'[✓✔]\s+(.+?)(?:\s+\(\d+\s*ms\)|\s*$)', output, re.MULTILINE)
    ]

    # rust: "test xxx ... ok"
    passing += re.findall(r'test\s+(\S+)\s+\.\.\.\s+ok', output)

    return _unique(passing)


def _parse_expected_actual(text: str, failure: dict) -> None:
    """Fill expected/actual when common runner wording is present."""
    patterns = [
        # Go: "expected 5, got 3"
        (r'expected\s+(.+?),\s*got\s+(.+)', "expected", "actual"),
        # Go: "got 3, want 5"
        (r'got\s+(.+?),\s*want\s+(.+)', "actual", "expected"),
        # Go/Jest custom: "want 5, got 3"
        (r'want\s+(.+?),\s*got\s+(.+)', "expected", "actual"),
        # Pytest: "assert 3 == 5"
        (r'assert\s+(.+?)\s*==\s*(.+?)(?:\s*$|\s+where)', "actual", "expected"),
    ]
    for pattern, first_key, second_key in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            failure[first_key] = match.group(1).strip()
            failure[second_key] = match.group(2).strip()
            failure["error_type"] = failure["error_type"] or "AssertionError"
            return

    expected_match = re.search(r'Expected:\s*(.+)', text)
    received_match = re.search(r'Received:\s*(.+)', text)
    if expected_match:
        failure["expected"] = expected_match.group(1).strip()
    if received_match:
        failure["actual"] = received_match.group(1).strip()
    if expected_match or received_match:
        failure["error_type"] = failure["error_type"] or "AssertionError"


def _parse_pytest_failures(output: str) -> list[dict]:
    failures = []

    # 按pytest的FAILURES section分割每个测试块
    # 格式: _____ test_name _____\n file:line: in func\n ... \nE   assert ...
    blocks = re.split(r'(?m)^_{3,}\s+(.+?)\s+_{3,}\s*$', output)

    # blocks[0]是前缀，之后是 [test_name, block_content, test_name, block_content, ...]
    i = 1
    while i < len(blocks) - 1:
        test_name = blocks[i].strip()
        block = blocks[i + 1] if i + 1 < len(blocks) else ""
        i += 2

        failure = _new_failure(test_name)

        # 提取文件和行号: "file.py:42: in test_func"
        loc_match = re.search(r'([\w/\\._-]+\.py):(\d+):', block)
        if loc_match:
            failure["file"] = loc_match.group(1)
            failure["line"] = int(loc_match.group(2))

        # 提取E行（pytest的错误详情）
        e_lines = re.findall(r'^E\s+(.+)$', block, re.MULTILINE)
        e_text = "\n".join(e_lines)
        if e_lines:
            failure["snippet"] = "\n".join(e_lines[:5])

        _parse_expected_actual(e_text, failure)

        # "assert X is True" / "assert X is not None"
        if not failure["error_type"] and re.search(r'assert\s+', e_text):
            failure["error_type"] = "AssertionError"
            # 提取 "where X = func()"
            where_match = re.search(r'where\s+(.+?)\s*=\s*(.+?)(?:\s*$)', e_text)
            if where_match:
                failure["actual"] = where_match.group(1).strip()

        # 异常类型: "TypeError: ..." / "AttributeError: ..."
        exc_match = re.search(
            r'(TypeError|AttributeError|ValueError|KeyError|IndexError|'
            r'ZeroDivisionError|NotImplementedError|RuntimeError|NameError):\s*(.+)',
            block,
        )
        if exc_match:
            failure["error_type"] = exc_match.group(1)
            failure["snippet"] = exc_match.group(2).strip()[:200]

        # "where None = func()" 模式
        where_none = re.search(r'where None = (\w+)\(', block)
        if where_none and not failure["actual"]:
            failure["actual"] = "None"
            failure["error_type"] = failure["error_type"] or "AssertionError"

        failures.append(failure)

    return failures


def _parse_pytest_summary_failures(output: str) -> list[dict]:
    failures = []
    for match in re.finditer(r'FAILED\s+(\S+)\s*-\s*(.+)', output):
        failure = _new_failure(match.group(1))
        reason = match.group(2).strip()
        failure["error_type"] = "AssertionError"
        failure["snippet"] = reason[:200]
        _parse_expected_actual(reason, failure)
        failures.append(failure)
    return failures


def _parse_go_failures(output: str) -> list[dict]:
    failures = []
    blocks = re.finditer(
        r'(?ms)^--- FAIL:\s+(\S+).*?\n(.*?)(?=^--- (?:FAIL|PASS):|^FAIL\b|\Z)',
        output,
    )
    for match in blocks:
        failure = _new_failure(match.group(1))
        block = match.group(2)

        loc_match = re.search(r'(?m)^\s+([^\s:]+\.go):(\d+):\s*(.+)$', block)
        if loc_match:
            failure["file"] = loc_match.group(1)
            failure["line"] = int(loc_match.group(2))
            failure["snippet"] = loc_match.group(3).strip()[:200]
        else:
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            if lines:
                failure["snippet"] = lines[0][:200]

        _parse_expected_actual(block, failure)
        failure["error_type"] = failure["error_type"] or "TestFailure"
        failures.append(failure)

    return failures


def _parse_jest_failures(output: str) -> list[dict]:
    failures = []
    current_file = ""

    file_match = re.search(r'(?m)^FAIL\s+(.+)$', output)
    if file_match:
        current_file = file_match.group(1).strip()

    blocks = list(re.finditer(
        r'(?ms)^\s*●\s+(.+?)\s*\n(.*?)(?=^\s*●\s+|^Test Suites:|^Tests:|\Z)',
        output,
    ))

    for match in blocks:
        failure = _new_failure(match.group(1).strip())
        block = match.group(2)
        failure["file"] = current_file

        stack_match = re.search(r'\(([^()\s]+?\.(?:ts|tsx|js|jsx)):(\d+):\d+\)', block)
        if stack_match:
            failure["file"] = stack_match.group(1)
            failure["line"] = int(stack_match.group(2))
        else:
            frame_match = re.search(r'(?m)^\s*>\s*(\d+)\s*\|', block)
            if frame_match:
                failure["line"] = int(frame_match.group(1))

        _parse_expected_actual(block, failure)
        snippet_lines = [line.strip() for line in block.splitlines() if line.strip()]
        if snippet_lines:
            failure["snippet"] = "\n".join(snippet_lines[:5])[:200]
        failure["error_type"] = failure["error_type"] or "AssertionError"
        failures.append(failure)

    if not failures:
        for name in re.findall(r'[✕×]\s+(.+?)(?:\s+\(\d+\s*ms\)|\s*$)', output, re.MULTILINE):
            failure = _new_failure(name)
            failure["file"] = current_file
            failure["error_type"] = "AssertionError"
            failures.append(failure)

    return failures


def parse_test_failures(output: str) -> list[dict]:
    """
    从pytest/go test/jest输出解析每个失败测试的结构化信息。
    返回: [{"test_name", "expected", "actual", "error_type", "file", "line", "snippet"}]
    """
    if not output:
        return []

    failures = _parse_pytest_failures(output)
    if not failures:
        failures = _parse_pytest_summary_failures(output)
    if not failures and "--- FAIL:" in output:
        failures = _parse_go_failures(output)
    if not failures and ("FAIL " in output or "● " in output or "✕" in output or "×" in output):
        failures = _parse_jest_failures(output)
    return failures[:10]  # 最多10个，避免prompt过长
