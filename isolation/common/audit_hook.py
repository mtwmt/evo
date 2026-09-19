"""Python sys.addaudithook 安全隔離機制。

攔截宇宙代碼可能產生的越界檔案寫入、未授權外部進程啟動及外部網路連線。
"""

import os
import sys
from pathlib import Path


class SecurityViolation(PermissionError):
    """當 habitat 內代碼違反 Evo 安全邊界時拋出。"""
    pass


def install_audit_hook(allowed_workspace: Path | str) -> None:
    """安裝 audit hook 限制檔案寫入與系統呼叫。

    參數：
        allowed_workspace: 允許進行讀寫的目錄路徑（habitat 或 habitat_staging）。
    """
    workspace_path = Path(allowed_workspace).resolve()

    def audit_hook(event: str, args: tuple) -> None:
        # 1. 檔案寫入與修改邊界檢查
        if event == "open":
            # 參數格式：(file, mode, flags)
            file_path, mode, _ = args
            # 若為寫入、附加、覆寫等修改模式
            if isinstance(mode, str) and any(m in mode for m in ("w", "a", "+", "x")):
                if not isinstance(file_path, (str, bytes, os.PathLike)):
                    # 描述符形式的 open 無法由 hook 推回路徑，交由原生 OS 沙盒限制。
                    return
                try:
                    resolved_file = Path(file_path).resolve()
                    # 檢查目標路徑是否位於允許的工作空間之內
                    if not resolved_file.is_relative_to(workspace_path):
                        raise SecurityViolation(
                            f"Evo 安全邊界阻斷：禁止寫入工作區以外的路徑 '{file_path}'（允許工作區：'{workspace_path}'）"
                        )
                except (ValueError, RuntimeError):
                    raise SecurityViolation(
                        f"Evo 安全邊界阻斷：無效的檔案路徑 '{file_path}'"
                    )

        # 2. 阻斷從宇宙代碼內直接執行 shell 或啟動未授權子進程
        elif event in ("subprocess.Popen", "os.system", "os.spawn", "os.posix_spawn"):
            raise SecurityViolation(
                f"Evo 安全邊界阻斷：禁止未授權進程執行操作：{event}"
            )

        # 3. 阻斷任何外部網路連線行為
        elif event == "socket.connect":
            raise SecurityViolation(
                f"Evo 安全邊界阻斷：禁止發起外部網路連線：{event}"
            )

    sys.addaudithook(audit_hook)


if __name__ == "__main__":
    # 若作為啟動腳本直接運行，由環境變數讀取工作區
    workspace_env = os.environ.get("EVO_ALLOWED_WORKSPACE")
    if workspace_env:
        install_audit_hook(workspace_env)
