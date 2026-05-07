"""
审计日志：持久化任务执行轨迹为人类可读格式。

存储位置：
  成功：~/.kaiwu/logs/success/YYYY-MM-DD_HHMMSS_<expert_type>.json
  失败：~/.kaiwu/logs/failed/YYYY-MM-DD_HHMMSS_<expert_type>.json
不记录代码内容，只记录元数据和行为轨迹。
各目录最多保留100条，超出自动清理最旧的。

增强字段：routing_source, gap_type, transition_reason, expert_selected,
          can_handle_results, test_delta（每次retry的结构化记录）
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LOGS_BASE = Path.home() / ".kaiwu" / "logs"
LOGS_SUCCESS = LOGS_BASE / "success"
LOGS_FAILED = LOGS_BASE / "failed"
# 向后兼容：旧日志的flat目录
LOGS_LEGACY = LOGS_BASE
MAX_LOGS = 100


class AuditLogger:
    """任务执行审计日志（增强版：含MoE决策轨迹）。"""

    def __init__(self):
        self._events: list[dict] = []
        self._iterations: list[dict] = []
        self._start_time: float = 0

    def start(self):
        """任务开始时调用。"""
        self._events = []
        self._iterations = []
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

    def log_iteration(self, attempt: int, gap_type: str = "",
                      expert_selected: str = "",
                      can_handle_results: Optional[dict] = None,
                      transition_reason: str = "",
                      test_delta: Optional[dict] = None):
        """记录每轮retry迭代的结构化决策信息。"""
        self._iterations.append({
            "attempt": attempt,
            "gap_type": gap_type,
            "expert_selected": expert_selected,
            "can_handle_results": can_handle_results or {},
            "transition_reason": transition_reason,
            "test_delta": test_delta or {},
        })

    def write(self, ctx, elapsed: float, success: bool, model: str = "unknown"):
        """
        任务完成时写入日志文件。非阻塞，失败静默。
        成功写入 success/ 目录，失败写入 failed/ 目录。

        Args:
            ctx: TaskContext
            elapsed: 总耗时秒数
            success: 是否成功
            model: 模型名称
        """
        try:
            # 选择目录
            log_dir = LOGS_SUCCESS if success else LOGS_FAILED
            log_dir.mkdir(parents=True, exist_ok=True)

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

            # Gap信息
            initial_gap_type = ""
            if ctx.gap and hasattr(ctx.gap, 'gap_type'):
                initial_gap_type = ctx.gap.gap_type.value if hasattr(ctx.gap.gap_type, 'value') else str(ctx.gap.gap_type)

            record = {
                "task": ctx.user_input[:200],
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
                # MoE增强字段
                "routing_source": getattr(ctx, 'routing_source', ''),
                "initial_gap_type": initial_gap_type,
                "iterations": self._iterations,
                "events": self._events,
                # TraceCoder: 完整历史教训链
                "attempt_history": getattr(ctx, 'attempt_history', []),
            }

            log_path = log_dir / filename
            log_path.write_text(
                json.dumps(record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            # 清理超过MAX_LOGS的旧日志
            self._cleanup(log_dir)

        except Exception as e:
            logger.debug("Audit log write failed (non-blocking): %s", e)

    def _cleanup(self, directory: Path):
        """保留最近MAX_LOGS条日志，删除最旧的。"""
        try:
            logs = sorted(directory.glob("*.json"), key=lambda p: p.name)
            if len(logs) > MAX_LOGS:
                for old in logs[:len(logs) - MAX_LOGS]:
                    old.unlink()
        except Exception:
            pass


def list_logs(limit: int = 20) -> list[dict]:
    """列出最近的日志摘要（同时扫描success/failed/和旧flat目录）。"""
    all_logs = []

    for directory in [LOGS_SUCCESS, LOGS_FAILED, LOGS_LEGACY]:
        if not directory.exists():
            continue
        for path in directory.glob("*.json"):
            # 跳过子目录中的文件（避免重复扫描）
            if directory == LOGS_LEGACY and path.parent != LOGS_LEGACY:
                continue
            all_logs.append(path)

    # 按文件名排序（时间戳在文件名中）
    all_logs.sort(key=lambda p: p.name, reverse=True)

    result = []
    for i, path in enumerate(all_logs[:limit]):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            # 判断来源目录
            source = "unknown"
            if LOGS_SUCCESS in path.parents or path.parent == LOGS_SUCCESS:
                source = "success"
            elif LOGS_FAILED in path.parents or path.parent == LOGS_FAILED:
                source = "failed"
            else:
                source = "success" if data.get("success", False) else "failed"

            result.append({
                "id": i + 1,
                "file": path.name,
                "task": data.get("task", "")[:60],
                "success": data.get("success", False),
                "elapsed_s": data.get("elapsed_s", 0),
                "timestamp": data.get("timestamp", ""),
                "model": data.get("model", ""),
                "routing_source": data.get("routing_source", ""),
                "initial_gap_type": data.get("initial_gap_type", ""),
                "source": source,
            })
        except Exception:
            continue

    return result


def show_log(log_id: int) -> Optional[dict]:
    """获取指定ID的日志详情。"""
    all_logs = []
    for directory in [LOGS_SUCCESS, LOGS_FAILED, LOGS_LEGACY]:
        if not directory.exists():
            continue
        for path in directory.glob("*.json"):
            if directory == LOGS_LEGACY and path.parent != LOGS_LEGACY:
                continue
            all_logs.append(path)

    all_logs.sort(key=lambda p: p.name, reverse=True)

    idx = log_id - 1
    if idx < 0 or idx >= len(all_logs):
        return None

    try:
        return json.loads(all_logs[idx].read_text(encoding="utf-8"))
    except Exception:
        return None


def clear_logs() -> int:
    """清除所有日志，返回删除数量。"""
    count = 0
    for directory in [LOGS_SUCCESS, LOGS_FAILED, LOGS_LEGACY]:
        if not directory.exists():
            continue
        for path in directory.glob("*.json"):
            try:
                path.unlink()
                count += 1
            except Exception:
                pass
    return count
