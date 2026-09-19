"""安全隔離模組初始化。"""

import sys
from pathlib import Path

from isolation.common.sandbox import BaseSandbox
from isolation.macos.sandbox import MacOSSandbox
from isolation.windows.sandbox import WindowsSandbox


def get_sandbox(workspace_path: Path) -> BaseSandbox:
    """依據當前作業系統回傳對應的沙盒實例。"""
    if sys.platform == "win32":
        return WindowsSandbox(workspace_path)
    # 預設使用 Unix / macOS 沙盒
    return MacOSSandbox(workspace_path)


__all__ = ["BaseSandbox", "MacOSSandbox", "WindowsSandbox", "get_sandbox"]
