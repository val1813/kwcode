"""
GapDetector: 从测试输出确定性计算任务缺口类型，驱动所有后续决策。
纯确定性，零LLM调用。
"""

import os
import re
import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional

__all__ = ["GapType", "Gap", "GapDetector", "GAP_TO_EXPERT_TYPE"]

logger = logging.getLogger(__name__)


class GapType(Enum):
    NONE = "none"                             # 所有测试通过，任务完成
    NOT_IMPLEMENTED = "not_implemented"       # NotImplementedError/raise存根
    STUB_RETURNS_NONE = "stub_returns_none"   # pass存根导致返回None
    LOGIC_ERROR = "logic_error"              # 断言失败，逻辑错误
    MISSING_DEP = "missing_dep"              # ImportError/ModuleNotFoundError
    SYNTAX_STRUCTURAL = "syntax_structural"  # IndentationError/SyntaxError
    MISSING_TOOLCHAIN = "missing_toolchain"  # go/node/npm not found
    WRONG_FILE = "wrong_file"                # 改了错误的文件
    NO_TEST = "no_test"                      # 找不到测试文件
    ENVIRONMENT = "environment"              # 其他环境问题
    UNKNOWN = "unknown"                      # 无法分类


@dataclass
class Gap:
    gap_type: GapType
    confidence: float       # 0-1，这个判断有多确定
    files: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    error_msg: str = ""
    suggestion: str = ""


# 确定性Gap类型到专家类型的映射
GAP_TO_EXPERT_TYPE = {
    GapType.NOT_IMPLEMENTED:   "locator_repair",
    GapType.STUB_RETURNS_NONE: "locator_repair",
    GapType.LOGIC_ERROR:       "locator_repair",
    GapType.MISSING_DEP:       "locator_repair",
    GapType.SYNTAX_STRUCTURAL: "locator_repair",
    GapType.MISSING_TOOLCHAIN: "env_fix",
    GapType.NO_TEST:           "codegen",
    GapType.WRONG_FILE:        "locator_repair",
    GapType.ENVIRONMENT:       "env_fix",
    GapType.UNKNOWN:           "locator_repair",
}


class GapDetector:
    """
    纯确定性，零LLM调用。
    从测试输出正则匹配计算GapType。
    """

    def compute(self, test_output: str, project_root: str = ".") -> Gap:
        """主入口：从测试输出计算Gap。"""

        if not test_output or test_output.strip() == "":
            return Gap(GapType.NO_TEST, 0.9, [], [], "", "找不到测试输出")

        # 按优先级匹配，第一个命中的优先

        # 1. 工具链缺失（最高优先级，环境问题先解决）
        if self._match_toolchain(test_output):
            return self._build_toolchain_gap(test_output)

        # 2. 依赖缺失
        if 'ModuleNotFoundError' in test_output or 'ImportError' in test_output:
            return self._build_missing_dep_gap(test_output)

        # 3. 未实现（NotImplementedError）
        if ('NotImplementedError' in test_output or
                'not implemented' in test_output.lower()):
            return self._build_not_implemented_gap(test_output, project_root)

        # 4. pass存根返回None
        if self._is_stub_returns_none(test_output):
            return self._build_stub_none_gap(test_output, project_root)

        # 5. 语法/缩进错误
        if 'IndentationError' in test_output or 'SyntaxError' in test_output:
            return self._build_syntax_gap(test_output)

        # 6. 断言失败（逻辑错误）
        if ('AssertionError' in test_output or
                'FAILED' in test_output or
                '--- FAIL:' in test_output):
            return self._build_logic_gap(test_output, project_root)

        # 7. 所有测试通过
        if self._all_passed(test_output):
            return Gap(GapType.NONE, 1.0, [], [], "", "")

        return Gap(GapType.UNKNOWN, 0.3, [], [], test_output[:200], "")

    def _match_toolchain(self, output: str) -> bool:
        patterns = [
            r'go: not found',
            r'node: not found',
            r'npm: not found',
            r'command not found: go',
            r'command not found: node',
            r'command not found: npm',
            r'command not found: cargo',
            r'command not found: javac',
            r'/bin/sh: \d+: \w+: not found',
            r'Toolchain missing:',
        ]
        return any(re.search(p, output) for p in patterns)

    def _is_stub_returns_none(self, output: str) -> bool:
        """None相关错误通常来自pass存根。扩展检测：assert None == X 模式。"""
        # 经典模式：NoneType + 操作错误
        if ('NoneType' in output and
                ('has no attribute' in output or
                 'is not iterable' in output or
                 'unsupported operand' in output or
                 'is not subscriptable' in output or
                 'object is not callable' in output)):
            return True
        # 扩展模式：多个 assert None == X（pass函数返回None被直接断言）
        none_asserts = len(re.findall(r'assert None ==', output))
        if none_asserts >= 2:
            return True
        # 扩展模式：where None = func_name()（pytest输出格式）
        none_calls = len(re.findall(r'where None = \w+\(', output))
        if none_calls >= 2:
            return True
        return False

    def _all_passed(self, output: str) -> bool:
        patterns = [
            r'\d+ passed',
            r'^PASS$',
            r'^ok\s+',
            r'Tests:.*\d+ passed',
            r'All tests passed',
            r'test result: ok',
            r'\[no test files\]',  # go: no test files = not a failure
        ]
        return any(re.search(p, output, re.MULTILINE) for p in patterns)

    def _build_toolchain_gap(self, output: str) -> Gap:
        # 提取缺失的工具名
        tool = "unknown"
        for name in ("go", "node", "npm", "cargo", "javac", "rustc"):
            if name in output.lower():
                tool = name
                break
        return Gap(
            GapType.MISSING_TOOLCHAIN, 0.95,
            [], [], output[:200],
            f"工具链缺失：{tool}，需要安装"
        )

    def _build_missing_dep_gap(self, output: str) -> Gap:
        # 只提取 No module named 'xxx'
        pkgs = re.findall(r"No module named '([^']+)'", output)
        # 去掉子模块，只取顶层包名
        pkgs = list(set(pkg.split(".")[0] for pkg in pkgs))
        files = self._extract_error_files(output)
        return Gap(
            GapType.MISSING_DEP, 0.95,
            files, [], output[:200],
            f"缺少依赖：{', '.join(pkgs)}" if pkgs else "缺少依赖"
        )

    def _build_not_implemented_gap(self, output: str, project_root: str) -> Gap:
        files = self._extract_error_files(output)
        functions = self._extract_function_names(output)
        # AST存根扫描：找到所有pass函数，提供完整target_functions
        stub_functions = self._scan_stubs_in_files(files, project_root)
        if stub_functions:
            functions = stub_functions
        return Gap(
            GapType.NOT_IMPLEMENTED, 0.9,
            files, functions, output[:200],
            "函数未实现，需要完整实现"
        )

    def _build_stub_none_gap(self, output: str, project_root: str) -> Gap:
        files = self._extract_error_files(output)
        functions = self._extract_function_names(output)
        # AST存根扫描
        stub_functions = self._scan_stubs_in_files(files, project_root)
        if stub_functions:
            functions = stub_functions
        return Gap(
            GapType.STUB_RETURNS_NONE, 0.85,
            files, functions, output[:200],
            "pass存根返回None，需要实现函数体"
        )

    def _build_syntax_gap(self, output: str) -> Gap:
        files = self._extract_error_files(output)
        return Gap(
            GapType.SYNTAX_STRUCTURAL, 0.95,
            files, [], output[:200],
            "语法或缩进错误"
        )

    def _build_logic_gap(self, output: str, project_root: str) -> Gap:
        files = self._extract_error_files(output)
        functions = self._extract_function_names(output)
        return Gap(
            GapType.LOGIC_ERROR, 0.8,
            files, functions, output[:200],
            "断言失败，逻辑错误"
        )

    def _extract_error_files(self, output: str) -> list[str]:
        """从测试输出提取出错的文件路径。"""
        files = []
        # Python: File "xxx.py", line N
        files += re.findall(r'File "([^"]+\.py)"', output)
        # Go: file.go:42:5:
        files += re.findall(r'(\S+\.go):\d+:', output)
        # Rust: --> src/main.rs:42:5
        files += re.findall(r'-->\s+(\S+\.rs):\d+', output)
        # TypeScript/JavaScript: file.ts(42,5):
        files += re.findall(r'(\S+\.(?:ts|js|tsx|jsx))[:(]\d+', output)
        # 去重，过滤测试文件和标准库
        result = []
        seen = set()
        for f in files:
            basename = os.path.basename(f)
            if basename in seen:
                continue
            # 过滤标准库和虚拟环境
            if '/lib/python' in f or '/site-packages/' in f:
                continue
            seen.add(basename)
            result.append(f)
        return result[:5]  # 最多5个

    def _extract_function_names(self, output: str) -> list[str]:
        """从测试输出提取相关函数名。"""
        functions = []
        # Python traceback: in function_name
        functions += re.findall(r'in (\w+)\n', output)
        # pytest: test_foo.py::TestBar::test_baz → 提取被测函数
        # Go: --- FAIL: TestFoo
        functions += re.findall(r'--- FAIL:\s+(\w+)', output)
        # 过滤测试函数本身和常见框架函数
        skip = {'<module>', 'wrapper', 'inner', 'setUp', 'tearDown',
                'run', 'main', '__init__', 'execute'}
        result = [f for f in functions if f not in skip and not f.startswith('test_')]
        return list(set(result))[:5]

    def _scan_stubs_in_files(self, files: list[str], project_root: str) -> list[str]:
        """AST扫描文件中的pass/raise NotImplementedError存根函数。"""
        import ast as _ast
        stub_functions = []
        for fpath in files[:3]:
            # 构建绝对路径
            if not os.path.isabs(fpath):
                fpath = os.path.join(project_root, fpath)
            if not os.path.exists(fpath) or not fpath.endswith('.py'):
                continue
            try:
                with open(fpath, encoding='utf-8', errors='ignore') as f:
                    source = f.read()
                tree = _ast.parse(source)
            except Exception:
                continue
            for node in _ast.walk(tree):
                if not isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    continue
                if node.name.startswith('__') and node.name.endswith('__'):
                    continue
                if self._is_stub_body(node.body):
                    stub_functions.append(node.name)
        return stub_functions

    @staticmethod
    def _is_stub_body(body: list) -> bool:
        """判断函数体是否是存根。"""
        import ast as _ast
        if not body:
            return True
        # 跳过docstring
        real_body = body
        if (len(body) >= 1 and isinstance(body[0], _ast.Expr) and
                isinstance(body[0].value, _ast.Constant) and
                isinstance(body[0].value.value, str)):
            real_body = body[1:]
        if not real_body:
            return True
        if len(real_body) == 1:
            stmt = real_body[0]
            if isinstance(stmt, _ast.Pass):
                return True
            if isinstance(stmt, _ast.Raise):
                return True
            if isinstance(stmt, _ast.Expr) and isinstance(stmt.value, _ast.Constant):
                if stmt.value.value is ...:
                    return True
            if isinstance(stmt, _ast.Return) and stmt.value is None:
                return True
        return False
