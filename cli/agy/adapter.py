"""Antigravity CLI (agy) 適配器實作。"""

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from cli.adapters.base import BaseCLIAdapter


class AgyAdapter(BaseCLIAdapter):
    """Google Antigravity CLI (`agy`) 適配器。"""

    def __init__(self):
        super().__init__(name="agy", default_model="gemini-3.1-pro-high")
        self._models_cache: list[str] = []
        self._models_cache_updated_at = 0.0

    def is_available(self) -> bool:
        """檢查系統中是否存在 agy 命令。"""
        return shutil.which("agy") is not None

    def fetch_available_models(self) -> list[str]:
        """由 ``agy models`` 取得目前帳號實際可選用的模型。"""
        # 此方法會由 WebSocket 狀態封包頻繁呼叫，避免每次推送都啟動 agy。
        now = time.monotonic()
        if now - self._models_cache_updated_at < 300:
            return self._models_cache.copy()

        if not self.is_available():
            self._models_cache_updated_at = now
            return []

        try:
            proc = subprocess.run(
                ["agy", "models"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
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

        # 工作目錄由 cwd 指定；以 accept-edits 明確要求單回合代理直接在
        # staging 工作區實作，而非僅回覆設計建議。
        cmd = ["agy", "--prompt", prompt, "--mode", "accept-edits"]
        if self.current_model:
            cmd.extend(["--model", self.current_model])

        proc = subprocess.run(
            cmd,
            cwd=str(workspace_path),
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"agy 執行失敗（代碼 {proc.returncode}）：{proc.stderr}")
        return proc.stdout
