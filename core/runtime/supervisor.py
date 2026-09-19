"""Runtime Supervisor 運行監控器：管理 habitat/main.py 進程生命週期。"""

import subprocess
import time
from pathlib import Path

from config.settings import HABITAT_DIR
from isolation import get_sandbox
from isolation.verifier import verify_sandbox_boundaries


class RuntimeSupervisor:
    """在沙盒中監督與維護 habitat/ 宇宙主進程。"""

    def __init__(self, habitat_dir: Path = HABITAT_DIR):
        self.habitat_dir = Path(habitat_dir).resolve()
        self.process: subprocess.Popen | None = None
        self.log_file = self.habitat_dir / "universe_runtime.log"

    def is_running(self) -> bool:
        """檢查宇宙 main.py 目前是否在運作中。"""
        return self.process is not None and self.process.poll() is None

    def get_pid(self) -> int | None:
        """取得當前宇宙主進程之 PID。"""
        if self.is_running() and self.process:
            return self.process.pid
        return None

    def start(self, tick_interval: float | None = None) -> None:
        """在安全沙盒隔離環境中啟動 habitat/main.py。"""
        if self.is_running():
            return

        main_py = self.habitat_dir / "main.py"
        if not main_py.exists():
            raise FileNotFoundError(f"無法啟動宇宙：找不到入口檔案 '{main_py}'。")

        if not verify_sandbox_boundaries(self.habitat_dir):
            raise RuntimeError("沙盒邊界驗證失敗，拒絕啟動宇宙。")

        sandbox = get_sandbox(self.habitat_dir)
        env = sandbox.get_isolated_environ()
        if tick_interval is not None:
            env["EVO_TICK_INTERVAL"] = str(tick_interval)

        cmd = sandbox.build_command(main_py)

        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        # 以附加模式寫入宇宙執行日誌
        log_handle = open(self.log_file, "a", encoding="utf-8")

        self.process = subprocess.Popen(
            cmd,
            cwd=str(self.habitat_dir),
            env=env,
            stdout=log_handle,
            stderr=log_handle,
        )

    def stop(self, timeout: float = 3.0) -> None:
        """優雅停止 habitat/main.py 進程。"""
        if not self.is_running() or not self.process:
            return

        self.process.terminate()
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()

        self.process = None

    def restart(self, tick_interval: float | None = None) -> None:
        """重啟 habitat/main.py 進程。"""
        self.stop()
        time.sleep(0.5)
        self.start(tick_interval=tick_interval)
