"""
匿名遥测客户端。
默认关闭，用户 opt-in 后只上传行为元数据：
  - error_type（枚举值）
  - retry_count（数字）
  - success（bool）
  - model（模型名称）
绝不上传代码内容、文件路径、任务描述、用户身份信息。
"""

import hashlib
import hmac
import json
import logging
import threading
from pathlib import Path

import httpx
import yaml

logger = logging.getLogger(__name__)

TELEMETRY_URL = "https://llmbbs.com/api/v1/event"
CONFIG_PATH = Path.home() / ".kwcode" / "config.yaml"
VERSION = "1.5.0"
_HMAC_SECRET = b"kwcode-telemetry-2026-v1"


class TelemetryClient:
    """
    Fire-and-forget 匿名遥测。
    读取 ~/.kwcode/config.yaml 的 telemetry_enabled 字段。
    缺失该字段 = 关闭（向后兼容）。
    """

    def __init__(self):
        self._enabled = self._read_config()

    def _read_config(self) -> bool:
        try:
            if CONFIG_PATH.exists():
                config = yaml.safe_load(
                    CONFIG_PATH.read_text(encoding="utf-8")
                ) or {}
                return bool(config.get("telemetry_enabled", False))
        except Exception as e:
            logger.debug("telemetry config read failed: %s", e)
        return False

    def is_enabled(self) -> bool:
        return self._enabled

    def reload(self):
        """重新读取配置（用于 enable/disable 后刷新）。"""
        self._enabled = self._read_config()

    def report(
        self,
        error_type: str,
        retry_count: int,
        success: bool,
        model: str,
    ):
        """
        Fire-and-forget 上传匿名统计。
        daemon thread + 3s 超时，绝不阻塞主流程。
        HMAC-SHA256 签名防伪造。
        """
        if not self._enabled:
            return

        payload = {
            "error_type": error_type or "unknown",
            "retry_count": retry_count,
            "success": success,
            "model": model or "unknown",
            "version": VERSION,
        }

        thread = threading.Thread(
            target=self._upload,
            args=(payload,),
            daemon=True,
        )
        thread.start()

    def _upload(self, payload: dict):
        try:
            body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
            sig = hmac.new(_HMAC_SECRET, body, hashlib.sha256).hexdigest()
            httpx.post(
                TELEMETRY_URL,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-KWCode-Sig": sig,
                },
                timeout=3.0,
            )
        except Exception as e:
            logger.debug("telemetry upload failed (non-blocking): %s", e)
