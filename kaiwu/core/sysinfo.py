"""
硬件信息采集模块。
- psutil 获取 RAM/CPU（跨平台）
- nvidia-smi 获取 GPU VRAM（graceful fallback）
- VRAMWatcher 后台线程每10秒刷新 VRAM
"""

import platform
import subprocess
import threading
from dataclasses import dataclass

import psutil


@dataclass
class SysInfo:
    gpu_name: str = "N/A"
    vram_used_gb: float = 0.0
    vram_total_gb: float = 0.0
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    cpu_name: str = "N/A"


def get_sysinfo() -> SysInfo:
    """采集一次完整硬件信息（启动时调用）。"""
    info = SysInfo()

    # RAM（psutil，跨平台）
    vm = psutil.virtual_memory()
    info.ram_total_gb = vm.total / 1024**3
    info.ram_used_gb = vm.used / 1024**3

    # CPU
    try:
        info.cpu_name = platform.processor() or "Unknown CPU"
        if len(info.cpu_name) > 20:
            info.cpu_name = info.cpu_name[:20] + "…"
    except Exception:
        pass

    # GPU VRAM（平台特定检测）
    if platform.system() == "Darwin":
        # macOS: Apple Silicon 或 AMD GPU
        try:
            out = subprocess.check_output(
                ["sysctl", "hw.model"],
                timeout=2,
                stderr=subprocess.DEVNULL,
            ).decode(encoding="utf-8").strip()
            if "Mac" in out:
                info.gpu_name = "Apple Silicon GPU"
                # macOS 使用统一内存架构，VRAM 信息不适用
        except Exception:
            pass
    else:
        # Windows/Linux: NVIDIA GPU（nvidia-smi）
        try:
            out = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                timeout=2,
                stderr=subprocess.DEVNULL,
            ).decode(encoding="utf-8").strip().split("\n")[0]
            parts = [p.strip() for p in out.split(",")]
            if len(parts) == 3:
                info.gpu_name = parts[0][:20]
                info.vram_used_gb = int(parts[1]) / 1024
                info.vram_total_gb = int(parts[2]) / 1024
        except Exception:
            pass  # 非NVIDIA或未安装驱动，显示 N/A

    return info


class VRAMWatcher:
    """
    后台守护线程，每10秒刷新一次 VRAM 使用量到 status_bar.vram_used。
    daemon=True 随主进程退出自动销毁。
    """

    INTERVAL = 10  # 秒

    def __init__(self, status_bar):
        self._status = status_bar
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="vram-watcher"
        )

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(timeout=self.INTERVAL):
            try:
                out = subprocess.check_output(
                    [
                        "nvidia-smi",
                        "--query-gpu=memory.used",
                        "--format=csv,noheader,nounits",
                    ],
                    timeout=2,
                    stderr=subprocess.DEVNULL,
                ).decode(encoding="utf-8").strip()
                # 只取第一行第一个数字，防止多GPU或格式变化
                first_line = out.split("\n")[0].strip()
                val = int("".join(c for c in first_line if c.isdigit()) or "0")
                if val > 0:
                    self._status.vram_used = val / 1024
            except Exception:
                pass  # nvidia-smi 不可用时静默跳过
