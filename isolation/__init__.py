"""安全隔離模組初始化。"""

import sys
from pathlib import Path

from isolation.common.sandbox import (
    BaseSandbox,
    SandboxUnavailableError,
    UnsupportedPlatformSandboxError,
)
from isolation.macos.sandbox import MacOSSandbox
from isolation.windows.sandbox import WindowsSandbox


class UnsupportedSandbox(BaseSandbox):
    """未實作原生隔離的平台一律拒絕執行。"""

    def build_command(self, script_path: Path, args: list[str] | None = None) -> list[str]:
        """明確拒絕未受支援的平台。"""
        raise UnsupportedPlatformSandboxError(
            f"平台 {sys.platform!r} 尚未實作可驗證的原生 OS 沙盒；拒絕執行。"
        )


def get_sandbox(workspace_path: Path) -> BaseSandbox:
    """依據當前作業系統回傳對應的沙盒實例。"""
    if sys.platform == "win32":
        return WindowsSandbox(workspace_path)
    if sys.platform == "darwin":
        return MacOSSandbox(workspace_path)
    return UnsupportedSandbox(workspace_path)


__all__ = [
    "BaseSandbox",
    "MacOSSandbox",
    "SandboxUnavailableError",
    "UnsupportedPlatformSandboxError",
    "UnsupportedSandbox",
    "WindowsSandbox",
    "get_sandbox",
]
