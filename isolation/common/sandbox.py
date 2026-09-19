"""提供 habitat 代碼隔離執行的抽象沙盒基底類別。"""

import os
from abc import ABC, abstractmethod
from pathlib import Path


class BaseSandbox(ABC):
    """抽象沙盒環境：提供進程隔離、乾淨環境變數與安全檢查機制。"""

    def __init__(self, workspace_path: Path):
        self.workspace_path = Path(workspace_path).resolve()

    def get_isolated_environ(self) -> dict[str, str]:
        """建立乾淨的環境變數字典，過濾敏感金鑰與憑證。"""
        allowed_vars = {
            "PATH",
            "PYTHONPATH",
            "HOME",
            "LANG",
            "LC_ALL",
            "TERM",
            "SYSTEMROOT",  # Windows 系統必需變數
            "COMSPEC",     # Windows 系統必需變數
        }
        env = {k: v for k, v in os.environ.items() if k in allowed_vars}

        # 注入 audit hook 所需的工作空間路徑
        env["EVO_ALLOWED_WORKSPACE"] = str(self.workspace_path)

        # 將專案根目錄加入 PYTHONPATH，使子進程能引入必要模組
        project_root = Path(__file__).resolve().parent.parent.parent
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{project_root}:{existing_pythonpath}".strip(":")

        return env

    @abstractmethod
    def build_command(self, script_path: Path, args: list[str] | None = None) -> list[str]:
        """組裝在沙盒中執行目標腳本的命令陣列。"""
        pass
