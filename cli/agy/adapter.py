"""Antigravity CLI (agy) 適配器實作。"""

import json
import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path
from threading import Event
from typing import Any

from cli.adapters.base import BaseCLIAdapter, ProcessCancelledError


class AgyAdapter(BaseCLIAdapter):
    """Google Antigravity CLI (`agy`) 適配器。"""

    def __init__(self):
        super().__init__(name="agy", default_model="gemini-3.1-pro-high")
        self._models_cache: list[str] = []
        self._models_cache_updated_at = 0.0

    def is_available(self) -> bool:
        """檢查系統中是否存在 agy 命令。"""
        return shutil.which("agy") is not None

    def fetch_available_models(self, cancel_event: Event | None = None) -> list[str]:
        """由 ``agy models`` 取得目前帳號實際可選用的模型。"""
        # 此方法會由 WebSocket 狀態封包頻繁呼叫，避免每次推送都啟動 agy。
        now = time.monotonic()
        if cancel_event is not None and cancel_event.is_set():
            return []
        if now - self._models_cache_updated_at < 300:
            return self._models_cache.copy()

        if not self.is_available():
            self._models_cache_updated_at = now
            return []

        try:
            proc = self.run_process(
                ["agy", "models"],
                cwd=Path.cwd(),
                context={"cancel_event": cancel_event} if cancel_event is not None else None,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            self._models_cache_updated_at = now
            return []
        except ProcessCancelledError:
            if cancel_event is None or not cancel_event.is_set():
                self._models_cache_updated_at = now
            return []

        if proc.returncode != 0:
            self._models_cache_updated_at = now
            return []

        # Agy 會列出目前帳號透過它可使用的所有供應商模型。模型 ID 位於
        # 每行第一欄；略過「Fetching models...」一類狀態文字即可。先前只
        # 保留 Gemini，會讓 Agy 實際提供的 Claude / GPT 模型在介面中消失。
        models: list[str] = []
        for line in proc.stdout.splitlines():
            model_id = line.split(maxsplit=1)[0] if line.strip() else ""
            if (
                "-" in model_id
                and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", model_id)
                and model_id not in models
            ):
                models.append(model_id)
        self._models_cache = models
        self._models_cache_updated_at = now
        return models.copy()

    def execute_turn(
        self,
        prompt: str,
        workspace_path: Path,
        context: dict[str, Any] | None = None
    ) -> str:
        """呼叫 agy 進行單回合認知修改，動態傳遞指定 model。"""
        if not self.is_available():
            raise RuntimeError("系統中未安裝或未在 PATH 中找到 'agy' CLI。")

        # 直接提供候選工作區目前的入口程式；空白生態池則明確交由 AI 創世。
        workspace = workspace_path.resolve()
        source_path = workspace / "main.py"
        source = self._read_candidate_source(source_path)
        if source is None:
            entrypoint_context = (
                f"Current entry point ({source_path}) does not exist.\n"
                "This is a blank genesis workspace. Create main.py and any supporting "
                "modules needed for the first autonomous universe. The complete candidate "
                "will be reviewed and smoke-tested before deployment."
            )
        else:
            entrypoint_context = (
                f"Current entry point ({source_path}):\n"
                "```python\n"
                f"{source}\n"
                "```"
            )
        agent_prompt = (
            f"{prompt}\n\n"
            f"Candidate workspace: {workspace}\n"
            f"{entrypoint_context}\n\n"
            "Use only native file read, write, and edit tools. Do not use "
            "RunCommand, shell, terminal, tests, or ls. Guardian handles testing."
        )

        # 明確要求 JSON 回應，以便辨識以代碼 0 結束、但遭工具權限提示擋下的情況。
        cmd = [
            "agy",
            "--prompt",
            agent_prompt,
            "--mode",
            "accept-edits",
            "--add-dir",
            str(workspace),
            "--output-format",
            "json",
        ]
        if self.current_model:
            cmd.extend(["--model", self.current_model])

        proc = self.run_process(
            cmd,
            cwd=str(workspace_path),
            context=context,
            timeout=300,
        )
        if self._permission_denied(proc.stderr):
            raise RuntimeError(
                "agy 遭工具權限提示阻擋："
                f"{self._diagnostic(proc.stderr)}"
            )
        if proc.returncode != 0:
            raise RuntimeError(
                f"agy 執行失敗（結束代碼 {proc.returncode}）："
                f"{self._diagnostic(proc.stderr or proc.stdout)}"
            )

        try:
            result = json.loads(proc.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError(
                f"agy 回傳的 JSON 格式無效：{self._diagnostic(proc.stdout)}"
            ) from exc
        if not isinstance(result, dict):
            raise RuntimeError("agy 回傳的 JSON 格式無效：預期為物件")
        status = result.get("status")
        response = result.get("response")
        if status != "SUCCESS":
            error = result.get("error")
            error_detail = (
                self._diagnostic(error)
                if isinstance(error, str) and error
                else "（未提供錯誤說明）"
            )
            raise RuntimeError(
                f"agy 回報狀態 {status!r}，錯誤：{error_detail}；"
                f"標準錯誤：{self._diagnostic(proc.stderr)}"
            )
        if not isinstance(response, str) or not response.strip():
            raise RuntimeError(
                "agy 回報 SUCCESS，但 response 為空或格式無效"
            )
        return response

    @staticmethod
    def _read_candidate_source(path: Path) -> str | None:
        """安全讀取 main.py；空白創世可缺少入口，其餘不安全檔案仍拒絕。"""
        max_bytes = 128 * 1024
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            fd = os.open(path, flags)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise RuntimeError(f"無法安全讀取候選程式碼 {path}：{exc}") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise RuntimeError(f"候選程式碼不是一般檔案：{path}")
            if info.st_nlink != 1:
                raise RuntimeError(f"候選程式碼不可為硬連結：{path}")
            if info.st_size > max_bytes:
                raise RuntimeError(
                    f"候選程式碼超過 {max_bytes} 位元組上限：{path}"
                )
            with os.fdopen(fd, "rb", closefd=False) as source_file:
                data = source_file.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise RuntimeError(
                    f"候選程式碼超過 {max_bytes} 位元組上限：{path}"
                )
            try:
                return data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise RuntimeError(f"候選程式碼不是 UTF-8 編碼：{path}") from exc
        finally:
            os.close(fd)

    @staticmethod
    def _permission_denied(stderr: str) -> bool:
        return bool(re.search(
            r"soft[- ]den(?:y|ying)|permission.{0,50}denied|"
            r"denied.{0,50}permission|tool confirmation.{0,50}(?:deny|denied)|"
            r"(?:approval|confirmation).{0,50}(?:required|denied|blocked|not granted)",
            stderr,
            re.IGNORECASE,
        ))

    @staticmethod
    def _diagnostic(text: str, limit: int = 1200) -> str:
        text = text.strip()
        if not text:
            return "（沒有診斷訊息）"
        return text if len(text) <= limit else text[:limit] + "…（已截斷）"
