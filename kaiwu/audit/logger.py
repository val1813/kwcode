"""
审计日志：持久化任务执行轨迹为人类可读格式。

存储位置：.kaiwu/logs/YYYY-MM-DD_HHMMSS_<expert_type>.json
不记录代码内容，只记录元数据和行为轨迹。
最多保留100条，超出自动清理最旧的。
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LOGS_DIR = Path.home() / ".kaiwu" / "logs"
MAX_LOGS = 100


class AuditLogger:
    """任务执行审计日志。"""

    def __init__(self):
        self._events: list[dict] = []
        self._start_time: float = 0

    def start(self):
        """任务开始时调用。"""
        self._events = []
        self._start_time = time.time()

    def log(self, stage: str, detail: str):
        """记录一个执行事件。"""
        elapsed = time.time() - self._start_time if self._start_time else 0
        self._events.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "elapsed_s": round(elapsed, 1),
            "stage": stage,
            "detail": detail,
        })

    def write(self, ctx, elapsed: float, success: bool, model: str = "unknown"):
        """
        任务完成时写入日志文件。非阻塞，失败静默。

        Args:
            ctx: TaskContext
            elapsed: 总耗时秒数
            success: 是否成功
            model: 模型名称
        """
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)

            gate = ctx.gate_result or {}
            expert_type = gate.get("expert_type", "unknown")
            difficulty = gate.get("difficulty", "?")
            ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            filename = f"{ts}_{expert_type}.json"

            # 提取修改的文件列表（不含内容）
            files_modified = []
            patches = []
            if ctx.generator_output:
                patches = ctx.generator_output.get("patches", [])
                files_modified = [p.get("file", "") for p in patches]

            # 计算改动行数
            lines_added = 0
            lines_removed = 0
            for p in patches:
                orig_lines = len(p.get("original", "").split("\n")) if p.get("original") else 0
                mod_lines = len(p.get("modified", "").split("\n")) if p.get("modified") else 0
                lines_added += max(0, mod_lines - orig_lines)
                lines_removed += max(0, orig_lines - mod_lines)

            # 测试结果
            tests_passed = 0
            tests_total = 0
            if ctx.verifier_output:
                tests_passed = ctx.verifier_output.get("tests_passed", 0)
                tests_total = ctx.verifier_output.get("tests_total", 0)

            record = {
                "task": ctx.user_input[:200],  # 保留任务描述（用户自己的输入）
                "timestamp": datetime.now().isoformat(),
                "model": model,
                "expert_type": expert_type,
                "difficulty": difficulty,
                "elapsed_s": round(elapsed, 1),
                "success": success,
                "retry_count": ctx.retry_count,
                "files_modified": files_modified,
                "lines_added": lines_added,
                "lines_removed": lines_removed,
                "tests_passed": tests_passed,
                "tests_total": tests_total,
                "search_triggered": ctx.search_triggered,
                "events": self._events,
            }

            log_path = LOGS_DIR / filename
            log_path.write_text(
                json.dumps(record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            # 清理超过MAX_LOGS的旧日志
            self._cleanup()

        except Exception as e:
            logger.debug("Audit log write failed (non-blocking): %s", e)

    def _cleanup(self):
        """保留最近MAX_LOGS条日志，删除最旧的。"""
        try:
            logs = sorted(LOGS_DIR.glob("*.json"), key=lambda p: p.name)
            if len(logs) > MAX_LOGS:
                for old in logs[:len(logs) - MAX_LOGS]:
                    old.unlink()
        except Exception:
            pass


def list_logs(limit: int = 20) -> list[dict]:
    """列出最近的日志摘要。"""
    if not LOGS_DIR.exists():
        return []

    logs = sorted(LOGS_DIR.glob("*.json"), key=lambda p: p.name, reverse=True)
    result = []
    for i, path in enumerate(logs[:limit]):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            result.append({
                "id": len(logs) - i,
                "file": path.name,
                "task": data.get("task", "")[:60],
                "success": data.get("success", False),
                "elapsed_s": data.get("elapsed_s", 0),
                "timestamp": data.get("timestamp", ""),
                "model": data.get("model", ""),
            })
        except Exception:
            continue

    return result


def show_log(log_id: int) -> Optional[dict]:
    """获取指定ID的日志详情。"""
    if not LOGS_DIR.exists():
        return None

    logs = sorted(LOGS_DIR.glob("*.json"), key=lambda p: p.name)
    idx = log_id - 1
    if idx < 0 or idx >= len(logs):
        return None

    try:
        return json.loads(logs[idx].read_text(encoding="utf-8"))
    except Exception:
        return None


def clear_logs() -> int:
    """清除所有日志，返回删除数量。"""
    if not LOGS_DIR.exists():
        return 0
    count = 0
    for path in LOGS_DIR.glob("*.json"):
        try:
            path.unlink()
            count += 1
        except Exception:
            pass
    return count
