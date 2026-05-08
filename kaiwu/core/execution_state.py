"""
ExecutionStateTracker: 追踪每次修改后测试结果的变化。
避免reset重试，支持从最优中间状态继续。Git bisect式定位。
"""

from dataclasses import dataclass, field
from typing import Optional

__all__ = ["TestDelta", "ExecutionStateTracker"]


@dataclass
class TestDelta:  # noqa: pytest collection disabled via conftest or naming
    """测试状态变化记录。"""
    attempt: int = 0
    newly_passing: list = field(default_factory=list)
    newly_failing: list = field(default_factory=list)
    unchanged_failing: list = field(default_factory=list)
    gap_type: str = "unknown"


class ExecutionStateTracker:
    """
    不是reset重试，是Git bisect式定位：知道哪步引入了问题。
    代码状态的回滚完全交给Checkpoint，本类只追踪测试状态。
    """

    def __init__(self):
        self._history: list[TestDelta] = []
        self._baseline_failing: set[str] = set()

    def reset(self):
        """重置tracker状态（新任务开始时调用）。"""
        self._history = []
        self._baseline_failing = set()

    def set_baseline(self, initial_failing: list[str]):
        """任务开始前先记录基线（pre_test的结果）。"""
        self._baseline_failing = set(initial_failing)

    def record(self, attempt: int, current_failing: list[str],
               current_passing: list[str], gap_type: str):
        """每次verifier运行后记录状态变化。"""
        current_failing_set = set(current_failing)
        current_passing_set = set(current_passing)

        delta = TestDelta(
            attempt=attempt,
            newly_passing=[t for t in current_passing
                           if t in self._baseline_failing],
            newly_failing=[t for t in current_failing
                           if t not in self._baseline_failing],  # 回归！
            unchanged_failing=list(current_failing_set & self._baseline_failing),
            gap_type=gap_type,
        )
        self._history.append(delta)

    def has_regression(self) -> bool:
        """最近一次修改是否引入了回归。"""
        if not self._history:
            return False
        return len(self._history[-1].newly_failing) > 0

    def get_best_partial_state(self) -> Optional[TestDelta]:
        """
        找历史上最好的中间状态：
        通过了最多新测试 且 没有引入回归。
        可以从这里继续而不是从头开始。
        """
        valid = [d for d in self._history if not d.newly_failing]
        if not valid:
            return None
        return max(valid, key=lambda d: len(d.newly_passing))

    def get_regression_point(self) -> Optional[int]:
        """找到引入回归的那次attempt编号。"""
        for delta in self._history:
            if delta.newly_failing:
                return delta.attempt
        return None

    def get_new_failures(self) -> list[str]:
        """返回最近一次attempt新引入的失败测试。"""
        if not self._history:
            return []
        return list(self._history[-1].newly_failing)

    def get_progress_summary(self) -> dict:
        """获取当前进展摘要（供审计日志使用）。"""
        if not self._history:
            return {"total_attempts": 0, "best_passing": 0, "regressions": 0}

        best = self.get_best_partial_state()
        regressions = sum(1 for d in self._history if d.newly_failing)
        return {
            "total_attempts": len(self._history),
            "best_passing": len(best.newly_passing) if best else 0,
            "regressions": regressions,
            "baseline_failing_count": len(self._baseline_failing),
        }
