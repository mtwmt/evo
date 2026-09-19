"""Claude CLI 適配器實作。"""

import shutil
from pathlib import Path
from threading import Event
from typing import Any

from cli.adapters.base import BaseCLIAdapter


class ClaudeAdapter(BaseCLIAdapter):
    """Claude Code CLI (`claude`) 適配器。"""

    def __init__(self):
        super().__init__(name="claude", default_model="sonnet")
        self.current_effort = "medium"

    def is_available(self) -> bool:
        """檢查系統中是否存在 claude 命令。"""
        return shutil.which("claude") is not None

    def fetch_available_models(self, cancel_event: Event | None = None) -> list[str]:
        """回傳目前 Claude Code CLI 支援的模型別名。"""
        # Claude Code 沒有 `claude models` 子命令；其 --help 指定下列可用別名。
        return ["fable", "opus", "sonnet", "haiku"]

    @property
    def supported_efforts(self) -> tuple[str, ...]:
        return ("low", "medium", "high", "xhigh", "max")

    def execute_turn(
        self,
        prompt: str,
        workspace_path: Path,
        context: dict[str, Any] | None = None
    ) -> str:
        """呼叫 claude CLI 進行單回合非互動執行，動態傳遞指定 model。"""
        if not self.is_available():
            raise RuntimeError("系統中未安裝或未在 PATH 中找到 'claude' CLI。")

        cmd = ["claude"]
        if self.current_model:
            cmd.extend(["--model", self.current_model])
        cmd.extend(["--effort", self.current_effort])
        cmd.extend(["-p", prompt])

        proc = self.run_process(
            cmd,
            cwd=str(workspace_path),
            context=context,
            timeout=300,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"claude 執行失敗（代碼 {proc.returncode}）：{proc.stderr}")
        return proc.stdout
