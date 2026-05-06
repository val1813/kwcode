"""
ToolGateway: Tool/专家分层，权限检查 + 事件emit + 文件读缓存 + 脏标记。

正确分层：
  专家层（只做生成，输出 patch 结构）
    ↓
  ToolGateway（权限检查 + emit 事件 + 文件读缓存 + 脏标记）
    ↓
  executor.py（read_file / write_file / run_bash / apply_patch）

理论来源：
- CC 工具沙箱隔离（arXiv:2604.14228）
- deny-first 权限模型（CC Source Analysis 2026）
"""

import logging
from typing import Optional

from kaiwu.core.event_bus import EventBus
from kaiwu.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)

# 每个专家允许调用的工具白名单
EXPERT_PERMISSIONS = {
    "locator":   ["read_file", "list_dir"],
    "generator": ["read_file"],            # 只读，不写文件
    "verifier":  ["apply_patch", "write_file", "run_bash", "read_file"],
    "debugger":  ["read_file", "run_bash"],
    "reviewer":  ["read_file"],
    "office":    ["write_file", "read_file"],
    "vision":    ["read_file"],
    "chat":      ["read_file", "run_bash", "list_dir"],
    "search":    [],
}


class ToolGateway:
    """
    工具网关：专家通过此层访问工具，实现权限隔离和缓存。
    - 权限检查：每个专家只能调用白名单内的工具
    - 文件缓存：同一文件不重复读取（除非被标记为脏）
    - 事件发射：所有工具调用都通过 EventBus 可观测
    """

    def __init__(self, executor: ToolExecutor, bus: Optional[EventBus] = None):
        self.executor = executor
        self.bus = bus or EventBus()
        self._expert = "unknown"
        self._cache: dict[str, str] = {}
        self._dirty: set[str] = set()

    def set_expert(self, name: str):
        """设置当前专家身份（用于权限检查）。"""
        self._expert = name

    def read_file(self, path: str) -> str:
        """读取文件，带缓存。脏文件自动刷新缓存。"""
        self._check("read_file")
        if path in self._dirty:
            self._cache.pop(path, None)
            self._dirty.discard(path)
        if path in self._cache:
            return self._cache[path]
        self.bus.emit("reading_file", {"path": path, "expert": self._expert})
        content = self.executor.read_file(path)
        if not content.startswith("[ERROR]"):
            self._cache[path] = content
        return content

    def write_file(self, path: str, content: str) -> bool:
        """写入文件，标记为脏。"""
        self._check("write_file")
        self.bus.emit("writing_file", {"path": path, "expert": self._expert})
        result = self.executor.write_file(path, content)
        if result:
            self._dirty.add(path)
            self._cache.pop(path, None)
            self.bus.emit("file_written", {"path": path})
        return result

    def apply_patch(self, path: str, original: str, modified: str) -> bool:
        """应用patch，标记文件为脏。"""
        self._check("apply_patch")
        self.bus.emit("applying_patch", {"path": path, "expert": self._expert})
        result = self.executor.apply_patch(path, original, modified)
        if result:
            self._dirty.add(path)
            self._cache.pop(path, None)
        self.bus.emit("patch_result", {"path": path, "success": result})
        return result

    def run_bash(self, cmd: str, cwd: Optional[str] = None, timeout: int = 60) -> str:
        """执行shell命令。"""
        self._check("run_bash")
        self.bus.emit("running_cmd", {"cmd": cmd[:80], "expert": self._expert})
        return self.executor.run_bash(cmd, cwd, timeout)

    def list_dir(self, path: str = ".") -> list:
        """列出目录内容。"""
        self._check("list_dir")
        return self.executor.list_dir(path)

    def _check(self, tool: str):
        """权限检查：当前专家是否有权调用此工具。"""
        allowed = EXPERT_PERMISSIONS.get(self._expert, [])
        if tool not in allowed:
            msg = f"[{self._expert}] 无权调用 {tool}，允许：{allowed}"
            logger.warning("[gateway] %s", msg)
            raise PermissionError(msg)

    def reset_session(self):
        """重置缓存和脏标记（新任务开始时调用）。"""
        self._cache.clear()
        self._dirty.clear()

    def invalidate(self, path: str):
        """手动标记文件为脏（外部修改时调用）。"""
        self._dirty.add(path)
        self._cache.pop(path, None)

    @property
    def cache_size(self) -> int:
        """当前缓存的文件数。"""
        return len(self._cache)
