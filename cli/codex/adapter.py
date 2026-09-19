"""Codex CLI 適配器實作。"""

import json
import shutil
import time
from pathlib import Path
from threading import Event
from typing import Any

from cli.adapters.base import BaseCLIAdapter


class CodexAdapter(BaseCLIAdapter):
    """Codex CLI (`codex`) 適配器。"""

    def __init__(self):
        super().__init__(name="codex", default_model="gpt-5.6-terra")
        self.current_effort = "medium"
        self._models_cache: list[str] = []
        self._models_cache_updated_at = 0.0

    def _find_binary(self) -> str | None:
        """尋找系統中的 codex 二進制檔。"""
        return shutil.which("codex")

    def is_available(self) -> bool:
        """檢查 codex 是否可用。"""
        return self._find_binary() is not None

    def fetch_available_models(self, cancel_event: Event | None = None) -> list[str]:
        """讀取 Codex CLI 為目前帳號快取的可用模型目錄。"""
        now = time.monotonic()
        if now - self._models_cache_updated_at < 300:
            return self._models_cache.copy()

        catalog_path = Path.home() / ".codex" / "models_cache.json"
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            models = [
                item["slug"]
                for item in catalog.get("models", [])
                if isinstance(item, dict)
                and isinstance(item.get("slug"), str)
                and item.get("visibility") == "list"
            ]
        except (OSError, json.JSONDecodeError):
            models = []

        self._models_cache = models
        self._models_cache_updated_at = now
        return models.copy()

    @property
    def supported_efforts(self) -> tuple[str, ...]:
        return ("low", "medium", "high", "xhigh", "max", "ultra")

    def execute_turn(
        self,
        prompt: str,
        workspace_path: Path,
        context: dict[str, Any] | None = None
    ) -> str:
        """呼叫 codex exec 進行單回合非互動執行，動態傳遞指定 model。"""
        binary = self._find_binary()
        if not binary:
            raise RuntimeError("系統中未安裝或未在 PATH 中找到 'codex' CLI。")

        cmd = [binary, "exec"]
        cmd.extend(["-c", f'model_reasoning_effort="{self.current_effort}"'])
        if self.current_model:
            cmd.extend(["-m", self.current_model])
        cmd.append(prompt)

        proc = self.run_process(
            cmd,
            cwd=str(workspace_path),
            context=context,
            timeout=300,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"codex 執行失敗（代碼 {proc.returncode}）：{proc.stderr}")
        return proc.stdout
