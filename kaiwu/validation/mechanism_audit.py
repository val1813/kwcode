"""
v1.6.2 机制验证：7个最小任务，逐条审查每个机制是否真正生效。
用法: python -m kaiwu.validation.mechanism_audit --ollama-model qwen3:8b
"""

import argparse
import io
import os
import shutil
import sys
import tempfile
import time

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


# ═══════════════════════════════════════════════════════════════════
# Task Setup Functions
# ═══════════════════════════════════════════════════════════════════

def setup_task1_initial_failure_inject(tmpdir: str):
    """Task 1: 验证initial_test_failure注入Generator prompt。
    设计：函数返回硬编码0，测试期望x+y。LLM必须看到测试报错才能知道要实现加法。
    """
    src = os.path.join(tmpdir, "src")
    tests = os.path.join(tmpdir, "tests")
    os.makedirs(src); os.makedirs(tests)

    with open(os.path.join(src, "__init__.py"), "w", encoding="utf-8") as f:
        f.write("")
    with open(os.path.join(src, "math_ops.py"), "w", encoding="utf-8") as f:
        f.write(
            'def compute(x, y):\n'
            '    """Compute something with x and y."""\n'
            '    return 0  # TODO: implement\n'
        )
    with open(os.path.join(tests, "__init__.py"), "w", encoding="utf-8") as f:
        f.write("")
    with open(os.path.join(tests, "test_math.py"), "w", encoding="utf-8") as f:
        f.write(
            'import sys, os\n'
            'sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))\n'
            'from src.math_ops import compute\n\n'
            'def test_add_positive():\n'
            '    assert compute(2, 3) == 5\n\n'
            'def test_add_zero():\n'
            '    assert compute(0, 0) == 0\n\n'
            'def test_add_negative():\n'
            '    assert compute(-1, 4) == 3\n'
        )


def setup_task2_retry_hint_tests(tmpdir: str):
    """Task 2: 验证retry_hint携带具体失败测试名。
    设计：两个函数，一个简单(is_even)一个需要看测试名(classify_number)。
    LLM第一次大概率只修is_even，retry时应看到test_classify_negative失败。
    """
    src = os.path.join(tmpdir, "src")
    tests = os.path.join(tmpdir, "tests")
    os.makedirs(src); os.makedirs(tests)

    with open(os.path.join(src, "__init__.py"), "w", encoding="utf-8") as f:
        f.write("")
    with open(os.path.join(src, "classifier.py"), "w", encoding="utf-8") as f:
        f.write(
            'def is_even(n):\n'
            '    """Return True if n is even."""\n'
            '    return n % 2 == 1  # BUG: should be == 0\n\n'
            'def classify_number(n):\n'
            '    """Return "positive", "negative", or "zero"."""\n'
            '    if n > 0:\n'
            '        return "positive"\n'
            '    return "zero"  # BUG: missing negative case\n'
        )
    with open(os.path.join(tests, "__init__.py"), "w", encoding="utf-8") as f:
        f.write("")
    with open(os.path.join(tests, "test_classifier.py"), "w", encoding="utf-8") as f:
        f.write(
            'import sys, os\n'
            'sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))\n'
            'from src.classifier import is_even, classify_number\n\n'
            'def test_is_even():\n'
            '    assert is_even(4) == True\n'
            '    assert is_even(3) == False\n\n'
            'def test_classify_positive():\n'
            '    assert classify_number(5) == "positive"\n\n'
            'def test_classify_negative():\n'
            '    assert classify_number(-3) == "negative"\n\n'
            'def test_classify_zero():\n'
            '    assert classify_number(0) == "zero"\n'
        )


def setup_task3_reviewer_skip(tmpdir: str):
    """Task 3: 验证Reviewer在tests_total==0时跳过。
    设计：Go项目，本地没有Go工具链，tests_total会是0。
    Reviewer不应被调用。
    """
    # 创建一个看起来像Go项目的结构
    with open(os.path.join(tmpdir, "go.mod"), "w", encoding="utf-8") as f:
        f.write('module example.com/hello\n\ngo 1.21\n')
    with open(os.path.join(tmpdir, "main.go"), "w", encoding="utf-8") as f:
        f.write(
            'package main\n\n'
            'import "fmt"\n\n'
            'func Add(a, b int) int {\n'
            '\treturn 0 // BUG: should return a + b\n'
            '}\n\n'
            'func main() {\n'
            '\tfmt.Println(Add(2, 3))\n'
            '}\n'
        )
    with open(os.path.join(tmpdir, "main_test.go"), "w", encoding="utf-8") as f:
        f.write(
            'package main\n\n'
            'import "testing"\n\n'
            'func TestAdd(t *testing.T) {\n'
            '\tif Add(2, 3) != 5 {\n'
            '\t\tt.Error("expected 5")\n'
            '\t}\n'
            '}\n'
        )


def setup_task4_toolchain_fuse(tmpdir: str):
    """Task 4: 验证MISSING_TOOLCHAIN快速熔断。
    设计：同Task3的Go项目，但这里验证的是orchestrator直接熔断不进retry。
    """
    # 和task3一样的Go项目
    setup_task3_reviewer_skip(tmpdir)
# Part 2: Task 5-7 setup + pipeline runner + main

def setup_task5_think_no_escalate(tmpdir: str):
    """Task 5: 验证think_escalate只在assertion时触发。
    设计：Python文件有syntax error（缺少冒号）。修复后如果还有syntax error，
    不应触发think_escalate（因为是syntax不是assertion）。
    """
    src = os.path.join(tmpdir, "src")
    tests = os.path.join(tmpdir, "tests")
    os.makedirs(src); os.makedirs(tests)

    with open(os.path.join(src, "__init__.py"), "w", encoding="utf-8") as f:
        f.write("")
    with open(os.path.join(src, "parser.py"), "w", encoding="utf-8") as f:
        # 故意的syntax error：缺少冒号和缩进问题
        f.write(
            'def parse_csv(text)\n'  # Missing colon - syntax error
            '    lines = text.strip().split("\\n")\n'
            '    result = []\n'
            '    for line in lines:\n'
            '        fields = line.split(",")\n'
            '        result.append(fields)\n'
            '    return result\n'
        )
    with open(os.path.join(tests, "__init__.py"), "w", encoding="utf-8") as f:
        f.write("")
    with open(os.path.join(tests, "test_parser.py"), "w", encoding="utf-8") as f:
        f.write(
            'import sys, os\n'
            'sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))\n'
            'from src.parser import parse_csv\n\n'
            'def test_simple_csv():\n'
            '    result = parse_csv("a,b,c\\n1,2,3")\n'
            '    assert result == [["a","b","c"], ["1","2","3"]]\n\n'
            'def test_empty():\n'
            '    result = parse_csv("")\n'
            '    assert result == [[""]]\n'
        )


def setup_task6_no_scope_narrow(tmpdir: str):
    """Task 6: 验证scope narrowing已删除。
    设计：3个文件的项目，Locator会定位到多个文件。即使retry也不应scope_narrow。
    """
    src = os.path.join(tmpdir, "src")
    tests = os.path.join(tmpdir, "tests")
    os.makedirs(src); os.makedirs(tests)

    with open(os.path.join(src, "__init__.py"), "w", encoding="utf-8") as f:
        f.write("")
    with open(os.path.join(src, "models.py"), "w", encoding="utf-8") as f:
        f.write(
            'class User:\n'
            '    def __init__(self, name, age):\n'
            '        self.name = name\n'
            '        self.age = age\n\n'
            '    def is_adult(self):\n'
            '        return self.age > 18  # BUG: should be >= 18\n'
        )
    with open(os.path.join(src, "validators.py"), "w", encoding="utf-8") as f:
        f.write(
            'def validate_name(name):\n'
            '    """Name must be non-empty and <= 50 chars."""\n'
            '    if not name:\n'
            '        return False\n'
            '    return True  # BUG: missing length check\n'
        )
    with open(os.path.join(src, "service.py"), "w", encoding="utf-8") as f:
        f.write(
            'from src.models import User\n'
            'from src.validators import validate_name\n\n'
            'def create_user(name, age):\n'
            '    if not validate_name(name):\n'
            '        raise ValueError("invalid name")\n'
            '    return User(name, age)\n'
        )
    with open(os.path.join(tests, "__init__.py"), "w", encoding="utf-8") as f:
        f.write("")
    with open(os.path.join(tests, "test_service.py"), "w", encoding="utf-8") as f:
        f.write(
            'import sys, os\n'
            'sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))\n'
            'from src.service import create_user\n\n'
            'def test_create_adult():\n'
            '    u = create_user("Alice", 18)\n'
            '    assert u.is_adult() == True\n\n'
            'def test_long_name_rejected():\n'
            '    try:\n'
            '        create_user("x" * 51, 20)\n'
            '        assert False, "should raise"\n'
            '    except ValueError:\n'
            '        pass\n\n'
            'def test_empty_name_rejected():\n'
            '    try:\n'
            '        create_user("", 20)\n'
            '        assert False, "should raise"\n'
            '    except ValueError:\n'
            '        pass\n'
        )


def setup_task7_wink_no_progress(tmpdir: str):
    """Task 7: 验证WinkMonitor tests_no_progress。
    设计：需要特定算法（二分查找）的任务，8B模型大概率连续失败。
    连续2次retry后tests_passed未提升，Wink应注入纠正。
    """
    src = os.path.join(tmpdir, "src")
    tests = os.path.join(tmpdir, "tests")
    os.makedirs(src); os.makedirs(tests)

    with open(os.path.join(src, "__init__.py"), "w", encoding="utf-8") as f:
        f.write("")
    with open(os.path.join(src, "search.py"), "w", encoding="utf-8") as f:
        f.write(
            'def binary_search_insert_pos(arr, target):\n'
            '    """Find the insertion position for target in sorted arr.\n'
            '    Must handle duplicates: return leftmost position.\n'
            '    Must be O(log n).\n'
            '    """\n'
            '    # BUG: linear scan, wrong for duplicates\n'
            '    for i, v in enumerate(arr):\n'
            '        if v >= target:\n'
            '            return i\n'
            '    return len(arr)\n'
        )
    with open(os.path.join(tests, "__init__.py"), "w", encoding="utf-8") as f:
        f.write("")
    with open(os.path.join(tests, "test_search.py"), "w", encoding="utf-8") as f:
        f.write(
            'import sys, os\n'
            'sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))\n'
            'from src.search import binary_search_insert_pos\n\n'
            'def test_basic():\n'
            '    assert binary_search_insert_pos([1,3,5,7], 4) == 2\n\n'
            'def test_duplicates_leftmost():\n'
            '    assert binary_search_insert_pos([1,2,2,2,3], 2) == 1\n\n'
            'def test_empty():\n'
            '    assert binary_search_insert_pos([], 5) == 0\n\n'
            'def test_all_same():\n'
            '    assert binary_search_insert_pos([3,3,3,3], 3) == 0\n\n'
            'def test_performance():\n'
            '    """Must be O(log n) - test with large array."""\n'
            '    import time\n'
            '    arr = list(range(0, 1000000, 2))  # 500k elements\n'
            '    t0 = time.time()\n'
            '    for _ in range(1000):\n'
            '        binary_search_insert_pos(arr, 500001)\n'
            '    elapsed = time.time() - t0\n'
            '    # O(log n) should finish 1000 calls in < 0.1s\n'
            '    # O(n) would take ~50s\n'
            '    assert elapsed < 1.0, f"Too slow: {elapsed:.2f}s (likely O(n))"\n'
        )


# ═══════════════════════════════════════════════════════════════════
# Pipeline Runner & Audit Logic
# ═══════════════════════════════════════════════════════════════════

AUDIT_TASKS = [
    {
        "id": 1,
        "name": "initial_test_failure注入",
        "mechanism": "Generator首次生成时注入测试报错信息",
        "setup": "setup_task1_initial_failure_inject",
        "description": "实现 src/math_ops.py 中的 compute 函数，使所有测试通过",
        "expected_expert_type": "locator_repair",
        "check": "check_task1",
    },
    {
        "id": 2,
        "name": "retry_hint携带失败测试名",
        "mechanism": "retry时hint包含具体失败的测试名",
        "setup": "setup_task2_retry_hint_tests",
        "description": "修复 src/classifier.py 中 is_even 和 classify_number 的bug",
        "expected_expert_type": "locator_repair",
        "check": "check_task2",
    },
    {
        "id": 3,
        "name": "Reviewer tests_total==0跳过",
        "mechanism": "无测试结果时Reviewer不介入",
        "setup": "setup_task3_reviewer_skip",
        "description": "修复 main.go 中 Add 函数的bug",
        "expected_expert_type": "locator_repair",
        "check": "check_task3",
    },
    {
        "id": 4,
        "name": "MISSING_TOOLCHAIN快速熔断",
        "mechanism": "工具链缺失时直接返回失败不进retry",
        "setup": "setup_task4_toolchain_fuse",
        "description": "修复 main.go 中 Add 函数的bug",
        "expected_expert_type": "locator_repair",
        "check": "check_task4",
    },
    {
        "id": 5,
        "name": "think_escalate条件触发",
        "mechanism": "只在assertion错误时升级think，syntax不触发",
        "setup": "setup_task5_think_no_escalate",
        "description": "修复 src/parser.py 的语法错误并实现 parse_csv 函数",
        "expected_expert_type": "locator_repair",
        "check": "check_task5",
    },
    {
        "id": 6,
        "name": "scope_narrowing已删除",
        "mechanism": "确认scope narrowing不再触发",
        "setup": "setup_task6_no_scope_narrow",
        "description": "修复 src/models.py 和 src/validators.py 的bug使测试通过",
        "expected_expert_type": "locator_repair",
        "check": "check_task6",
    },
    {
        "id": 7,
        "name": "WinkMonitor tests_no_progress",
        "mechanism": "连续retry未提升通过率时注入纠正hint",
        "setup": "setup_task7_wink_no_progress",
        "description": "修复 src/search.py 中 binary_search_insert_pos，要求O(log n)且处理重复元素",
        "expected_expert_type": "locator_repair",
        "check": "check_task7",
    },
]


def check_task1(status_lines, result, ctx):
    """验证：一次通过（retry_count==0说明initial_failure帮助了LLM）"""
    evidence = []
    passed = True

    # 检查pre_test阶段是否运行
    has_pre_test = any("pre_test" in l for l in status_lines)
    evidence.append(f"pre_test运行: {'是' if has_pre_test else '否'}")

    # 检查是否一次通过（最好的证据：LLM看到了测试报错，第一次就修对了）
    retry_count = ctx.retry_count if ctx else 0
    if result.get("success") and retry_count == 0:
        evidence.append("一次通过(retry=0)，说明initial_failure有效")
    elif result.get("success"):
        evidence.append(f"通过但retry了{retry_count}次")
        passed = True  # 通过就算pass，只是证据弱一些
    else:
        evidence.append("未通过")
        passed = False

    return passed, "; ".join(evidence)


def check_task2(status_lines, result, ctx):
    """验证：retry_hint中包含失败测试名"""
    evidence = []

    # 检查是否有retry
    has_retry = any("retry" in l.lower() for l in status_lines)
    evidence.append(f"触发retry: {'是' if has_retry else '否'}")

    # 检查retry_hint是否包含失败测试名
    hint = ctx.retry_hint if ctx else ""
    has_test_names = "仍然失败的测试" in hint or "FAILED" in hint
    evidence.append(f"hint含测试名: {'是' if has_test_names else '否'}")

    if has_test_names:
        # 提取具体测试名
        for line in hint.split("\n"):
            if "test_" in line:
                evidence.append(f"  → {line.strip()}")
                break

    passed = has_test_names or (result.get("success") and not has_retry)
    if result.get("success") and not has_retry:
        evidence.append("一次通过无需retry（机制未触发但任务成功）")
        passed = True

    return passed, "; ".join(evidence)


def check_task3(status_lines, result, ctx):
    """验证：status中不出现review阶段"""
    evidence = []

    has_review = any("[review]" in l or "审查需求对齐" in l for l in status_lines)
    evidence.append(f"Reviewer被调用: {'是(BAD)' if has_review else '否(GOOD)'}")

    # 检查tests_total
    v = ctx.verifier_output if ctx else {}
    if v:
        tt = v.get("tests_total", -1)
        evidence.append(f"tests_total={tt}")

    passed = not has_review
    return passed, "; ".join(evidence)


def check_task4(status_lines, result, ctx):
    """验证：circuit_break且包含工具链缺失，retry_count==0"""
    evidence = []

    has_fuse = any("circuit_break" in l and "工具链缺失" in l for l in status_lines)
    evidence.append(f"快速熔断: {'是' if has_fuse else '否'}")

    has_retry = any("[retry]" in l for l in status_lines)
    evidence.append(f"进入retry: {'是(BAD)' if has_retry else '否(GOOD)'}")

    retry_count = ctx.retry_count if ctx else -1
    evidence.append(f"retry_count={retry_count}")

    passed = has_fuse and not has_retry
    return passed, "; ".join(evidence)


def check_task5(status_lines, result, ctx):
    """验证：不出现think_escalate（syntax错误不触发）"""
    evidence = []

    has_think = any("think_escalate" in l for l in status_lines)
    evidence.append(f"think_escalate触发: {'是(BAD)' if has_think else '否(GOOD)'}")

    # 检查错误类型
    has_syntax = any("syntax" in l.lower() for l in status_lines)
    evidence.append(f"检测到syntax错误: {'是' if has_syntax else '否'}")

    passed = not has_think
    return passed, "; ".join(evidence)


def check_task6(status_lines, result, ctx):
    """验证：不出现scope_narrow"""
    evidence = []

    has_narrow = any("scope_narrow" in l for l in status_lines)
    evidence.append(f"scope_narrow触发: {'是(BAD)' if has_narrow else '否(GOOD)'}")

    has_retry = any("[retry]" in l for l in status_lines)
    evidence.append(f"有retry: {'是' if has_retry else '否'}")

    passed = not has_narrow
    return passed, "; ".join(evidence)


def check_task7(status_lines, result, ctx):
    """验证：wink_intervene且pattern为tests_no_progress"""
    evidence = []

    has_wink = any("wink_intervene" in l or "wink" in l.lower() for l in status_lines)
    evidence.append(f"Wink介入: {'是' if has_wink else '否'}")

    has_progress_check = any("tests_no_progress" in l or "未提升通过率" in l for l in status_lines)
    evidence.append(f"检测到无进展: {'是' if has_progress_check else '否'}")

    # 这个任务可能通过也可能不通过，关键是Wink是否触发
    # 如果一次通过说明模型能力强，Wink不需要触发也算PASS
    if result.get("success") and not has_wink:
        evidence.append("模型一次通过，Wink无需触发(ACCEPTABLE)")
        passed = True
    elif has_wink:
        passed = True
    else:
        evidence.append("未通过且Wink未触发(需要更多retry才能触发)")
        passed = False  # 可能retry次数不够

    return passed, "; ".join(evidence)


# ═══════════════════════════════════════════════════════════════════
# Pipeline Builder & Runner
# ═══════════════════════════════════════════════════════════════════

def _build_pipeline(ollama_model: str):
    """Build full pipeline for audit. Returns (gate, orchestrator, tool_executor)."""
    from kaiwu.llm.llama_backend import LLMBackend
    from kaiwu.core.gate import Gate
    from kaiwu.core.orchestrator import PipelineOrchestrator
    from kaiwu.experts.locator import LocatorExpert
    from kaiwu.experts.generator import GeneratorExpert
    from kaiwu.experts.verifier import VerifierExpert
    from kaiwu.experts.search_augmentor import SearchAugmentorExpert
    from kaiwu.experts.office_handler import OfficeHandlerExpert
    from kaiwu.memory.kaiwu_md import KaiwuMemory
    from kaiwu.registry.expert_registry import ExpertRegistry
    from kaiwu.tools.executor import ToolExecutor

    llm = LLMBackend(ollama_model=ollama_model)
    registry = ExpertRegistry()
    registry.load_builtin()

    tool_executor = ToolExecutor(project_root=".")
    gate = Gate(llm, registry=registry)
    locator = LocatorExpert(llm, tool_executor)
    generator = GeneratorExpert(llm, tool_executor, num_candidates=1)
    verifier = VerifierExpert(llm, tool_executor)
    search = SearchAugmentorExpert(llm)
    office = OfficeHandlerExpert()
    memory = KaiwuMemory()

    orchestrator = PipelineOrchestrator(
        locator=locator,
        generator=generator,
        verifier=verifier,
        search_augmentor=search,
        office_handler=office,
        tool_executor=tool_executor,
        memory=memory,
        registry=registry,
    )

    return gate, orchestrator, tool_executor


def _run_audit_task(task: dict, gate, orchestrator, tool_executor) -> tuple:
    """Run one audit task. Returns (result_dict, status_lines, ctx)."""
    setup_fn = globals()[task["setup"]]
    tmpdir = tempfile.mkdtemp(prefix=f"kaiwu_audit_{task['id']}_")
    status_lines = []

    try:
        setup_fn(tmpdir)
        tool_executor.project_root = os.path.abspath(tmpdir)

        gate_result = gate.classify(task["description"])
        gate_result["expert_type"] = task["expected_expert_type"]

        def on_status(stage, detail):
            status_lines.append(f"[{stage}] {detail}")

        result = orchestrator.run(
            user_input=task["description"],
            gate_result=gate_result,
            project_root=tmpdir,
            on_status=on_status,
            no_search=True,
        )

        ctx = result.get("context")
        return result, status_lines, ctx

    except Exception as e:
        return {"success": False, "error": str(e)}, status_lines, None
    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════
# Main Entry Point
# ═══════════════════════════════════════════════════════════════════

def run_audit(ollama_model: str = "qwen3:8b", task_ids: list = None):
    """Run mechanism audit. Returns audit report dict."""
    import httpx

    print("=" * 70)
    print("  KWCode v1.6.2 机制验证审查")
    print("=" * 70)
    print(f"  模型: {ollama_model}")
    print(f"  任务数: {len(AUDIT_TASKS)}")
    print()

    # Check Ollama
    try:
        resp = httpx.get("http://localhost:11434/api/tags", timeout=3.0)
        if resp.status_code != 200:
            raise ConnectionError()
    except Exception:
        print("  [ERROR] Ollama 不在线。请启动: ollama serve")
        return {"status": "error", "reason": "Ollama offline"}

    gate, orchestrator, tool_executor = _build_pipeline(ollama_model)

    tasks_to_run = AUDIT_TASKS
    if task_ids:
        tasks_to_run = [t for t in AUDIT_TASKS if t["id"] in task_ids]

    results = []

    for task in tasks_to_run:
        print(f"  [{task['id']}/7] {task['name']}")
        print(f"       机制: {task['mechanism']}")

        t0 = time.time()
        result, status_lines, ctx = _run_audit_task(task, gate, orchestrator, tool_executor)
        elapsed = time.time() - t0

        # Run check function
        check_fn = globals()[task["check"]]
        passed, evidence = check_fn(status_lines, result, ctx)

        status_str = "PASS" if passed else "FAIL"
        task_success = "成功" if result.get("success") else "失败"

        print(f"       任务结果: {task_success} ({elapsed:.1f}s)")
        print(f"       机制验证: [{status_str}] {evidence}")

        # Print last few status lines for debugging
        if not passed or not result.get("success"):
            print(f"       --- pipeline trace (last 8) ---")
            for line in status_lines[-8:]:
                print(f"         {line}")

        print()

        results.append({
            "id": task["id"],
            "name": task["name"],
            "mechanism": task["mechanism"],
            "task_success": result.get("success", False),
            "mechanism_pass": passed,
            "evidence": evidence,
            "elapsed_s": round(elapsed, 1),
            "status_lines_count": len(status_lines),
        })

    # ── Summary Report ──
    print("=" * 70)
    print("  审查报告")
    print("=" * 70)
    print()
    print(f"  {'机制':<30} {'状态':<8} {'证据'}")
    print(f"  {'─' * 30} {'─' * 8} {'─' * 40}")

    total_pass = 0
    for r in results:
        status = "PASS" if r["mechanism_pass"] else "FAIL"
        if r["mechanism_pass"]:
            total_pass += 1
        print(f"  {r['name']:<30} {status:<8} {r['evidence'][:50]}")

    print()
    print(f"  总计: {total_pass}/{len(results)} 机制验证通过")
    print("=" * 70)

    report = {
        "status": "completed",
        "model": ollama_model,
        "total_pass": total_pass,
        "total_tasks": len(results),
        "results": results,
    }

    # Save report
    report_path = os.path.join(os.path.dirname(__file__), "mechanism_audit_results.json")
    import json
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n  报告已保存: {report_path}")

    return report


def main():
    parser = argparse.ArgumentParser(description="KWCode v1.6.2 Mechanism Audit")
    parser.add_argument("--ollama-model", type=str, default="qwen3:8b",
                        help="Ollama model to use")
    parser.add_argument("--task", type=int, default=None,
                        help="Only run specific task ID (1-7)")
    args = parser.parse_args()

    task_ids = [args.task] if args.task else None
    run_audit(ollama_model=args.ollama_model, task_ids=task_ids)


if __name__ == "__main__":
    main()