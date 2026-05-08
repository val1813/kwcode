"""
专项3：ExecutionStateTracker价值验证
目标：量化tracker相比reset重试的提升
成功标准：tracker能正确检测回归、找到最优中间状态
"""

import pytest
from kaiwu.core.execution_state import ExecutionStateTracker, TestDelta


class TestExecutionTrackerValue:
    """验证ExecutionStateTracker的核心价值：回归检测+最优中间状态。"""

    def test_scenario_1_progressive_fix(self):
        """场景1：逐步修复，每次通过更多测试（无回归）。"""
        tracker = ExecutionStateTracker()
        tracker.set_baseline(["test_a", "test_b", "test_c"])

        # 第1次修改：通过了test_a
        tracker.record(0, ["test_b", "test_c"], ["test_a"], "logic_error")
        assert not tracker.has_regression()
        best = tracker.get_best_partial_state()
        assert best is not None
        assert "test_a" in best.newly_passing

        # 第2次修改：通过了test_a和test_b
        tracker.record(1, ["test_c"], ["test_a", "test_b"], "logic_error")
        assert not tracker.has_regression()
        best = tracker.get_best_partial_state()
        assert len(best.newly_passing) == 2

        # 第3次修改：全部通过
        tracker.record(2, [], ["test_a", "test_b", "test_c"], "none")
        assert not tracker.has_regression()
        best = tracker.get_best_partial_state()
        assert len(best.newly_passing) == 3

    def test_scenario_2_regression_detected(self):
        """场景2：第2次修改引入回归（新的测试开始失败）。"""
        tracker = ExecutionStateTracker()
        tracker.set_baseline(["test_a", "test_b"])

        # 第1次修改：通过了test_a
        tracker.record(0, ["test_b"], ["test_a"], "logic_error")
        assert not tracker.has_regression()

        # 第2次修改：test_a又失败了，还引入了test_c失败（回归！）
        tracker.record(1, ["test_b", "test_c"], ["test_a"], "logic_error")
        assert tracker.has_regression()
        assert tracker.get_regression_point() == 1

        # 最优中间状态应该是第1次（无回归）
        best = tracker.get_best_partial_state()
        assert best is not None
        assert best.attempt == 0
        assert "test_a" in best.newly_passing

    def test_latest_new_failures_feed_retry_hint(self):
        """最近一次新增失败应能被orchestrator用于回归重试提示。"""
        tracker = ExecutionStateTracker()
        tracker.set_baseline(["test_a"])

        tracker.record(0, ["test_a"], [], "logic_error")
        assert tracker.get_new_failures() == []

        tracker.record(1, ["test_a", "test_c", "test_d"], [], "logic_error")
        assert tracker.get_new_failures() == ["test_c", "test_d"]

    def test_scenario_3_immediate_regression(self):
        """场景3：第一次修改就引入回归。"""
        tracker = ExecutionStateTracker()
        tracker.set_baseline(["test_a"])

        # 第1次修改：test_a还是失败，但引入了test_b失败（回归）
        tracker.record(0, ["test_a", "test_b"], [], "logic_error")
        assert tracker.has_regression()
        assert tracker.get_regression_point() == 0

        # 没有有效的中间状态
        best = tracker.get_best_partial_state()
        assert best is None

    def test_scenario_4_oscillating_results(self):
        """场景4：结果振荡（修一个坏一个）。"""
        tracker = ExecutionStateTracker()
        tracker.set_baseline(["test_a", "test_b", "test_c"])

        # 第1次：修好test_a，但引入test_d失败
        tracker.record(0, ["test_b", "test_c", "test_d"], ["test_a"], "logic_error")
        assert tracker.has_regression()  # test_d是新失败

        # 第2次：修好test_b，没有新回归
        tracker.record(1, ["test_c"], ["test_a", "test_b"], "logic_error")
        assert not tracker.has_regression()

        # 最优中间状态：第2次（2个newly_passing，无回归）
        best = tracker.get_best_partial_state()
        assert best is not None
        assert best.attempt == 1
        assert len(best.newly_passing) == 2

    def test_scenario_5_no_progress(self):
        """场景5：多次尝试都没有进展（同样的测试一直失败）。"""
        tracker = ExecutionStateTracker()
        tracker.set_baseline(["test_a", "test_b"])

        # 3次尝试，结果完全一样
        tracker.record(0, ["test_a", "test_b"], [], "logic_error")
        tracker.record(1, ["test_a", "test_b"], [], "logic_error")
        tracker.record(2, ["test_a", "test_b"], [], "logic_error")

        assert not tracker.has_regression()
        # 没有进展，best_partial_state应该是None（没有newly_passing）
        best = tracker.get_best_partial_state()
        # 所有delta的newly_passing都是空的
        assert best is None or len(best.newly_passing) == 0

    def test_reset_clears_state(self):
        """验证reset()清除所有状态。"""
        tracker = ExecutionStateTracker()
        tracker.set_baseline(["test_a"])
        tracker.record(0, [], ["test_a"], "none")

        tracker.reset()
        assert not tracker.has_regression()
        assert tracker.get_best_partial_state() is None
        assert tracker.get_regression_point() is None

    def test_progress_summary(self):
        """验证get_progress_summary()输出。"""
        tracker = ExecutionStateTracker()
        tracker.set_baseline(["test_a", "test_b", "test_c"])

        tracker.record(0, ["test_b", "test_c"], ["test_a"], "logic_error")
        tracker.record(1, ["test_c", "test_d"], ["test_a", "test_b"], "logic_error")

        summary = tracker.get_progress_summary()
        assert summary["total_attempts"] == 2
        assert summary["baseline_failing_count"] == 3
        assert summary["regressions"] == 1  # test_d是回归

    def test_tracker_vs_reset_comparison(self):
        """
        对照验证：tracker提供的信息 vs 纯reset。
        tracker知道"第1次修改是最好的"，reset不知道。
        """
        tracker = ExecutionStateTracker()
        tracker.set_baseline(["test_a", "test_b", "test_c"])

        # 第1次：修好2个
        tracker.record(0, ["test_c"], ["test_a", "test_b"], "logic_error")
        # 第2次：引入回归（修坏了）
        tracker.record(1, ["test_c", "test_x"], ["test_a", "test_b"], "logic_error")

        # tracker知道：
        # 1. 第2次有回归（test_x是新失败）
        assert tracker.has_regression()
        # 2. 最优状态是第1次（2个newly_passing，无回归）
        best = tracker.get_best_partial_state()
        assert best.attempt == 0
        assert len(best.newly_passing) == 2
        # 3. 回归点是第2次
        assert tracker.get_regression_point() == 1

        # 纯reset不知道这些信息，只能从头开始
        # tracker的价值：知道应该回到第1次的状态继续
