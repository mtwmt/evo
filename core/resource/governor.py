"""Evo 資源守門員：以週期取樣監控進程樹與儲存配額。"""

import threading
import time
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
    cpu_sampled: bool = True


class ResourceGovernor:
    """週期取樣資源並回報超限；不是核心層級的即時硬性配額。"""

    def __init__(
        self,
        target_pid: int | None = None,
        habitat_dir: Path = HABITAT_DIR,
        *,
        cpu_limit_percent: float = MAX_CPU_PERCENT,
        ram_limit_bytes: int = MAX_RAM_BYTES,
        subprocess_limit: int = MAX_SUBPROCESSES,
        habitat_limit_bytes: int = MAX_HABITAT_SIZE_BYTES,
        sample_interval: float = 1.0,
        cpu_violation_samples: int = 3,
    ):
        self.target_pid = target_pid
        self.habitat_dir = Path(habitat_dir).resolve()
        self.cpu_limit_percent = cpu_limit_percent
        self.ram_limit_bytes = ram_limit_bytes
        self.subprocess_limit = subprocess_limit
        self.habitat_limit_bytes = habitat_limit_bytes
        self.sample_interval = max(0.0, sample_interval)
        self.cpu_violation_samples = max(1, cpu_violation_samples)
        self._sample_lock = threading.RLock()
        self._root_identity: tuple[int, float] | None = None
        self._cpu_baseline: dict[tuple[int, float], float] = {}
        self._last_cpu_sample_at: float | None = None
        self._last_cpu_percent = 0.0
        self._cpu_sample_ready = False
        self._cpu_over_limit_samples = 0
        self._cpu_limit_latched = False

    def get_habitat_disk_usage(self) -> int:
        """計算 habitat 目錄總用量；無法讀取的檔案不納入總數。"""
        if not self.habitat_dir.exists():
            return 0
        total_size = 0
        for entry in self.habitat_dir.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                try:
                    total_size += entry.stat().st_size
                except OSError:
                    continue
        return total_size

    def _collect_process_tree(
        self, pid: int | None
    ) -> tuple[tuple[int, float] | None, dict[tuple[int, float], float], int, int]:
        if pid is None:
            return None, {}, 0, 0
        try:
            root = psutil.Process(pid)
            root_identity = (root.pid, root.create_time())
            processes = [root, *root.children(recursive=True)]
        except (psutil.NoSuchProcess, psutil.AccessDenied, PermissionError):
            return None, {}, 0, 0

        cpu_times: dict[tuple[int, float], float] = {}
        ram_total = 0
        for proc in processes:
            try:
                identity = (proc.pid, proc.create_time())
                times = proc.cpu_times()
                cpu_times[identity] = times.user + times.system
                ram_total += proc.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied, PermissionError):
                continue
        return root_identity, cpu_times, ram_total, max(0, len(processes) - 1)

    def _sample_cpu(
        self,
        pid: int | None,
        now: float,
    ) -> tuple[float, bool]:
        """以同一進程身份的 CPU 時間差估算用量，首次取樣只建立基準。"""
        root_identity, cpu_times, _, _ = self._collect_process_tree(pid)
        if root_identity is None:
            self._root_identity = None
            self._cpu_baseline = {}
            self._last_cpu_sample_at = None
            self._last_cpu_percent = 0.0
            self._cpu_sample_ready = False
            self._cpu_over_limit_samples = 0
            self._cpu_limit_latched = False
            return 0.0, False

        if root_identity != self._root_identity or self._last_cpu_sample_at is None:
            self._root_identity = root_identity
            self._cpu_baseline = cpu_times
            self._last_cpu_sample_at = now
            self._last_cpu_percent = 0.0
            self._cpu_sample_ready = False
            self._cpu_over_limit_samples = 0
            self._cpu_limit_latched = False
            return 0.0, False

        elapsed = now - self._last_cpu_sample_at
        if elapsed < self.sample_interval:
            return self._last_cpu_percent, self._cpu_sample_ready

        if not self._cpu_baseline or not cpu_times:
            self._cpu_baseline = cpu_times
            self._last_cpu_sample_at = now
            self._last_cpu_percent = 0.0
            self._cpu_sample_ready = False
            return 0.0, False

        cpu_delta = sum(
            max(0.0, value - self._cpu_baseline.get(identity, value))
            for identity, value in cpu_times.items()
        )
        self._last_cpu_percent = 100.0 * cpu_delta / max(elapsed, 1e-9)
        self._cpu_sample_ready = True
        self._cpu_baseline = cpu_times
        self._last_cpu_sample_at = now
        if self._last_cpu_percent > self.cpu_limit_percent:
            self._cpu_over_limit_samples += 1
        else:
            self._cpu_over_limit_samples = 0
            self._cpu_limit_latched = False
        if self._cpu_over_limit_samples >= self.cpu_violation_samples:
            self._cpu_limit_latched = True
        return self._last_cpu_percent, True

    def check_limits(self, pid: int | None = None, *, now: float | None = None) -> ResourceMetrics:
        """檢驗目標進程樹與磁碟用量。CPU 超限需連續多次取樣才會觸發。"""
        target_pid = self.target_pid if pid is None else pid
        with self._sample_lock:
            sampled_at = time.monotonic() if now is None else now
            cpu_total, cpu_sampled = self._sample_cpu(target_pid, sampled_at)
            _, _, ram_total, subproc_count = self._collect_process_tree(target_pid)
            disk_usage = self.get_habitat_disk_usage()

            warning = None
            healthy = True
            if ram_total > self.ram_limit_bytes:
                healthy = False
                warning = (
                    f"RAM 記憶體超過上限：{ram_total / (1024 * 1024):.1f}MB > "
                    f"{self.ram_limit_bytes / (1024 * 1024):.0f}MB"
                )
            elif subproc_count > self.subprocess_limit:
                healthy = False
                warning = f"子進程數量超過上限：{subproc_count} > {self.subprocess_limit}"
            elif disk_usage > self.habitat_limit_bytes:
                healthy = False
                warning = (
                    f"Habitat 磁碟配額超過上限：{disk_usage / (1024 * 1024):.1f}MB > "
                    f"{self.habitat_limit_bytes / (1024 * 1024):.0f}MB"
                )
            elif self._cpu_limit_latched:
                healthy = False
                warning = (
                    f"CPU 持續超過取樣限制：{cpu_total:.1f}% > "
                    f"{self.cpu_limit_percent:.1f}%"
                )

            return ResourceMetrics(
                cpu_percent=cpu_total,
                ram_bytes=ram_total,
                subprocess_count=subproc_count,
                habitat_size_bytes=disk_usage,
                is_healthy=healthy,
                warning_message=warning,
                cpu_sampled=cpu_sampled,
            )


def _resource_monitor_interval(governor: ResourceGovernor) -> float:
    """讓監控週期至少符合 CPU 取樣間隔。"""
    return max(0.25, governor.sample_interval)
