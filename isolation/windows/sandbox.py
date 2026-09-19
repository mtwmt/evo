"""Windows 平台專屬沙盒實作。"""

from pathlib import Path

from isolation.common.sandbox import BaseSandbox, UnsupportedPlatformSandboxError


class WindowsSandbox(BaseSandbox):
    """尚未提供原生 Windows 沙盒，因此所有執行請求均明確拒絕。"""

    def build_command(self, script_path: Path, args: list[str] | None = None) -> list[str]:
        """在未實作原生限制時 fail closed，不退回 Python audit hook。"""
        raise UnsupportedPlatformSandboxError(
            "Windows 尚未實作可驗證的原生 OS 沙盒；拒絕執行候選程式。"
        )
