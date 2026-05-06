"""
EventBus: 统一事件总线（Event Sourcing 模式）。
append-only 日志支持 replay/调试，替代分散的 on_status 回调。

理论来源：
- Event Sourcing（Martin Fowler）
- CC 27 个 hook 事件（arXiv:2604.14228）
- Codified Context append-only 日志（arXiv:2602.20478）
"""

from collections import defaultdict
from typing import Callable
import time
import logging

logger = logging.getLogger(__name__)

__all__ = ["EventBus"]


class EventBus:
    """
    统一事件总线。
    - on(event, handler) 注册监听
    - emit(event, payload) 发射事件
    - replay() 返回完整事件日志
    """

    _instance: "EventBus | None" = None

    def __init__(self):
        self._handlers: dict[str, list[Callable]] = defaultdict(list)
        self._wildcard: list[Callable] = []
        self._log: list[dict] = []
        # 首个实例自动成为单例（Orchestrator 创建的那个）
        if EventBus._instance is None:
            EventBus._instance = self

    @classmethod
    def get_instance(cls) -> "EventBus | None":
        """返回全局单例（如果已创建）。专家层用此获取 bus。"""
        return cls._instance

    def on(self, event: str, handler: Callable):
        """注册事件处理器。event="*" 监听所有事件。"""
        if event == "*":
            self._wildcard.append(handler)
        else:
            self._handlers[event].append(handler)

    def off(self, event: str, handler: Callable):
        """移除事件处理器。"""
        if event == "*":
            try:
                self._wildcard.remove(handler)
            except ValueError:
                pass
        else:
            try:
                self._handlers[event].remove(handler)
            except ValueError:
                pass

    def emit(self, event: str, payload: dict | None = None):
        """发射事件，通知所有监听器，同时记录到日志。"""
        payload = payload or {}
        entry = {"t": time.time(), "event": event, **payload}
        self._log.append(entry)
        for h in self._handlers.get(event, []) + self._wildcard:
            try:
                h(event, payload)
            except Exception as e:
                logger.debug("EventBus handler error [%s]: %s", event, e)

    def replay(self) -> list[dict]:
        """返回完整事件日志副本。"""
        return list(self._log)

    def clear_log(self):
        """清空事件日志（不影响已注册的handler）。"""
        self._log.clear()

    def handler_count(self) -> int:
        """返回已注册handler总数。"""
        total = len(self._wildcard)
        for handlers in self._handlers.values():
            total += len(handlers)
        return total
