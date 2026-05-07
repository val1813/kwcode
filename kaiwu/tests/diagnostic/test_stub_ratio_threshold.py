"""
专项2：stub_ratio阈值验证测试集
目标：找到stub_ratio的最优阈值，验证存根检测路由准确性
成功标准：路由准确率 > 85%
"""

import ast
import os
import tempfile
import pytest
from kaiwu.core.gap_detector import GapDetector, GapType, Gap
from kaiwu.core.context import TaskContext


# ══════════════════════════════════════
# 存根文件样本（高stub_ratio → scope=whole_file）
# ══════════════════════════════════════

STUB_FILES = [
    # stub_ratio = 1.0 (全部是存根)
    (
        "all_stubs.py",
        '''
class Calculator:
    def add(self, a, b):
        pass

    def subtract(self, a, b):
        pass

    def multiply(self, a, b):
        pass

    def divide(self, a, b):
        pass
''',
        "whole_file_impl",
        "4/4 stubs, ratio=1.0",
    ),
    # stub_ratio = 1.0 (NotImplementedError)
    (
        "not_impl.py",
        '''
def parse_config(path):
    raise NotImplementedError

def validate_config(config):
    raise NotImplementedError

def apply_config(config):
    raise NotImplementedError
''',
        "whole_file_impl",
        "3/3 NotImplementedError, ratio=1.0",
    ),
    # stub_ratio = 0.75
    (
        "mostly_stubs.py",
        '''
import os

def get_version():
    return "1.0.0"

def load_data(path):
    pass

def process_data(data):
    pass

def save_results(results, path):
    pass
''',
        "whole_file_impl",
        "3/4 stubs, ratio=0.75",
    ),
    # stub_ratio = 0.67
    (
        "two_thirds_stub.py",
        '''
class Parser:
    def __init__(self, text):
        self.text = text

    def tokenize(self):
        pass

    def parse(self):
        pass
''',
        "whole_file_impl",
        "2/3 stubs (excluding __init__), ratio=0.67",
    ),
    # stub_ratio = 0.5 (边界)
    (
        "half_stub.py",
        '''
def helper():
    return 42

def compute(x, y):
    return helper() + x

def transform(data):
    pass

def validate(data):
    pass
''',
        "whole_file_impl",
        "2/4 stubs, ratio=0.5 (borderline)",
    ),
]

# ══════════════════════════════════════
# Bug修复文件样本（应路由到 locator_repair）
# ══════════════════════════════════════

BUG_FIX_FILES = [
    # 完整实现但有bug
    (
        "buggy_sort.py",
        '''
def bubble_sort(arr):
    n = len(arr)
    for i in range(n):
        for j in range(0, n-i-1):
            if arr[j] < arr[j+1]:  # bug: should be >
                arr[j], arr[j+1] = arr[j+1], arr[j]
    return arr
''',
        "locator_repair",
        "logic bug in sort (wrong comparison)",
    ),
    # 完整实现但有off-by-one
    (
        "buggy_search.py",
        '''
def binary_search(arr, target):
    left, right = 0, len(arr)  # bug: should be len(arr)-1
    while left <= right:
        mid = (left + right) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            left = mid + 1
        else:
            right = mid - 1
    return -1
''',
        "locator_repair",
        "off-by-one in binary search",
    ),
    # 完整实现但有类型错误
    (
        "buggy_format.py",
        '''
def format_name(first, last):
    return first + " " + last

def format_age(age):
    return "Age: " + age  # bug: should be str(age)

def format_full(first, last, age):
    return format_name(first, last) + ", " + format_age(age)
''',
        "locator_repair",
        "type error (missing str())",
    ),
    # 完整实现，复杂逻辑
    (
        "complex_logic.py",
        '''
import re

def parse_email(text):
    pattern = r'[\\w.+-]+@[\\w-]+\\.[\\w.-]+'
    matches = re.findall(pattern, text)
    return matches

def validate_email(email):
    if '@' not in email:
        return False
    parts = email.split('@')
    if len(parts) != 2:
        return False
    return len(parts[1]) > 0

def extract_domain(email):
    return email.split('@')[1] if '@' in email else None
''',
        "locator_repair",
        "fully implemented, no stubs",
    ),
    # 只有一个小存根（stub_ratio很低）
    (
        "mostly_done.py",
        '''
class DataLoader:
    def __init__(self, path):
        self.path = path
        self.data = None

    def load(self):
        with open(self.path) as f:
            self.data = f.read()
        return self.data

    def parse(self):
        if not self.data:
            self.load()
        return self.data.split("\\n")

    def validate(self):
        pass  # TODO: add validation later

    def transform(self):
        lines = self.parse()
        return [line.strip() for line in lines if line.strip()]
''',
        "locator_repair",
        "1/5 stub, ratio=0.2 (mostly implemented)",
    ),
]


class TestStubRatioThreshold:
    """验证Gate对存根文件 vs bug修复文件的路由准确率。"""

    def _compute_stub_ratio(self, content: str) -> float:
        """计算文件的stub_ratio。"""
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return 0.0

        func_count = 0
        stub_count = 0
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # 跳过__init__等dunder方法
                if node.name.startswith('__') and node.name.endswith('__'):
                    continue
                func_count += 1
                if self._is_stub(node):
                    stub_count += 1

        if func_count == 0:
            return 0.0
        return stub_count / func_count

    def _is_stub(self, node) -> bool:
        """判断函数是否是存根。"""
        body = node.body
        # 跳过docstring
        if (body and isinstance(body[0], ast.Expr) and
                isinstance(body[0].value, ast.Constant) and
                isinstance(body[0].value.value, str)):
            body = body[1:]
        if not body:
            return True
        if len(body) == 1:
            stmt = body[0]
            if isinstance(stmt, ast.Pass):
                return True
            if isinstance(stmt, ast.Raise):
                return True
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                if stmt.value.value is ...:
                    return True
        return False

    @pytest.mark.parametrize("filename,content,expected,desc", STUB_FILES,
                             ids=[s[3] for s in STUB_FILES])
    def test_stub_files_detected(self, filename, content, expected, desc):
        """存根文件应该被检测为whole_file scope。"""
        ratio = self._compute_stub_ratio(content)
        # 阈值0.5：超过50%的函数是存根 → whole_file scope
        detected = "whole_file_impl" if ratio >= 0.5 else "locator_repair"
        assert detected == expected, (
            f"[{desc}] stub_ratio={ratio:.2f}, "
            f"期望路由={expected}, 实际路由={detected}"
        )

    @pytest.mark.parametrize("filename,content,expected,desc", BUG_FIX_FILES,
                             ids=[s[3] for s in BUG_FIX_FILES])
    def test_bugfix_files_not_stub(self, filename, content, expected, desc):
        """Bug修复文件不应该被路由到whole_file scope。"""
        ratio = self._compute_stub_ratio(content)
        detected = "whole_file_impl" if ratio >= 0.5 else "locator_repair"
        assert detected == expected, (
            f"[{desc}] stub_ratio={ratio:.2f}, "
            f"期望路由={expected}, 实际路由={detected}"
        )

    def test_overall_routing_accuracy(self):
        """验证整体路由准确率 > 85%。"""
        all_cases = STUB_FILES + BUG_FIX_FILES
        correct = 0
        total = len(all_cases)
        failures = []

        for filename, content, expected, desc in all_cases:
            ratio = self._compute_stub_ratio(content)
            detected = "whole_file_impl" if ratio >= 0.5 else "locator_repair"
            if detected == expected:
                correct += 1
            else:
                failures.append(f"  [{desc}] ratio={ratio:.2f} 期望={expected} 实际={detected}")

        accuracy = correct / total
        assert accuracy >= 0.85, (
            f"路由准确率 {accuracy:.0%} ({correct}/{total}) 低于85%阈值。\n"
            f"失败样本：\n" + "\n".join(failures)
        )

    def test_gap_type_maps_to_locator_repair(self):
        """验证NOT_IMPLEMENTED/STUB_RETURNS_NONE现在映射到locator_repair（由Generator通过scope处理）。"""
        from kaiwu.core.gap_detector import GAP_TO_EXPERT_TYPE
        assert GAP_TO_EXPERT_TYPE[GapType.NOT_IMPLEMENTED] == "locator_repair"
        assert GAP_TO_EXPERT_TYPE[GapType.STUB_RETURNS_NONE] == "locator_repair"
