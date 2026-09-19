"""雙軌節拍引擎：Universe Tick 與 Cognitive Heartbeat 調度器。"""

import asyncio
import hashlib
import os
import re
import shutil
import stat
import threading
import time
from collections.abc import Callable
from pathlib import Path

from cli.factory import get_adapter
from config.settings import HABITAT_STAGING_DIR, HISTORY_DIR, config
from core.context.manager import ContextManager
from core.lifecycle.snapshot import LifecycleManager
from core.review.guardian import Guardian
from core.runtime.supervisor import RuntimeSupervisor

MAX_ACCEPTED_SLEEP_CYCLES = 1000
CLI_CANCEL_WAIT_SECONDS = 4.0
MAX_CODE_TREE_ENTRIES = 4096
MAX_CODE_FILE_BYTES = 8 * 1024 * 1024
MAX_CODE_TREE_BYTES = 64 * 1024 * 1024


def _code_tree_fingerprint(root: Path, lifecycle_manager: LifecycleManager) -> str:
    """在資源上限內比對程式樹，拒絕連結並使用描述符逐層讀取。"""
    root = Path(root)
    root_stat = root.lstat()
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ValueError(f"候選程式根目錄不是一般目錄：'{root}'")
    digest = hashlib.sha256()
    entries = 0
    total_bytes = 0
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)

    def visit(directory_fd: int, relative: str, expected: os.stat_result | None = None) -> None:
        nonlocal entries, total_bytes
        try:
            opened = os.fstat(directory_fd)
            if not stat.S_ISDIR(opened.st_mode) or (expected is not None and
                    (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino)):
                raise ValueError(f"候選程式目錄在檢查時變更：'{relative or root.name}'")
            children = sorted(os.listdir(directory_fd))
        except OSError as exc:
            raise ValueError(f"無法列出候選程式目錄 '{relative or root}'：{exc}") from exc
        for name in children:
            if lifecycle_manager.is_database_file(name):
                continue
            rel = f"{relative}/{name}" if relative else name
            try:
                info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise ValueError(f"無法檢查候選程式項目 '{rel}'：{exc}") from exc
            entries += 1
            if entries > MAX_CODE_TREE_ENTRIES:
                raise ValueError("候選程式項目數超過安全上限")
            if stat.S_ISLNK(info.st_mode):
                raise ValueError(f"候選程式含有符號連結：'{rel}'")
            if stat.S_ISDIR(info.st_mode):
                digest.update(b"D\0" + rel.encode("utf-8", "surrogateescape") + b"\0"
                              + str(stat.S_IMODE(info.st_mode)).encode() + b"\0")
                try:
                    child_fd = os.open(name, directory_flags, dir_fd=directory_fd)
                except OSError as exc:
                    raise ValueError(f"無法安全開啟候選程式目錄 '{rel}'：{exc}") from exc
                try:
                    visit(child_fd, rel, info)
                finally:
                    os.close(child_fd)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"候選程式含有非一般檔案：'{rel}'")
            if info.st_nlink != 1:
                raise ValueError(f"候選程式含有硬連結檔案：'{rel}'")
            if info.st_size > MAX_CODE_FILE_BYTES:
                raise ValueError(f"候選程式檔案超過大小上限：'{rel}'")
            total_bytes += info.st_size
            if total_bytes > MAX_CODE_TREE_BYTES:
                raise ValueError("候選程式總大小超過安全上限")
            digest.update(b"F\0" + rel.encode("utf-8", "surrogateescape") + b"\0"
                          + str(stat.S_IMODE(info.st_mode)).encode() + b"\0"
                          + str(info.st_size).encode() + b"\0")
            # 不沿連結開啟，再以描述符確認仍是原本的一般單連結檔案。
            try:
                fd = os.open(name, file_flags, dir_fd=directory_fd)
                with os.fdopen(fd, "rb") as stream:
                    opened = os.fstat(stream.fileno())
                    if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                            or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)):
                        raise ValueError(f"候選程式項目在讀取時變更：'{rel}'")
                    size = 0
                    while chunk := stream.read(64 * 1024):
                        size += len(chunk)
                        if size > MAX_CODE_FILE_BYTES:
                            raise ValueError(f"候選程式檔案超過大小上限：'{rel}'")
                        if total_bytes - info.st_size + size > MAX_CODE_TREE_BYTES:
                            raise ValueError("候選程式總大小超過安全上限")
                        digest.update(chunk)
                    if size != info.st_size:
                        raise ValueError(f"候選程式檔案在讀取時變更：'{rel}'")
            except OSError as exc:
                raise ValueError(f"無法安全讀取候選程式檔案 '{rel}'：{exc}") from exc

    try:
        root_fd = os.open(root, directory_flags)
    except OSError as exc:
        raise ValueError(f"無法安全開啟候選程式根目錄 '{root}'：{exc}") from exc
    try:
        visit(root_fd, "", root_stat)
    finally:
        os.close(root_fd)
    return digest.hexdigest()


class CognitiveScheduler:
    """協調 AI 認知節拍、候選發布與運行監控器。"""

    def __init__(
        self,
        supervisor: RuntimeSupervisor,
        context_manager: ContextManager | None = None,
        guardian: Guardian | None = None,
        lifecycle_manager: LifecycleManager | None = None,
        staging_dir: Path = HABITAT_STAGING_DIR,
    ):
        self.supervisor = supervisor
        self.context_manager = context_manager or ContextManager()
        self.guardian = guardian or Guardian()
        self.lifecycle_manager = lifecycle_manager or LifecycleManager()
        self.staging_dir = Path(staging_dir).resolve()

        self.is_running = False
        self.sleep_cycles_remaining = 0
        self.last_heartbeat_time = 0.0
        self.current_turn_in_progress = False
        self.last_status: dict = {"status": "idle"}
        self.on_state_change: Callable[[dict], None] | None = None

        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._turn_cancelled = threading.Event()
        self._active_cli_done = threading.Event()
        self._active_cli_done.set()
        self._active_adapter = None
        self._generation = 0
        self._pause_requested = False
        self._shutdown_requested = False
        self._resource_blocked = False
        self._resource_block_reason: str | None = None
        self._worker_task: asyncio.Task | None = None

    @property
    def lifecycle_lock(self) -> threading.RLock:
        """供 API 控制端點共用的同步生命週期鎖。"""
        return self._lifecycle_lock

    def _publish_status(self, status: dict) -> None:
        self.last_status = status
        callback = self.on_state_change
        if callback is not None:
            try:
                callback(status)
            except Exception:
                pass

    def _publish_turn_status(self, generation: int, status: dict) -> None:
        """只發布仍有效回合的進度，避免暫停後顯示過期思考狀態。"""
        with self._state_lock:
            if not self._turn_was_cancelled_locked(generation):
                self._publish_status(status)

    def record_turn_log(self, message: str) -> None:
        """保留認知回合輸出，讓觀測者可診斷未部署的原因。"""
        try:
            log_path = HISTORY_DIR / "cognitive_turns.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(f"\n[{timestamp}] {message}\n")
        except OSError:
            # 記錄失敗不能阻斷世界本身的演化回合。
            pass

    def prepare_staging_workspace(self) -> None:
        """鏡像穩定 habitat，拒絕把符號連結追蹤到工作區外。"""
        if self.staging_dir.exists():
            shutil.rmtree(self.staging_dir)
        self.staging_dir.mkdir(parents=True, exist_ok=True)

        source_dir = self.supervisor.habitat_dir
        if not source_dir.exists():
            return
        _code_tree_fingerprint(source_dir, self.lifecycle_manager)
        for item in source_dir.iterdir():
            if self.lifecycle_manager.is_database_file(item.name):
                continue
            target = self.staging_dir / item.name
            if item.is_dir():
                self.lifecycle_manager.copy_code_tree(item, target)
            elif item.is_file():
                self.lifecycle_manager.copy_code_file(item, target)
        database = source_dir / "habitat.db"
        if database.exists():
            self.lifecycle_manager.copy_database_backup(database, self.staging_dir / "habitat.db")

    def _turn_was_cancelled(self, generation: int) -> bool:
        with self._state_lock:
            return self._turn_was_cancelled_locked(generation)

    def _turn_was_cancelled_locked(self, generation: int) -> bool:
        return (
            self._generation != generation
            or self._turn_cancelled.is_set()
            or self._pause_requested
            or self._shutdown_requested
            or self._resource_blocked
            or config.paused
        )

    def _restart_stable_if_allowed(self) -> None:
        with self._state_lock:
            entrypoint = self.supervisor.habitat_dir / "main.py"
            should_restart = (
                not self._pause_requested
                and not self._shutdown_requested
                and not self._resource_blocked
                and not config.paused
                and not self.supervisor.resource_block_reason
                and (entrypoint.exists() or entrypoint.is_symlink())
            )
            if should_restart:
                self.supervisor.start(tick_interval=config.universe_tick_interval)

    def _acknowledge_signals_if_current(
        self, generation: int, signal_ids: list[int]
    ) -> tuple[bool, str | None]:
        """以狀態鎖序列化訊息確認與暫停／shutdown 要求。"""
        with self._state_lock:
            if self._turn_was_cancelled_locked(generation):
                return False, None
            try:
                self.context_manager.acknowledge_observer_signals(signal_ids)
            except Exception as exc:
                return True, f"訊息確認失敗，訊息仍可重試：{exc}"
            return True, None

    def _deploy_candidate(self, generation: int, rounds: int, signal_ids: list[int]) -> dict:
        with self._lifecycle_lock:
            if self._turn_was_cancelled(generation):
                return {"status": "cancelled", "reason": "發布前收到暫停或關閉要求"}

            self.supervisor.stop()
            if self._turn_was_cancelled(generation):
                self._restart_stable_if_allowed()
                return {"status": "cancelled", "reason": "停止穩定版本後收到暫停或關閉要求"}

            try:
                db_snapshot, code_snapshot = self.lifecycle_manager.create_snapshot()
            except Exception as exc:
                try:
                    self._restart_stable_if_allowed()
                except Exception as restart_exc:
                    return {
                        "status": "error",
                        "error": f"建立快照失敗：{exc}；穩定版本重啟失敗：{restart_exc}",
                    }
                return {"status": "error", "error": f"建立快照失敗：{exc}"}

            if self._turn_was_cancelled(generation):
                self._restart_stable_if_allowed()
                return {"status": "cancelled", "reason": "快照完成後收到暫停或關閉要求"}

            try:
                self.lifecycle_manager.atomic_deploy_staging(self.staging_dir)
                if self._turn_was_cancelled(generation):
                    self.lifecycle_manager.rollback(db_snapshot, code_snapshot)
                    self._restart_stable_if_allowed()
                    return {"status": "cancelled", "reason": "候選啟動前收到暫停或關閉要求"}
                self.supervisor.start(tick_interval=config.universe_tick_interval)
                if self._turn_was_cancelled(generation):
                    self.supervisor.stop()
                    self.lifecycle_manager.rollback(db_snapshot, code_snapshot)
                    self._restart_stable_if_allowed()
                    return {"status": "cancelled", "reason": "候選啟動時收到暫停或關閉要求"}
            except Exception as deploy_error:
                # 失敗的候選進程先停止，確保回滾時沒有 SQLite 寫入者。
                self.supervisor.stop()
                try:
                    self.lifecycle_manager.rollback(db_snapshot, code_snapshot)
                    self._restart_stable_if_allowed()
                except Exception as rollback_error:
                    return {
                        "status": "rollback_error",
                        "error": f"部署失敗：{deploy_error}；回滾或穩定版本啟動失敗：{rollback_error}",
                    }
                return {"status": "rollback", "error": str(deploy_error)}

            acknowledged, acknowledgement_warning = self._acknowledge_signals_if_current(
                generation, signal_ids
            )
            if not acknowledged:
                self.supervisor.stop()
                self.lifecycle_manager.rollback(db_snapshot, code_snapshot)
                self._restart_stable_if_allowed()
                return {"status": "cancelled", "reason": "確認訊息前收到暫停或關閉要求"}
            result = {"status": "deployed", "rounds": rounds}
            if acknowledgement_warning:
                result["warning"] = acknowledgement_warning
            return result

    def execute_cognitive_turn(self) -> dict:
        """執行單回合 AI 認知節拍流程。"""
        with self._state_lock:
            if self.current_turn_in_progress:
                return {"status": "skipped", "reason": "上一回合仍在執行中"}
            if (
                self._shutdown_requested
                or self._pause_requested
                or self._resource_blocked
                or config.paused
            ):
                reason = self._resource_block_reason or "排程已暫停或正在關閉"
                return {"status": "skipped", "reason": reason}
            if self.sleep_cycles_remaining > 0:
                self.sleep_cycles_remaining -= 1
                self.last_heartbeat_time = time.monotonic()
                result = {
                    "status": "sleeping",
                    "cycles_left": self.sleep_cycles_remaining,
                }
                self._publish_status(result)
                return result
            self.current_turn_in_progress = True
            generation = self._generation
            self._turn_cancelled = threading.Event()
            turn_cancelled = self._turn_cancelled
            self._active_adapter = None

        result: dict = {"status": "error", "error": "回合尚未完成"}
        try:
            adapter = get_adapter()
            if not adapter.is_available():
                result = {
                    "status": "error",
                    "error": f"當前啟用的 CLI '{adapter.name}' 不可用。",
                }
                return result

            self.prepare_staging_workspace()
            original_code = _code_tree_fingerprint(self.staging_dir, self.lifecycle_manager)
            visible_world = self.context_manager.describe_visible_world()
            needs_visible_evolution = bool(visible_world.get("needs_evolution", True))

            # 讀取訊息與提示詞，但只在部署或合法休眠後確認此批訊息。
            prompt, signal_ids = self.context_manager.prepare_turn_context()
            max_repair_rounds = 3
            last_failure_reason = "候選未通過驗證，未提供進一步錯誤訊息"
            turn_success = False
            round_num = 0

            for round_num in range(1, max_repair_rounds + 1):
                if self._turn_was_cancelled(generation):
                    result = {"status": "cancelled", "reason": "執行認知回合時收到暫停或關閉要求"}
                    return result

                repair_note = ""
                if round_num > 1:
                    repair_note = (
                        "\n\n## 上一輪修復回饋\n"
                        f"{last_failure_reason}\n請在保留以上世界上下文的前提下修復候選程式。"
                    )
                current_prompt = f"{prompt}{repair_note}"
                cli_succeeded = True
                with self._state_lock:
                    if self._turn_was_cancelled_locked(generation):
                        result = {
                            "status": "cancelled",
                            "reason": "CLI 啟動前收到暫停或關閉要求",
                        }
                        return result
                    self._active_cli_done.clear()
                    self._active_adapter = adapter
                self._publish_turn_status(generation, {"status": "thinking", "round": round_num})
                try:
                    ai_output = adapter.execute_turn(
                        prompt=current_prompt,
                        workspace_path=self.staging_dir,
                        context={
                            "cancel_event": turn_cancelled,
                            "cancel_lock": self._state_lock,
                        },
                    )
                except Exception as exc:
                    cli_succeeded = False
                    ai_output = f"執行異常：{exc}"
                    last_failure_reason = f"CLI 執行異常：{exc}"
                finally:
                    with self._state_lock:
                        self._active_cli_done.set()

                changed_code = False
                code_scan_failed = False
                try:
                    changed_code = (
                        _code_tree_fingerprint(self.staging_dir, self.lifecycle_manager)
                        != original_code
                    )
                except (OSError, ValueError) as exc:
                    code_scan_failed = True
                    last_failure_reason = f"檢查候選程式碼失敗：{exc}"
                self.record_turn_log(
                    f"round={round_num}; code_changed={changed_code}; output:\n{ai_output[-6000:]}"
                )

                if self._turn_was_cancelled(generation):
                    result = {
                        "status": "cancelled",
                        "reason": "CLI 執行期間收到暫停或關閉要求",
                    }
                    return result
                if not cli_succeeded:
                    continue
                if code_scan_failed:
                    continue

                sleep_match = re.search(r"STATUS:\s*SLEEP\s+(\d+)", ai_output, re.IGNORECASE)
                if sleep_match:
                    requested_cycles = int(sleep_match.group(1))
                    if not needs_visible_evolution and 1 <= requested_cycles <= MAX_ACCEPTED_SLEEP_CYCLES:
                        with self._state_lock:
                            if self._turn_was_cancelled_locked(generation):
                                result = {
                                    "status": "cancelled",
                                    "reason": "休眠確認訊息前收到暫停或關閉要求",
                                }
                                return result
                            self.sleep_cycles_remaining = requested_cycles
                            self.last_heartbeat_time = time.monotonic()
                            try:
                                self.context_manager.acknowledge_observer_signals(signal_ids)
                                acknowledgement_warning = None
                            except Exception as exc:
                                acknowledgement_warning = (
                                    f"訊息確認失敗，訊息仍可重試：{exc}"
                                )
                        result = {
                            "status": "ai_sleep_requested",
                            "cycles": self.sleep_cycles_remaining,
                        }
                        if acknowledgement_warning:
                            result["warning"] = acknowledgement_warning
                        return result
                    last_failure_reason = (
                        "休眠請求不合法，或畫面仍需要演化；請修改候選程式碼並提供可見進展。"
                    )
                    continue

                if needs_visible_evolution and not changed_code:
                    last_failure_reason = "尚未偵測到候選程式碼的實際變更。"
                    continue

                self._publish_turn_status(generation, {"status": "verifying", "round": round_num})
                try:
                    verification = self.guardian.verify(self.staging_dir)
                except Exception as exc:
                    last_failure_reason = f"候選驗證器執行異常：{exc}"
                    continue
                if verification.passed:
                    turn_success = True
                    break
                last_failure_reason = (
                    f"候選版本在 '{verification.step}' 步驟未通過檢驗：\n"
                    f"{verification.error_message}\n請修復候選程式碼中的問題。"
                )

            if turn_success:
                self._publish_turn_status(generation, {"status": "deploying", "round": round_num})
                result = self._deploy_candidate(generation, round_num, signal_ids)
            else:
                result = {
                    "status": "abandoned",
                    "reason": (
                        f"經歷 {max_repair_rounds} 輪修復仍未通過驗收："
                        f"{last_failure_reason}"
                    ),
                }
            return result
        except Exception as exc:
            result = {"status": "error", "error": str(exc)}
            return result
        finally:
            with self._state_lock:
                self.last_heartbeat_time = time.monotonic()
                self.current_turn_in_progress = False
            self._publish_status(result)

    def pause_runtime(self) -> None:
        """先取消目前回合，再用生命週期鎖停止正式進程。"""
        with self._state_lock:
            self._turn_cancelled.set()
            self._generation += 1
            self._pause_requested = True
            config.paused = True
            active_adapter = self._active_adapter
            active_cli_done = self._active_cli_done

        cancel_error = None
        cancel_active = getattr(active_adapter, "cancel_active_processes", None)
        if callable(cancel_active):
            try:
                cancel_active()
            except Exception as exc:
                cancel_error = exc
        with self._lifecycle_lock:
            try:
                self.supervisor.stop()
            except Exception as exc:
                if cancel_error is None:
                    cancel_error = exc

        cli_stopped = active_cli_done.wait(timeout=CLI_CANCEL_WAIT_SECONDS)
        if cli_stopped and callable(cancel_active):
            try:
                # runner 結束後才回收殘留群組，避免與 pipe 讀取競爭。
                cancel_active()
            except Exception as exc:
                if cancel_error is None:
                    cancel_error = exc

        if not cli_stopped or cancel_error is not None:
            reason = (
                f"CLI 程序未能確認停止：{cancel_error}"
                if cancel_error is not None
                else "等待 CLI 程序停止逾時"
            )
            self._publish_status({"status": "pause_failed", "reason": reason})
            raise RuntimeError(reason)
        self._publish_status({"status": "paused", "reason": "觀察者已暫停宇宙"})

    def resume_runtime(self, tick_interval: float | None = None) -> None:
        """解除資源鎖並序列化恢復正式進程。"""
        with self._lifecycle_lock:
            with self._state_lock:
                if self._shutdown_requested:
                    raise RuntimeError("服務正在關閉，無法恢復宇宙")
                previous_resource_reason = self._resource_block_reason
                self._pause_requested = False
                self._resource_blocked = False
                self._resource_block_reason = None
                self.supervisor.clear_resource_block()
                config.paused = False
                try:
                    entrypoint = self.supervisor.habitat_dir / "main.py"
                    if entrypoint.exists() or entrypoint.is_symlink():
                        self.supervisor.start(
                            tick_interval=(
                                config.universe_tick_interval
                                if tick_interval is None
                                else tick_interval
                            )
                        )
                except Exception as exc:
                    self._generation += 1
                    self._pause_requested = True
                    self._resource_blocked = previous_resource_reason is not None
                    self._resource_block_reason = previous_resource_reason
                    self._turn_cancelled.set()
                    config.paused = True
                    if previous_resource_reason is not None:
                        self.supervisor.block_for_resource_violation(previous_resource_reason)
                    self._publish_status({"status": "resume_failed", "reason": str(exc)})
                    raise
            self._publish_status({"status": "running"})

    def restart_runtime(self, tick_interval: float | None = None) -> bool:
        """以生命週期鎖序列化速度切換與部署。"""
        with self._lifecycle_lock:
            with self._state_lock:
                may_restart = (
                    not config.paused
                    and not self._pause_requested
                    and not self._shutdown_requested
                    and not self._resource_blocked
                )
            if not may_restart:
                return False
            self.supervisor.stop()
            time.sleep(0.5)
            with self._state_lock:
                may_start = (
                    not config.paused
                    and not self._pause_requested
                    and not self._shutdown_requested
                    and not self._resource_blocked
                )
                if not may_start:
                    return False
                self.supervisor.start(tick_interval=tick_interval)
            return True

    def block_runtime_for_resources(self, reason: str) -> None:
        """記錄資源超限原因並停止受管進程樹，避免自動反覆重啟。"""
        with self._state_lock:
            self._generation += 1
            self._turn_cancelled.set()
            self._resource_blocked = True
            self._resource_block_reason = reason
            active_adapter = self._active_adapter
        cancel_active = getattr(active_adapter, "cancel_active_processes", None)
        if callable(cancel_active):
            cancel_active()
        with self._lifecycle_lock:
            self.supervisor.block_for_resource_violation(reason)
            self._publish_status({"status": "resource_limited", "reason": reason})

    def stop_loop(self) -> None:
        """請求排程與目前回合停止；不假設背景執行緒會隨 task cancel 結束。"""
        with self._state_lock:
            self._turn_cancelled.set()
            self._generation += 1
            self._shutdown_requested = True
            active_adapter = self._active_adapter
        cancel_active = getattr(active_adapter, "cancel_active_processes", None)
        if callable(cancel_active):
            cancel_active()
        self.is_running = False

    async def shutdown(self) -> None:
        """等待背景回合完成清理後，才停止正式宇宙進程。"""
        self.stop_loop()
        worker = self._worker_task
        if worker is not None and not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                # to_thread 中的函式仍執行時，必須等它完成後才能停進程。
                await asyncio.shield(worker)
            except Exception:
                pass
        with self._lifecycle_lock:
            self.supervisor.stop()

    async def run_loop(self) -> None:
        """依速度模式定期觸發回合；單次例外記錄後冷卻重試。"""
        self.is_running = True
        try:
            while self.is_running:
                with self._state_lock:
                    may_run = (
                        not config.paused
                        and not self._pause_requested
                        and not self._shutdown_requested
                        and not self._resource_blocked
                    )
                if may_run:
                    cooldown = max(0.0, config.heartbeat_cooldown)
                    elapsed = time.monotonic() - self.last_heartbeat_time
                    if elapsed >= cooldown and not self.current_turn_in_progress:
                        worker = asyncio.create_task(asyncio.to_thread(self.execute_cognitive_turn))
                        self._worker_task = worker
                        try:
                            await asyncio.shield(worker)
                        except asyncio.CancelledError:
                            self.stop_loop()
                            try:
                                await asyncio.shield(worker)
                            except Exception:
                                pass
                            raise
                        except Exception as exc:
                            self.record_turn_log(f"scheduler loop error: {exc}")
                            self.last_heartbeat_time = time.monotonic()
                            self._publish_status({"status": "error", "error": str(exc)})
                        finally:
                            if self._worker_task is worker:
                                self._worker_task = None
                await asyncio.sleep(0.25)
        finally:
            self.is_running = False
