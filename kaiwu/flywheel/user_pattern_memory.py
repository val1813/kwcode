"""
用户错误模式记忆。
跨项目统计用户高频错误类型，用于任务开始时主动提示。
存在用户 home 目录 ~/.kaiwu/user_patterns.json，跨项目有效。
不收集任何代码内容。
"""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

USER_PATTERNS_FILE = Path.home() / ".kaiwu" / "user_patterns.json"
TOP_ERROR_THRESHOLD = 5
TOP_N_ERRORS = 3


class UserPatternMemory:
    """
    跨项目的用户错误模式记忆。
    记录用户经常犯的错误类型，在任务开始时注入针对性提示。
    """

    def __init__(self):
        self._data: dict = self._load()

    def _load(self) -> dict:
        try:
            if USER_PATTERNS_FILE.exists():
                with open(USER_PATTERNS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.debug("user_patterns load failed: %s", e)
        return {
            "error_frequency": {},
            "top_errors": [],
            "total_tasks": 0,
            "success_rate": 0.0,
            "last_updated": "",
        }

    def _save(self):
        try:
            USER_PATTERNS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(USER_PATTERNS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug("user_patterns save failed (non-blocking): %s", e)

    def record_task(self, error_types_encountered: list[str], success: bool):
        """
        记录一次任务的错误类型统计。

        Args:
            error_types_encountered: 本次任务遇到的错误类型列表
            success: 任务是否最终成功
        """
        self._data["total_tasks"] += 1

        n = self._data["total_tasks"]
        old_rate = self._data["success_rate"]
        self._data["success_rate"] = old_rate + (
            (1.0 if success else 0.0) - old_rate
        ) / n

        for error_type in error_types_encountered:
            if error_type and error_type != "unknown":
                freq = self._data["error_frequency"]
                freq[error_type] = freq.get(error_type, 0) + 1

        self._data["top_errors"] = [
            error
            for error, count in sorted(
                self._data["error_frequency"].items(),
                key=lambda x: -x[1],
            )[:TOP_N_ERRORS]
            if count >= TOP_ERROR_THRESHOLD
        ]

        self._data["last_updated"] = datetime.now().isoformat()
        self._save()

    def get_warning_hint(self) -> str:
        """
        根据用户高频错误模式生成提示，注入 Gate 的 memory_context。
        只有积累足够数据后才生成提示，避免误报。
        """
        if self._data["total_tasks"] < 20:
            return ""

        top = self._data.get("top_errors", [])
        if not top:
            return ""

        ERROR_HINTS = {
            "assertion": "注意：你经常遇到断言错误，建议先确认测试的期望值",
            "import": "注意：你经常遇到缺少依赖，建议检查 requirements.txt 是否完整",
            "runtime": "注意：你经常遇到运行时错误，建议在修改前先阅读完整的调用链",
            "syntax": "注意：你经常遇到语法错误，建议修改后先用 linter 检查",
            "patch_apply": "注意：你经常遇到 patch 未命中，建议先确认文件没有被其他工具修改",
        }

        hints = [ERROR_HINTS[e] for e in top if e in ERROR_HINTS]
        if not hints:
            return ""

        return "\n".join(hints)

    def get_summary(self) -> dict:
        """返回用户模式摘要，用于 /stats 命令展示。"""
        return {
            "total_tasks": self._data["total_tasks"],
            "success_rate": f"{self._data['success_rate']:.1%}",
            "top_errors": self._data["top_errors"],
            "error_frequency": self._data["error_frequency"],
        }
