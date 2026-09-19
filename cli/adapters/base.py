"""CLI 適配器抽象基底介面。"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from contextlib import ExitStack
from pathlib import Path
from threading import Event, RLock
from typing import Any


class ProcessCancelledError(RuntimeError):
    """當認知回合取消並停止 CLI 程序時拋出。"""


class ProcessTerminationError(RuntimeError):
    """當 CLI 程序樹無法在期限內終止或回收時拋出。"""


def _signal_process_group(process: subprocess.Popen, sig: int) -> None:
    """向隔離的 CLI 程序群組送出訊號，必要時退回操作根程序。"""
    try:
        if os.name == "nt":
            if sig == signal.SIGTERM and hasattr(signal, "CTRL_BREAK_EVENT"):
                process.send_signal(signal.CTRL_BREAK_EVENT)
            elif sig == signal.SIGTERM:
                process.terminate()
            else:
                process.kill()
        else:
            os.killpg(process.pid, sig)
    except (OSError, ProcessLookupError):
        # 群組可能在檢查與送出訊號之間已經結束。
        if process.poll() is None:
            try:
                process.terminate() if sig == signal.SIGTERM else process.kill()
            except OSError:
                pass


def _process_group_exists(process: subprocess.Popen) -> bool:
    if os.name == "nt":
        return process.poll() is None
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stop_process_group(process: subprocess.Popen, grace_seconds: float = 0.4) -> None:
    """停止 CLI 程序樹，必要時強制結束，並確認根程序已回收。"""
    _signal_process_group(process, signal.SIGTERM)
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline and _process_group_exists(process):
        process.poll()  # 根程序結束時盡早回收。
        time.sleep(0.02)

    if _process_group_exists(process):
        _signal_process_group(process, signal.SIGKILL)

    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        try:
            process.communicate(timeout=0.05)
        except subprocess.TimeoutExpired:
            pass
        if process.poll() is not None and not _process_group_exists(process):
            return
        time.sleep(0.02)

    if process.poll() is None:
        try:
            process.kill()
            process.wait(timeout=0.2)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProcessTerminationError("CLI 根程序未能在期限內回收") from exc
    if _process_group_exists(process):
        raise ProcessTerminationError("CLI 子程序仍存活，無法確認程序樹已停止")


def run_cancellable_process(
    args: list[str],
    *,
    cwd: str | Path,
    context: dict[str, Any] | None = None,
    timeout: float = 300,
) -> subprocess.CompletedProcess[str]:
    """在獨立程序群組執行 CLI，收到取消要求時儘快停止。

    ``external_cancel_lock`` 與排程器共用，``cancel_lock`` 則保護此適配器的
    程序登記；兩者一起序列化最後一次取消檢查與 Popen。
    """
    context = context or {}
    cancel_event: Event | None = context.get("cancel_event")
    adapter_cancel_event: Event | None = context.get("adapter_cancel_event")
    cancel_lock: RLock | None = context.get("cancel_lock")
    external_cancel_lock = context.get("external_cancel_lock")
    generation = context.get("process_generation")
    current_generation = context.get("current_generation")
    register_process = context.get("register_process")
    unregister_process = context.get("unregister_process")

    def was_cancelled() -> bool:
        return any(
            event is not None and event.is_set()
            for event in (cancel_event, adapter_cancel_event)
        )

    def launch() -> subprocess.Popen[str]:
        if was_cancelled():
            raise ProcessCancelledError("CLI 回合已取消，未啟動程序")
        if current_generation is not None and current_generation() != generation:
            raise ProcessCancelledError("CLI 執行已取消，未啟動程序")
        options: dict[str, Any] = {}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        process = subprocess.Popen(
            args,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **options,
        )
        if register_process is not None:
            register_process(process, adapter_cancel_event or Event(), Event())
        return process

    with ExitStack() as locks:
        if external_cancel_lock is not None:
            locks.enter_context(external_cancel_lock)
        if cancel_lock is not None and cancel_lock is not external_cancel_lock:
            locks.enter_context(cancel_lock)
        process = launch()

    try:
        deadline = time.monotonic() + timeout
        while True:
            if was_cancelled():
                _stop_process_group(process)
                raise ProcessCancelledError("CLI 回合已取消，程序已停止")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop_process_group(process)
                raise subprocess.TimeoutExpired(args, timeout)

            try:
                stdout, stderr = process.communicate(timeout=min(0.1, remaining))
                if _process_group_exists(process):
                    _stop_process_group(process)
                return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        if process.poll() is None or _process_group_exists(process):
            _stop_process_group(process)
        raise
    finally:
        if unregister_process is not None:
            unregister_process(process)


class BaseCLIAdapter(ABC):
    """LLM CLI 適配器抽象介面：支援動態模型探索與自訂最新模型切換，拒絕死板 Hardcode。"""

    def __init__(self, name: str, default_model: str = ""):
        self.name = name
        self.current_model = default_model
        self.current_effort = ""
        self._process_lock = RLock()
        self._process_generation = 0
        self._active_processes: dict[subprocess.Popen, tuple[Event, Event]] = {}

    def cancel_active_processes(self) -> None:
        """通知此適配器目前執行中的程序自行終止與回收。"""
        with self._process_lock:
            self._process_generation += 1
            for cancel_event, runner_done in self._active_processes.values():
                cancel_event.set()
            active = tuple(self._active_processes)
        for process in active:
            with self._process_lock:
                state = self._active_processes.get(process)
            if state is None or not state[1].is_set():
                continue
            _stop_process_group(process)
            with self._process_lock:
                if not _process_group_exists(process):
                    self._active_processes.pop(process, None)

    def run_process(
        self,
        args: list[str],
        *,
        cwd: str | Path,
        context: dict[str, Any] | None = None,
        timeout: float = 300,
    ) -> subprocess.CompletedProcess[str]:
        """以此適配器執行並管理子程序。"""
        with self._process_lock:
            generation = self._process_generation
            adapter_cancel_event = Event()
        external_cancel_lock = (context or {}).get("cancel_lock")
        managed_context = dict(context or {})
        managed_context.update(
            {
                "adapter_cancel_event": adapter_cancel_event,
                "external_cancel_lock": external_cancel_lock,
                "cancel_lock": self._process_lock,
                "process_generation": generation,
                "current_generation": lambda: self._process_generation,
                "register_process": self._register_process,
                "unregister_process": self._unregister_process,
            }
        )
        return run_cancellable_process(
            args,
            cwd=cwd,
            context=managed_context,
            timeout=timeout,
        )

    def _register_process(
        self,
        process: subprocess.Popen,
        cancel_event: Event,
        runner_done: Event,
    ) -> None:
        self._active_processes[process] = (cancel_event, runner_done)

    def _unregister_process(self, process: subprocess.Popen) -> None:
        with self._process_lock:
            state = self._active_processes.get(process)
            if state is not None:
                state[1].set()
                if process.poll() is not None and not _process_group_exists(process):
                    self._active_processes.pop(process, None)

    @property
    def supported_efforts(self) -> tuple[str, ...]:
        """回傳 CLI 支援的推理強度；空值代表不支援。"""
        return ()

    @abstractmethod
    def is_available(self) -> bool:
        """檢查系統 PATH 中是否存在該 CLI 可執行檔。"""
        pass

    @abstractmethod
    def fetch_available_models(self, cancel_event: Event | None = None) -> list[str]:
        """動態向 CLI 或本機環境探索最新可用之模型清單。"""
        pass

    def set_model(self, model_name: str) -> None:
        """設定當前使用的模型名稱。允許輸入任何最新發布或自訂的模型字串。"""
        cleaned = model_name.strip()
        if not cleaned:
            raise ValueError("模型名稱不能為空。")
        self.current_model = cleaned

    def set_effort(self, effort: str) -> None:
        """設定 CLI 推理強度。"""
        if effort not in self.supported_efforts:
            raise ValueError(f"{self.name} 不支援推理強度 '{effort}'。")
        self.current_effort = effort

    @abstractmethod
    def execute_turn(
        self,
        prompt: str,
        workspace_path: Path,
        context: dict[str, Any] | None = None
    ) -> str:
        """執行一輪 AI 認知節拍。

        參數：
            prompt: 完整組裝之提示詞。
            workspace_path: 供 AI 直接讀寫修改的 habitat 目錄。
            context: 額外環境資訊字典。

        回傳：
            CLI 輸出之文字或日誌內容。
        """
        pass
