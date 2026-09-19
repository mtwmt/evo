"""macOS 原生沙盒實作，以 sandbox-exec 提供作業系統級限制。"""

import json
import shutil
import site
import subprocess
import sys
from pathlib import Path

from isolation.common.sandbox import BaseSandbox, SandboxUnavailableError
from isolation.package_installer import VENDOR_DIRECTORY

_RUNTIME_PATH_CACHE: dict[tuple[str, str, str, tuple[str, ...]], set[Path]] = {}


class MacOSSandbox(BaseSandbox):
    """使用 deny-default Seatbelt 政策執行候選程式。"""

    def __init__(self, workspace_path: Path):
        super().__init__(workspace_path)
        if sys.platform != "darwin":
            raise SandboxUnavailableError("macOS sandbox-exec 只能在 macOS 使用。")
        self._sandbox_exec = shutil.which("sandbox-exec")
        if not self._sandbox_exec:
            raise SandboxUnavailableError(
                "找不到 sandbox-exec；拒絕以 audit hook 代替原生 OS 隔離。"
            )

    @staticmethod
    def _profile_path(path: Path) -> str:
        """將受信任的原生路徑安全編碼成 Seatbelt 字串。"""
        return json.dumps(str(path.resolve()))

    @staticmethod
    def _profile_literal(path: Path) -> str:
        """保留執行檔 symlink 路徑，供 Seatbelt 比對 execvp 請求。"""
        return json.dumps(str(path.absolute()))

    def _runtime_read_paths(self) -> set[Path]:
        """收集 Python 標準庫與其原生模組所需的唯讀路徑。"""
        cache_key = (
            sys.executable,
            sys.prefix,
            sys.base_prefix,
            tuple(sys.path),
        )
        cached_paths = _RUNTIME_PATH_CACHE.get(cache_key)
        if cached_paths is not None:
            return set(cached_paths)

        paths = {
            Path(sys.executable).resolve().parent,
            Path(sys.prefix).resolve(),
            Path(sys.base_prefix).resolve(),
            (Path(__file__).resolve().parent.parent / "common").resolve(),
        }
        package_roots = [Path(root).resolve() for root in site.getsitepackages()]
        runtime_roots = [Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(), *package_roots]
        for entry in sys.path:
            if not entry:
                continue
            resolved = Path(entry).resolve()
            if any(resolved == root or resolved.is_relative_to(root) for root in runtime_roots):
                paths.add(resolved)

        # 為標準庫原生擴充允許其明確連結的唯讀動態函式庫目錄。
        otool = shutil.which("otool")
        extension_dirs = {
            Path(entry).resolve()
            for entry in sys.path
            if (
                entry
                and Path(entry).is_dir()
                and Path(entry).name == "lib-dynload"
                and any(
                    Path(entry).resolve().is_relative_to(root)
                    for root in runtime_roots
                )
            )
        }
        if otool:
            for extension_dir in extension_dirs:
                for extension in extension_dir.glob("*.so"):
                    try:
                        result = subprocess.run(
                            [otool, "-L", str(extension)],
                            capture_output=True,
                            text=True,
                            timeout=5,
                            check=False,
                        )
                    except (OSError, subprocess.TimeoutExpired):
                        continue
                    if result.returncode != 0:
                        continue
                    for line in result.stdout.splitlines()[1:]:
                        dependency = line.strip().split(" (", 1)[0]
                        if dependency.startswith("/"):
                            dependency_path = Path(dependency)
                            paths.add(dependency_path.resolve().parent)

        # macOS 系統函式庫與框架是 Python 執行時的唯讀依賴。
        paths.update(
            Path(path)
            for path in (
                "/System/Library",
                "/usr/lib",
                "/Library/Apple/System/Library",
            )
            if Path(path).exists()
        )
        _RUNTIME_PATH_CACHE[cache_key] = set(paths)
        return set(paths)

    @staticmethod
    def _runtime_module_paths() -> list[str]:
        """保留 venv 標準庫與 site-packages 路徑，排除專案及使用者路徑。"""
        package_roots = [Path(root).resolve() for root in site.getsitepackages()]
        runtime_roots = [Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(), *package_roots]
        result = []
        for entry in sys.path:
            if not entry:
                continue
            resolved = Path(entry).resolve()
            if any(resolved == root or resolved.is_relative_to(root) for root in runtime_roots):
                result.append(entry)
        return result

    def build_profile(self) -> str:
        """建立僅允許工作區寫入、禁止網路與一般子程序的原生政策。"""
        if self.workspace_path == Path("/"):
            raise SandboxUnavailableError("不能將檔案系統根目錄設為沙盒工作區。")
        workspace = self._profile_path(self.workspace_path)
        readable_paths = sorted(self._runtime_read_paths(), key=str)

        rules = [
            "(version 1)",
            "(deny default)",
            # dyld 會以 openat 的根目錄描述符解析載入路徑。
            '(allow file-read* file-test-existence (literal "/"))',
            f"(allow file-read* (subpath {workspace}))",
            f"(allow file-test-existence (subpath {workspace}))",
            f"(allow file-write* (subpath {workspace}))",
            "(allow sysctl-read)",
        ]
        executable_paths = {Path(sys.executable).resolve()}
        rules.extend(
            f"(allow process-exec (literal {self._profile_literal(path)}))"
            for path in sorted(executable_paths, key=str)
        )
        rules.extend(
            f"(allow file-read* file-test-existence (subpath {self._profile_path(path)}))"
            for path in readable_paths
        )
        rules.extend(
            f"(allow file-map-executable (subpath {self._profile_path(path)}))"
            for path in readable_paths
        )
        metadata_paths = {self.workspace_path, *readable_paths, *executable_paths}
        for path in tuple(metadata_paths):
            metadata_paths.update(path.absolute().parents)
        rules.extend(
            f"(allow file-read-metadata (literal {self._profile_literal(path)}))"
            for path in sorted(metadata_paths, key=str)
        )
        return "\n".join(rules)

    def _build_command(
        self,
        script_path: Path,
        args: list[str] | None = None,
        *,
        install_audit_hook: bool,
    ) -> list[str]:
        script_resolved = Path(script_path).resolve()
        if not script_resolved.is_relative_to(self.workspace_path):
            raise ValueError("沙盒入口程式必須位於指定工作區內。")

        hook_path = (Path(__file__).resolve().parent.parent / "common" / "audit_hook.py").resolve()
        vendor_path = self.workspace_path / VENDOR_DIRECTORY
        if vendor_path.exists() and (vendor_path.is_symlink() or not vendor_path.is_dir()):
            raise SandboxUnavailableError("受控套件目錄必須是工作區內非符號連結目錄。")
        runtime_paths = [str(self.workspace_path)]
        if vendor_path.is_dir():
            runtime_paths.append(str(vendor_path))
        runner_parts = ["import runpy, sys"]
        runner_parts.extend(
            [
                f"sys.prefix = {sys.prefix!r}",
                f"sys.exec_prefix = {sys.exec_prefix!r}",
                f"sys.base_prefix = {sys.base_prefix!r}",
                f"sys.base_exec_prefix = {sys.base_exec_prefix!r}",
                f"sys.path = {runtime_paths!r} + {self._runtime_module_paths()!r}",
            ]
        )
        if install_audit_hook:
            runner_parts.extend(
                [
                    f"hook = runpy.run_path({str(hook_path)!r})['install_audit_hook']",
                    f"hook({str(self.workspace_path)!r})",
                ]
            )
        runner_parts.extend(
            [
                "print('EVO_NATIVE_SANDBOX_ACTIVE', flush=True)",
                f"sys.path.insert(0, {str(self.workspace_path)!r})",
                f"sys.argv = [{str(script_resolved)!r}] + {args or []!r}",
                f"runpy.run_path({str(script_resolved)!r}, run_name='__main__')",
            ]
        )
        inline_runner = "; ".join(runner_parts)
        return [
            str(self._sandbox_exec),
            "-p",
            self.build_profile(),
            str(Path(sys.executable).resolve()),
            "-I",
            "-S",
            "-c",
            inline_runner,
        ]

    def build_command(self, script_path: Path, args: list[str] | None = None) -> list[str]:
        """透過原生 OS 政策啟動 Python，audit hook 僅作補強。"""
        return self._build_command(script_path, args, install_audit_hook=True)

    def build_native_probe_command(self, script_path: Path) -> list[str]:
        """建立只驗證 OS 政策的探針命令，避免 audit hook 造成假陽性。"""
        return self._build_command(script_path, install_audit_hook=False)
