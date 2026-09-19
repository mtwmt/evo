"""CLI 適配器抽象基底介面。"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class BaseCLIAdapter(ABC):
    """LLM CLI 適配器抽象介面：支援動態模型探索與自訂最新模型切換，拒絕死板 Hardcode。"""

    def __init__(self, name: str, default_model: str = ""):
        self.name = name
        self.current_model = default_model
        self.current_effort = ""

    @property
    def supported_efforts(self) -> tuple[str, ...]:
        """回傳 CLI 支援的推理強度；空值代表不支援。"""
        return ()

    @abstractmethod
    def is_available(self) -> bool:
        """檢查系統 PATH 中是否存在該 CLI 可執行檔。"""
        pass

    @abstractmethod
    def fetch_available_models(self) -> list[str]:
        """動態向 CLI 或本機環境探索最新可用之模型清單。"""
        pass

    @property
    def available_models(self) -> list[str]:
        """相容屬性：動態探索並回傳可用模型清單。"""
        return self.fetch_available_models()

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
            workspace_path: 供 AI 讀寫修改的候選工作區目錄。
            context: 額外環境資訊字典。

        回傳：
            CLI 輸出之文字或日誌內容。
        """
        pass
