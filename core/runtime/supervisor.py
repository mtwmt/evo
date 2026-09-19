"""Runtime Supervisor 運行監控器：管理 habitat/main.py 進程生命週期。"""

import subprocess
import threading
import time
from pathlib import Path

import psutil

from config.settings import HABITAT_DIR
from isolation import get_sandbox
from isolation.package_installer import PackageInstallationError, install_workspace_packages
from isolation.verifier import verify_sandbox_boundaries


class RuntimeSupervisor:
    """在沙盒中監督與維護 habitat/ 宇宙主進程。"""

    def __init__(
        self,
        habitat_dir: Path = HABITAT_DIR,
        startup_timeout: float = 2.0,
        startup_poll_interval: float = 0.05,
    ):
        self.habitat_dir = Path(habitat_dir).resolve()
        self.process: subprocess.Popen | None = None
        self.log_file = self.habitat_dir / "universe_runtime.log"
        self.startup_timeout = startup_timeout
        self.startup_poll_interval = startup_poll_interval
        self._log_handle = None
        self._lock = threading.RLock()
        self._resource_block_reason: str | None = None

    def is_running(self) -> bool:
        """檢查宇宙 main.py 目前是否在運作中。"""
        return self.process is not None and self.process.poll() is None

    def get_pid(self) -> int | None:
        """取得當前宇宙主進程之 PID。"""
        if self.is_running() and self.process:
            return self.process.pid
        return None

    @property
    def resource_block_reason(self) -> str | None:
        return self._resource_block_reason

    def block_for_resource_violation(self, reason: str) -> None:
        """鎖住自動啟動，直到操作者明確解除資源違規狀態。"""
        with self._lock:
            self._resource_block_reason = reason
            self.stop()

    def clear_resource_block(self) -> None:
        with self._lock:
            self._resource_block_reason = None

    def start(self, tick_interval: float | None = None) -> None:
        """啟動宇宙，並等待有限時間確認進程仍存活。"""
        with self._lock:
            if self._resource_block_reason:
                raise RuntimeError(f"宇宙因資源超限而停止：{self._resource_block_reason}")
            if self.is_running():
                return
            if self.process is not None:
                self._reap_process(self.process)

            main_py = self.habitat_dir / "main.py"
            if not main_py.exists():
                raise FileNotFoundError(f"無法啟動宇宙：找不到入口檔案 '{main_py}'。")

            try:
                install_workspace_packages(self.habitat_dir)
            except PackageInstallationError as exc:
                raise RuntimeError(f"受控線上套件安裝未完成，拒絕啟動宇宙：{exc}") from exc

            if not verify_sandbox_boundaries(self.habitat_dir):
                raise RuntimeError("沙盒邊界驗證失敗，拒絕啟動宇宙。")

            sandbox = get_sandbox(self.habitat_dir)
            env = sandbox.get_isolated_environ()
            if tick_interval is not None:
                env["EVO_TICK_INTERVAL"] = str(tick_interval)

            cmd = sandbox.build_command(main_py)

            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            log_handle = self.log_file.open("a", encoding="utf-8")
            try:
                process = subprocess.Popen(
                    cmd,
                    cwd=str(self.habitat_dir),
                    env=env,
                    stdout=log_handle,
                    stderr=log_handle,
                )
            except Exception:
                log_handle.close()
                raise

            self.process = process
            self._log_handle = log_handle
            deadline = time.monotonic() + self.startup_timeout
            while True:
                return_code = process.poll()
                if return_code is not None:
                    self._reap_process(process)
                    raise RuntimeError(
                        f"宇宙進程啟動後立即退出，結束碼：{return_code}。"
                    )
                if time.monotonic() >= deadline:
                    break
                time.sleep(min(self.startup_poll_interval, max(0.0, deadline - time.monotonic())))

    def _reap_process(self, process: subprocess.Popen) -> None:
        if self.process is process:
            self.process = None
        handle = self._log_handle
        self._log_handle = None
        if handle is not None:
            handle.close()

    def stop(self, timeout: float = 3.0) -> None:
        """停止受管進程樹並回收所有已啟動的進程。"""
        with self._lock:
            process = self.process
            if process is None:
                return

            children = []
            if process.poll() is None:
                try:
                    root = psutil.Process(process.pid)
                    children = root.children(recursive=True)
                except (psutil.NoSuchProcess, psutil.AccessDenied, PermissionError):
                    children = []

            for child in reversed(children):
                try:
                    child.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied, PermissionError):
                    pass
            if process.poll() is None:
                try:
                    process.terminate()
                except (ProcessLookupError, PermissionError):
                    pass

            try:
                _, alive_children = psutil.wait_procs(children, timeout=timeout)
            except (psutil.AccessDenied, PermissionError):
                alive_children = []
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except (ProcessLookupError, PermissionError):
                    pass
                process.wait()

            for child in alive_children:
                try:
                    child.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied, PermissionError):
                    pass
            if alive_children:
                try:
                    psutil.wait_procs(alive_children, timeout=timeout)
                except (psutil.NoSuchProcess, psutil.AccessDenied, PermissionError):
                    pass

            self._reap_process(process)

    def restart(self, tick_interval: float | None = None) -> None:
        """重啟宇宙 main.py 進程。"""
        with self._lock:
            self.stop()
            time.sleep(0.5)
            self.start(tick_interval=tick_interval)
