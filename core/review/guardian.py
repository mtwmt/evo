"""候選版本審查門神（Guardian）：靜態檢查、安全冒煙與健康驗證。"""

import json
import os
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import NamedTuple

from isolation import get_sandbox
from isolation.common.sandbox import SandboxUnavailableError
from isolation.package_installer import PackageInstallationError, install_workspace_packages


class VerificationResult(NamedTuple):
    """驗證結果結構體。"""

    passed: bool
    step: str
    error_message: str | None


class Guardian:
    """外層守門員：在部署前檢驗候選版本的安全性、語法與穩定性。"""

    def __init__(self, ruff_path: str = "ruff"):
        self.ruff_path = ruff_path

    def run_static_analysis(self, candidate_dir: Path) -> VerificationResult:
        """對候選目錄執行 ruff check 靜態語法分析。"""
        candidate_dir = Path(candidate_dir).resolve()

        ruff_bin = shutil.which(self.ruff_path) or str(Path(sys.executable).parent / self.ruff_path)
        if not Path(ruff_bin).exists() and not shutil.which(ruff_bin):
            cmd = [sys.executable, "-m", "ruff", "check", str(candidate_dir)]
        else:
            cmd = [ruff_bin, "check", str(candidate_dir)]
        # 候選不能以自己的 ruff.toml、忽略註解或 gitignore 關閉池外檢查。
        cmd.extend(["--isolated", "--select", "E9,F", "--ignore-noqa", "--no-respect-gitignore"])

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if proc.returncode != 0:
                return VerificationResult(
                    passed=False,
                    step="static_analysis",
                    error_message=f"Ruff 靜態檢查未通過：\n{proc.stdout}\n{proc.stderr}",
                )
            return VerificationResult(passed=True, step="static_analysis", error_message=None)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return VerificationResult(
                passed=False,
                step="static_analysis",
                error_message=f"執行靜態分析失敗：{exc}",
            )

    @staticmethod
    def _stop_process_tree(proc: subprocess.Popen, timeout_seconds: float = 2.0) -> None:
        """停止候選進程群組並等待回收，超時後以 SIGKILL 清理。"""
        if proc.poll() is not None:
            proc.wait()
            return

        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()

    @staticmethod
    def _drain_output(stream, output: dict[str, bytearray]) -> None:
        """持續排空子程序輸出，只保留有限的開頭與尾端診斷文字。"""
        head_limit = 4096
        tail_limit = 8192
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            if len(output["head"]) < head_limit:
                remaining = head_limit - len(output["head"])
                output["head"].extend(chunk[:remaining])
            output["tail"].extend(chunk)
            if len(output["tail"]) > tail_limit:
                del output["tail"][:-tail_limit]

    @staticmethod
    def _format_output(output: dict[str, bytearray]) -> str:
        """將有限緩衝區轉成錯誤訊息，避免把大量輸出載入記憶體。"""
        head = bytes(output["head"])
        tail = bytes(output["tail"])
        truncated = len(tail) >= 8192 and head != tail[: len(head)]
        marker = "\n...[輸出已截斷]...\n".encode()
        combined = head if not truncated else head + marker + tail
        return combined.decode("utf-8", errors="replace")

    @staticmethod
    def _validate_candidate_database(db_file: Path) -> str | None:
        """檢查 SQLite 完整性、必要場景資料與可解析的觀測內容。"""
        try:
            db_stat = db_file.lstat()
        except FileNotFoundError:
            return "候選版本未建立 habitat.db。"
        if stat.S_ISLNK(db_stat.st_mode) or not stat.S_ISREG(db_stat.st_mode):
            return "候選 habitat.db 必須是工作區內的一般檔案，不可使用符號連結。"
        if db_stat.st_nlink != 1:
            return "候選 habitat.db 具有多個硬連結，拒絕由父程序開啟。"

        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(f"{db_file}{suffix}")
            try:
                sidecar_stat = sidecar.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(sidecar_stat.st_mode) or sidecar_stat.st_nlink != 1:
                return f"候選 SQLite sidecar {sidecar.name} 使用連結，拒絕由父程序開啟。"

        try:
            with sqlite3.connect(str(db_file), timeout=2) as connection:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()
                if integrity != ("ok",):
                    return f"候選 habitat.db 完整性檢查失敗：{integrity!r}"

                table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'scene_primitives'"
                ).fetchone()
                if table is None:
                    return "候選 habitat.db 缺少 scene_primitives 觀測資料表。"

                row = connection.execute(
                    "SELECT json_data FROM scene_primitives WHERE id = 'current_scene'"
                ).fetchone()
                if row is None or not isinstance(row[0], str) or not row[0].strip():
                    return "候選 habitat.db 尚未寫入 current_scene 觀測資料。"
                scene = json.loads(row[0])
                if not isinstance(scene, dict) or not isinstance(scene.get("entities"), list):
                    return "候選 current_scene 觀測資料格式無效。"
        except (sqlite3.Error, json.JSONDecodeError, OSError) as exc:
            return f"候選 habitat.db 無法讀取或驗證：{exc}"
        return None

    def run_smoke_test(self, candidate_dir: Path, timeout_seconds: float = 5.0) -> VerificationResult:
        """在原生 OS 沙盒中試跑候選，並驗證 SQLite 與場景觀測資料。"""
        requested_dir = Path(candidate_dir).absolute()
        if requested_dir.is_symlink():
            return VerificationResult(
                passed=False,
                step="sandbox_setup",
                error_message="候選工作區不可為符號連結。",
            )
        candidate_dir = requested_dir.resolve()
        main_file = candidate_dir / "main.py"
        if not main_file.is_file():
            return VerificationResult(
                passed=False,
                step="smoke_test",
                error_message="候選版本缺少核心入口檔案 'main.py'。",
            )
        if main_file.is_symlink():
            return VerificationResult(
                passed=False,
                step="sandbox_setup",
                error_message="候選入口 main.py 不可為符號連結。",
            )

        proc: subprocess.Popen | None = None
        output_state = {"head": bytearray(), "tail": bytearray()}
        output_reader: threading.Thread | None = None
        try:
            try:
                install_workspace_packages(candidate_dir)
            except PackageInstallationError as exc:
                return VerificationResult(
                    passed=False,
                    step="package_install",
                    error_message=f"候選受控套件安裝失敗：{exc}",
                )
            sandbox = get_sandbox(candidate_dir)
            environment = sandbox.get_isolated_environ()
            environment["EVO_SMOKE_TEST"] = "1"
            environment["EVO_TICK_INTERVAL"] = "0.1"
            command = sandbox.build_command(main_file)

            proc = subprocess.Popen(
                command,
                cwd=str(candidate_dir),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
                bufsize=0,
            )
            output_reader = threading.Thread(
                target=self._drain_output,
                args=(proc.stdout, output_state),
                name="guardian-smoke-output",
                daemon=True,
            )
            output_reader.start()

            time.sleep(max(0.0, timeout_seconds))
            poll_result = proc.poll()
            if poll_result is None:
                self._stop_process_tree(proc)
            output_reader.join(timeout=2)
            output = self._format_output(output_state)

            if "EVO_NATIVE_SANDBOX_ACTIVE" not in output:
                return VerificationResult(
                    passed=False,
                    step="sandbox_setup",
                    error_message=(
                        "原生 OS 沙盒尚未啟動；平台政策可能不支援，或外層執行環境"
                        f"拒絕 sandbox-exec。候選未獲准執行。\n{output[-4000:]}"
                    ),
                )

            if poll_result is not None:
                return VerificationResult(
                    passed=False,
                    step="smoke_test",
                    error_message=(
                        f"候選宇宙提早結束（退出代碼 {poll_result}）：\n{output[-4000:]}"
                    ),
                )

            database_error = self._validate_candidate_database(candidate_dir / "habitat.db")
            if database_error:
                return VerificationResult(
                    passed=False,
                    step="smoke_test",
                    error_message=database_error,
                )
            return VerificationResult(passed=True, step="smoke_test", error_message=None)
        except SandboxUnavailableError as exc:
            return VerificationResult(
                passed=False,
                step="sandbox_setup",
                error_message=f"原生 OS 沙盒不可用，拒絕執行候選：{exc}",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return VerificationResult(
                passed=False,
                step="sandbox_setup" if proc is None else "smoke_test",
                error_message=f"候選沙盒冒煙執行失敗：{exc}",
            )
        finally:
            if proc is not None:
                self._stop_process_tree(proc)
                if proc.stdout is not None:
                    proc.stdout.close()
            if output_reader is not None:
                output_reader.join(timeout=1)

    def verify(self, candidate_dir: Path) -> VerificationResult:
        """執行完整驗收管線：靜態分析後進行原生沙盒冒煙測試。"""
        static_result = self.run_static_analysis(candidate_dir)
        if not static_result.passed:
            return static_result

        return self.run_smoke_test(candidate_dir, timeout_seconds=5.0)
