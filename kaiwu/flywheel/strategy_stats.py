"""
错误策略有效性统计。
记录每种 error_type 在每种重试序列下的成功率。
数据存本地 ~/.kwcode/strategy_stats.json，不上传。
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

STATS_FILE = Path.home() / ".kwcode" / "strategy_stats.json"


class StrategyStats:
    """
    统计错误策略的实际有效性。
    每次任务结束后更新，Orchestrator 启动时加载用于调整策略优先级。
    """

    def __init__(self):
        self._stats: dict = self._load()

    def _load(self) -> dict:
        try:
            if STATS_FILE.exists():
                with open(STATS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.debug("strategy_stats load failed: %s", e)
        return {}

    def _save(self):
        try:
            STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(STATS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._stats, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug("strategy_stats save failed (non-blocking): %s", e)

    def record(
        self,
        error_type: str,
        sequence: list[str],
        success: bool,
        retries_used: int,
    ):
        """
        记录一次策略使用结果。只记录元数据，不记录任何代码内容。

        Args:
            error_type: 错误类型（syntax/assertion/runtime等）
            sequence: 使用的专家序列（如 ["generator", "verifier"]）
            success: 是否最终成功
            retries_used: 使用了几次重试
        """
        seq_key = "_".join(sequence)

        if error_type not in self._stats:
            self._stats[error_type] = {}

        if seq_key not in self._stats[error_type]:
            self._stats[error_type][seq_key] = {
                "attempts": 0,
                "successes": 0,
                "success_rate": 0.0,
                "avg_retries_to_success": 0.0,
                "_total_retries": 0,
            }

        entry = self._stats[error_type][seq_key]
        entry["attempts"] += 1
        if success:
            entry["successes"] += 1
            entry["_total_retries"] += retries_used

        entry["success_rate"] = entry["successes"] / entry["attempts"]

        if entry["successes"] > 0:
            entry["avg_retries_to_success"] = (
                entry["_total_retries"] / entry["successes"]
            )

        self._save()

    def get_best_sequence(
        self,
        error_type: str,
        default_sequence: list[str],
        min_attempts: int = 10,
    ) -> list[str]:
        """
        返回该错误类型下成功率最高的策略序列。
        数据不足时（attempts < min_attempts）返回默认序列。
        """
        if error_type not in self._stats:
            return default_sequence

        candidates = {
            seq_key: data
            for seq_key, data in self._stats[error_type].items()
            if data["attempts"] >= min_attempts
        }

        if not candidates:
            return default_sequence

        best_key = max(
            candidates,
            key=lambda k: (
                candidates[k]["success_rate"],
                -candidates[k]["avg_retries_to_success"],
            ),
        )

        return best_key.split("_")

    def get_summary(self) -> dict:
        """返回可读的统计摘要，用于 /stats 命令展示。"""
        summary = {}
        for error_type, sequences in self._stats.items():
            best = max(
                sequences.items(),
                key=lambda x: x[1]["success_rate"],
                default=(None, None),
            )
            if best[0]:
                summary[error_type] = {
                    "best_sequence": best[0],
                    "best_success_rate": f"{best[1]['success_rate']:.1%}",
                    "total_attempts": sum(
                        v["attempts"] for v in sequences.values()
                    ),
                }
        return summary
