"""以 deny-default Seatbelt 限制獨立 macOS CLI 執行檔。"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from isolation.common.sandbox import SandboxUnavailableError


class MacOSCLISandbox:
    """在工作區邊界內執行一個已解析的獨立 Mach-O 執行檔。"""

    def __init__(self, workspace_path: Path, executable_path: str | Path):
        if sys.platform != "darwin":
            raise SandboxUnavailableError("macOS CLI 沙盒只能在 macOS 使用。")
        self.workspace_path = Path(workspace_path).resolve()
        if self.workspace_path == Path("/") or not self.workspace_path.is_dir():
            raise SandboxUnavailableError("CLI 沙盒工作區必須是已存在且非根目錄的目錄。")

        sandbox_exec = shutil.which("sandbox-exec")
        if not sandbox_exec:
            raise SandboxUnavailableError("找不到 sandbox-exec；拒絕直接執行 CLI。")
        self._sandbox_exec = str(Path(sandbox_exec).resolve())

        executable = Path(executable_path).expanduser()
        if not executable.is_absolute():
            raise SandboxUnavailableError("CLI 執行檔必須以絕對路徑指定。")
        try:
            self.executable_path = executable.resolve(strict=True)
            executable_metadata = self.executable_path.stat()
        except OSError as exc:
            raise SandboxUnavailableError(f"無法解析 CLI 執行檔：{exc}") from exc
        if (
            not self.executable_path.is_file()
            or not executable_metadata.st_mode & 0o111
            or not os.access(self.executable_path, os.X_OK)
        ):
            raise SandboxUnavailableError("CLI 必須是可執行的一般檔案。")
        if self.executable_path.is_relative_to(self.workspace_path):
            raise SandboxUnavailableError("CLI 執行檔不可位於可寫入的工作區內。")

        self._dependencies = self._inspect_macho_dependencies()
        if any(path.is_relative_to(self.workspace_path) for path in self._dependencies):
            raise SandboxUnavailableError("CLI 相依函式庫不可位於可寫入的工作區內。")

    def _inspect_macho_dependencies(self) -> set[Path]:
        """使用 Apple otool 檢查 Mach-O，拒絕包裝器與相對載入路徑。"""
        otool = Path("/usr/bin/otool")
        if not otool.is_file():
            raise SandboxUnavailableError("找不到系統 otool；無法驗證 CLI 的 Mach-O 依賴。")
        try:
            result = subprocess.run(
                [str(otool), "-L", str(self.executable_path)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SandboxUnavailableError(f"無法檢查 CLI 的 Mach-O 依賴：{exc}") from exc
        if result.returncode != 0 or not result.stdout.startswith(str(self.executable_path) + ":"):
            raise SandboxUnavailableError(
                "CLI 不是可驗證的獨立 Mach-O 執行檔；shell/Node 包裝器目前不支援。"
            )

        dependencies: set[Path] = set()
        for line in result.stdout.splitlines()[1:]:
            match = re.match(r"^\s*(\S+)\s+\(compatibility version ", line)
            if not match:
                continue
            raw_path = match.group(1)
            if not raw_path.startswith("/"):
                raise SandboxUnavailableError(
                    f"CLI 含未支援的相對 Mach-O 依賴路徑：{raw_path}"
                )
            dependency = Path(raw_path)
            try:
                try:
                    resolved = dependency.resolve(strict=True)
                    if not resolved.is_file():
                        raise OSError("dependency is not a regular file")
                except FileNotFoundError:
                    # 現代 macOS 透過 dyld shared cache 提供部分 /usr/lib 路徑。
                    # 只保留 otool 明確列出的單一 dylib literal，不放寬目錄權限。
                    if not raw_path.startswith("/usr/lib/") or not raw_path.endswith(".dylib"):
                        raise
                    resolved = dependency
            except OSError as exc:
                raise SandboxUnavailableError(
                    f"無法安全解析 CLI 依賴 {raw_path}: {exc}"
                ) from exc
            dependencies.add(resolved)
        return dependencies

    @staticmethod
    def _literal(path: Path) -> str:
        return json.dumps(str(path))

    def build_profile(self) -> str:
        """建立僅允許工作區寫入、精確執行檔讀取且不開放網路的政策。"""
        readable_files = {self.executable_path, *self._dependencies}
        workspace = self._literal(self.workspace_path)
        rules = [
            "(version 1)",
            "(deny default)",
            '(allow file-read* file-test-existence (literal "/"))',
            f"(allow file-read* file-test-existence (subpath {workspace}))",
            f"(allow file-write* (subpath {workspace}))",
            f"(allow process-exec (literal {self._literal(self.executable_path)}))",
            "(allow sysctl-read)",
        ]
        for path in sorted(readable_files, key=str):
            literal = self._literal(path)
            rules.append(f"(allow file-read* file-test-existence (literal {literal}))")
            rules.append(f"(allow file-map-executable (literal {literal}))")

        metadata_paths = {self.workspace_path}
        for path in readable_files | {Path(self._sandbox_exec)}:
            metadata_paths.update(path.absolute().parents)
        rules.extend(
            f"(allow file-read-metadata (literal {self._literal(path)}))"
            for path in sorted(metadata_paths, key=str)
        )
        return "\n".join(rules)

    def build_command(self, args: list[str]) -> list[str]:
        """包裝指定執行檔的 argv，並拒絕切換成其他執行檔。"""
        if not args:
            raise ValueError("CLI 命令不可為空。")
        argv = list(args)
        supplied_executable = Path(argv[0]).resolve()
        if supplied_executable != self.executable_path:
            raise ValueError("命令執行檔與已驗證的 CLI 不符。")
        return [
            self._sandbox_exec,
            "-p",
            self.build_profile(),
            str(self.executable_path),
            *argv[1:],
        ]

    def get_isolated_environ(self) -> dict[str, str]:
        """提供工作區內的狀態路徑，不繼承主機環境設定。"""
        home = str(self.workspace_path)
        return {
            "HOME": home,
            "TMPDIR": home,
            "TMP": home,
            "TEMP": home,
            "XDG_CONFIG_HOME": home,
            "XDG_CACHE_HOME": home,
            "XDG_DATA_HOME": home,
            "XDG_STATE_HOME": home,
            "XDG_RUNTIME_DIR": home,
            "LANG": "C.UTF-8",
        }
