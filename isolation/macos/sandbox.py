"""macOS 平台專屬沙盒實作。"""

import sys
from pathlib import Path

from isolation.common.sandbox import BaseSandbox


class MacOSSandbox(BaseSandbox):
    """專為 macOS 環境設計的沙盒，在啟動時自動掛載 Python Audit Hook。"""

    def build_command(self, script_path: Path, args: list[str] | None = None) -> list[str]:
        """組裝隔離執行命令，於 Python 啟動時先注入 Audit Hook 再載入目標代碼。"""
        script_resolved = Path(script_path).resolve()
        hook_path = (Path(__file__).resolve().parent.parent / "common" / "audit_hook.py").resolve()

        # 內聯引導程式：先載入 audit hook 防護，再執行 main.py
        inline_runner = (
            f"import sys; "
            f"sys.path.insert(0, '{hook_path.parent.parent.parent}'); "
            f"from isolation.common.audit_hook import install_audit_hook; "
            f"install_audit_hook('{self.workspace_path}'); "
            f"import runpy; "
            f"sys.argv = ['{script_resolved}'] + {args or []}; "
            f"runpy.run_path('{script_resolved}', run_name='__main__')"
        )

        python_bin = sys.executable
        return [python_bin, "-c", inline_runner]
