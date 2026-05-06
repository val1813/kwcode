"""
CognitiveGate: 认知门控熔断，检测边际收益递减。

检测 Generator 输出是否在边际收益递减：
- patch 行数持续递减 → 模型已无有效修复方向 → 停止重试
- 替代固定计数熔断，更精确地判断何时该停

理论来源：
- CC Diminishing Returns Detection（CC Source Analysis 2026）
- SpecEyes 认知门控（arXiv:2603.23483）
- Speculative Actions（arXiv:2510.04371）
"""

import logging

logger = logging.getLogger(__name__)


class CognitiveGate:
    """
    认知门控：基于 patch 行数变化趋势判断是否应停止重试。
    比 token 数更精确——patch 行数直接反映修复意图变化。
    """

    def __init__(self, window: int = 3, threshold: float = 0.3):
        """
        Args:
            window: 观察窗口大小（需要多少次记录才开始判断）
            threshold: 递减阈值（最后一次 <= 第一次 * threshold 时触发）
        """
        self.window = window
        self.threshold = threshold
        self._patch_lines: list[int] = []

    def record(self, patches: list[dict]) -> None:
        """记录一次 Generator 输出的 patch 总行数。"""
        total = sum(len(p.get("modified", "").splitlines()) for p in patches)
        self._patch_lines.append(total)

    def should_stop(self) -> tuple[bool, str]:
        """
        判断是否应停止重试。
        Returns:
            (should_stop, reason) — reason 为空字符串表示不停止
        """
        if len(self._patch_lines) < self.window:
            return False, ""

        recent = self._patch_lines[-self.window:]

        # 持续递减且降幅超过阈值
        if all(recent[i] > recent[i + 1] for i in range(len(recent) - 1)):
            if recent[-1] <= recent[0] * self.threshold:
                return True, f"patch行数持续递减 {recent}，边际收益递减"

        # 最后一次极小（模型已无从下手）
        if recent[-1] <= 3 and len(self._patch_lines) >= 2:
            return True, f"patch行数降至 {recent[-1]} 行，停止重试"

        # 连续输出相同行数（原地打转）
        if len(set(recent)) == 1 and len(self._patch_lines) >= self.window:
            return True, f"patch行数连续 {self.window} 次相同({recent[-1]}行)，原地打转"

        return False, ""

    def reset(self):
        """重置状态（新任务开始时调用）。"""
        self._patch_lines.clear()

    @property
    def history(self) -> list[int]:
        """返回 patch 行数历史记录。"""
        return list(self._patch_lines)
