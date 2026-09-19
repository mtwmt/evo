"""Evo 資源守門員（Resource Governor）：監控進程樹與儲存配額。"""

from pathlib import Path
from typing import NamedTuple

import psutil

from config.settings import (
    HABITAT_DIR,
    MAX_CPU_PERCENT,
    MAX_HABITAT_SIZE_BYTES,
    MAX_RAM_BYTES,
    MAX_SUBPROCESSES,
)


class ResourceMetrics(NamedTuple):
    """資源使用量指標資料結構。"""
    cpu_percent: float
    ram_bytes: int
    subprocess_count: int
    habitat_size_bytes: int
    is_healthy: bool
    warning_message: str | None


class ResourceGovernor:
    """監控並強制執行 CPU、記憶體、子進程數與磁碟空間配額。"""

    def __init__(self, target_pid: int | None = None, habitat_dir: Path = HABITAT_DIR):
        self.target_pid = target_pid
        self.habitat_dir = Path(habitat_dir).resolve()

    def get_habitat_disk_usage(self) -> int:
        """計算 habitat 目錄所佔用的總位元組數。"""
        if not self.habitat_dir.exists():
            return 0
        total_size = 0
        for entry in self.habitat_dir.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                try:
                    total_size += entry.stat().st_size
                except OSError:
                    pass
        return total_size

    def check_limits(self, pid: int | None = None) -> ResourceMetrics:
        """檢驗目標進程樹與磁碟空間之資源消耗。"""
        target_pid = pid or self.target_pid
        cpu_total = 0.0
        ram_total = 0
        subproc_count = 0
        warning = None
        healthy = True

        if target_pid and psutil.pid_exists(target_pid):
            try:
                parent = psutil.Process(target_pid)
                children = parent.children(recursive=True)
                subproc_count = len(children)

                # 累計進程樹中所有進程的 CPU 與記憶體
                all_procs = [parent] + children
                for proc in all_procs:
                    try:
                        ram_total += proc.memory_info().rss
                        cpu_total += proc.cpu_percent(interval=None)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        disk_usage = self.get_habitat_disk_usage()

        # 硬性邊界檢核（依據 README 5.5）
        if ram_total > MAX_RAM_BYTES:
            healthy = False
            warning = f"RAM 記憶體超過上限：{ram_total / (1024*1024):.1f}MB > {MAX_RAM_BYTES / (1024*1024):.0f}MB"
        elif subproc_count > MAX_SUBPROCESSES:
            healthy = False
            warning = f"子進程數量超過上限：{subproc_count} > {MAX_SUBPROCESSES}"
        elif disk_usage > MAX_HABITAT_SIZE_BYTES:
            healthy = False
            warning = f"Habitat 磁碟配額超過上限：{disk_usage / (1024*1024):.1f}MB > {MAX_HABITAT_SIZE_BYTES / (1024*1024):.0f}MB"
        elif cpu_total > (MAX_CPU_PERCENT * 2.0):  # 給予短暫容許區間後提出警告
            warning = f"CPU 負載偏高：{cpu_total:.1f}%（目標限制：{MAX_CPU_PERCENT}%）"

        return ResourceMetrics(
            cpu_percent=cpu_total,
            ram_bytes=ram_total,
            subprocess_count=subproc_count,
            habitat_size_bytes=disk_usage,
            is_healthy=healthy,
            warning_message=warning,
        )
