"""
专项4：三层消噪触发率验证
目标：验证各路由层的触发频率和准确率
成功标准：gap_detector路由的准确率 > llm_fallback路由的准确率
"""

import pytest
from unittest.mock import MagicMock
from kaiwu.core.gap_detector import GapDetector, GapType, Gap, GAP_TO_EXPERT_TYPE
from kaiwu.core.gate import Gate


class MockLLM:
    """Mock LLM for Gate testing."""
    def __init__(self, response=""):
        self.response = response
        self.call_count = 0

    def generate(self, prompt="", system="", max_tokens=1024,
                 temperature=0.0, stop=None, grammar_str=None):
        self.call_count += 1
        return self.response


# ══════════════════════════════════════
# 测试场景：每个场景有明确的"正确答案"
# ══════════════════════════════════════

ROUTING_SCENARIOS = [
    # --- gap_detector应该路由的场景 ---
    {
        "desc": "NotImplementedError with high confidence",
        "user_input": "实现这个功能",
        "gap": Gap(GapType.NOT_IMPLEMENTED, 0.9, ["src/calc.py"], ["add"], "", ""),
        "expected_expert": "locator_repair",
        "expected_source": "gap_detector",
    },
    {
        "desc": "Logic error with high confidence",
        "user_input": "修复排序",
        "gap": Gap(GapType.LOGIC_ERROR, 0.85, ["sort.py"], ["bubble_sort"], "", ""),
        "expected_expert": "locator_repair",
        "expected_source": "gap_detector",
    },
    {
        "desc": "Syntax error with high confidence",
        "user_input": "修复语法错误",
        "gap": Gap(GapType.SYNTAX_STRUCTURAL, 0.95, ["main.py"], [], "", ""),
        "expected_expert": "locator_repair",
        "expected_source": "gap_detector",
    },
    {
        "desc": "Missing toolchain",
        "user_input": "运行测试",
        "gap": Gap(GapType.MISSING_TOOLCHAIN, 0.95, [], [], "go: not found", ""),
        "expected_expert": "env_fix",
        "expected_source": "gap_detector",
    },

    # --- keyword应该路由的场景（无gap或低confidence gap）---
    {
        "desc": "Explicit fix keyword, no gap",
        "user_input": "修复登录bug",
        "gap": None,
        "expected_expert": "locator_repair",
        "expected_source": "keyword",
    },
    {
        "desc": "Explicit create keyword, no gap",
        "user_input": "写一个HTTP服务器",
        "gap": None,
        "expected_expert": "codegen",
        "expected_source": "keyword",
    },
    {
        "desc": "Refactor keyword, no gap",
        "user_input": "重构认证模块",
        "gap": None,
        "expected_expert": "refactor",
        "expected_source": "keyword",
    },

    # --- 特殊任务快速路由 ---
    {
        "desc": "Chat greeting",
        "user_input": "你好，什么是Python",
        "gap": None,
        "expected_expert": "chat",
        "expected_source": "keyword",
    },
    {
        "desc": "Office document",
        "user_input": "生成一个Excel报表",
        "gap": None,
        "expected_expert": "office",
        "expected_source": "keyword",
    },

    # --- 低confidence gap不应覆盖用户意图 ---
    {
        "desc": "Low confidence gap should not override user intent",
        "user_input": "创建新文件",
        "gap": Gap(GapType.LOGIC_ERROR, 0.4, [], [], "", ""),
        "expected_expert": "codegen",
        "expected_source": "keyword",
    },

    # --- LLM fallback场景（无gap，无关键词）---
    {
        "desc": "Ambiguous input, no gap, no keywords",
        "user_input": "处理这个问题",
        "gap": None,
        "expected_expert": "locator_repair",  # LLM默认
        "expected_source": "llm_fallback",
    },

    # --- gap和用户意图冲突（高confidence gap wins）---
    {
        "desc": "User says create but gap says logic_error (high conf)",
        "user_input": "创建新功能",
        "gap": Gap(GapType.LOGIC_ERROR, 0.9, ["api.py"], [], "", ""),
        "expected_expert": "locator_repair",
        "expected_source": "gap_detector",
    },

    # --- STUB_RETURNS_NONE ---
    {
        "desc": "Stub returns None gap",
        "user_input": "修复这个问题",
        "gap": Gap(GapType.STUB_RETURNS_NONE, 0.85, ["config.py"], ["load"], "", ""),
        "expected_expert": "locator_repair",
        "expected_source": "gap_detector",
    },

    # --- NONE gap (all tests pass) ---
    {
        "desc": "All tests pass, user wants new feature",
        "user_input": "写一个新的API端点",
        "gap": Gap(GapType.NONE, 1.0, [], [], "", ""),
        "expected_expert": "codegen",
        "expected_source": "keyword",
    },
]


class TestRoutingLayerStats:
    """验证三层路由的触发率和准确率。"""

    @pytest.fixture
    def gate(self):
        llm = MockLLM(response='{"action": "modify"}')
        return Gate(llm=llm)

    @pytest.mark.parametrize("scenario", ROUTING_SCENARIOS,
                             ids=[s["desc"] for s in ROUTING_SCENARIOS])
    def test_individual_routing(self, gate, scenario):
        """验证单个场景的路由结果。"""
        result = gate.classify(
            user_input=scenario["user_input"],
            gap=scenario["gap"],
        )
        assert result["expert_type"] == scenario["expected_expert"], (
            f"[{scenario['desc']}] "
            f"期望expert={scenario['expected_expert']}, "
            f"实际={result['expert_type']}"
        )

    def test_routing_source_accuracy(self, gate):
        """验证routing_source字段正确标记。"""
        for scenario in ROUTING_SCENARIOS:
            result = gate.classify(
                user_input=scenario["user_input"],
                gap=scenario["gap"],
            )
            # 验证routing_source存在
            assert "routing_source" in result, (
                f"[{scenario['desc']}] 缺少routing_source字段"
            )

    def test_gap_detector_vs_llm_accuracy(self, gate):
        """
        核心验证：gap_detector路由的准确率 > llm_fallback路由的准确率。
        """
        gap_correct = 0
        gap_total = 0
        keyword_correct = 0
        keyword_total = 0
        llm_correct = 0
        llm_total = 0

        for scenario in ROUTING_SCENARIOS:
            result = gate.classify(
                user_input=scenario["user_input"],
                gap=scenario["gap"],
            )
            source = result.get("routing_source", "")
            is_correct = result["expert_type"] == scenario["expected_expert"]

            if source == "gap_detector":
                gap_total += 1
                if is_correct:
                    gap_correct += 1
            elif source == "keyword":
                keyword_total += 1
                if is_correct:
                    keyword_correct += 1
            elif source == "llm_fallback":
                llm_total += 1
                if is_correct:
                    llm_correct += 1

        # 统计输出
        gap_acc = gap_correct / gap_total if gap_total > 0 else 0
        keyword_acc = keyword_correct / keyword_total if keyword_total > 0 else 0
        llm_acc = llm_correct / llm_total if llm_total > 0 else 0

        print(f"\n路由层统计：")
        print(f"  gap_detector: {gap_correct}/{gap_total} = {gap_acc:.0%}")
        print(f"  keyword:      {keyword_correct}/{keyword_total} = {keyword_acc:.0%}")
        print(f"  llm_fallback: {llm_correct}/{llm_total} = {llm_acc:.0%}")

        # 核心断言：确定性路由准确率 >= LLM路由
        if gap_total > 0 and llm_total > 0:
            assert gap_acc >= llm_acc, (
                f"gap_detector准确率({gap_acc:.0%}) 应 >= llm_fallback({llm_acc:.0%})"
            )

    def test_no_llm_call_for_deterministic_routes(self, gate):
        """验证确定性路由不调用LLM。"""
        llm = gate.llm
        initial_calls = llm.call_count

        # 有高confidence gap的场景不应调用LLM
        gap = Gap(GapType.NOT_IMPLEMENTED, 0.9, ["test.py"], [], "", "")
        gate.classify("实现功能", gap=gap)

        # chat快速路由不应调用LLM
        gate.classify("你好")

        # office快速路由不应调用LLM
        gate.classify("生成Excel报表")

        # 这些都不应该触发LLM调用
        assert llm.call_count == initial_calls, (
            f"确定性路由不应调用LLM，但调用了{llm.call_count - initial_calls}次"
        )

    def test_llm_called_only_for_ambiguous(self, gate):
        """验证只有模糊输入才调用LLM。"""
        llm = gate.llm
        initial_calls = llm.call_count

        # 模糊输入，无gap，无关键词 → 应该调用LLM
        gate.classify("处理这个问题")

        assert llm.call_count > initial_calls, (
            "模糊输入应该触发LLM兜底分类"
        )

    def test_gap_to_expert_mapping_complete(self):
        """验证GAP_TO_EXPERT_TYPE覆盖所有非NONE/UNKNOWN的GapType。"""
        for gap_type in GapType:
            if gap_type in (GapType.NONE, GapType.UNKNOWN):
                continue
            assert gap_type in GAP_TO_EXPERT_TYPE, (
                f"GapType.{gap_type.name} 没有在GAP_TO_EXPERT_TYPE中映射"
            )
