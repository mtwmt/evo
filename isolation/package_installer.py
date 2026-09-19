"""受控的線上套件 bootstrap；世界主程序本身始終沒有網路權限。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REQUIREMENTS_FILE = "sandbox-requirements.txt"
VENDOR_DIRECTORY = ".evo-packages"
# 僅保留可用於離線媒體創作的純 Python / binary-wheel 套件；不可由候選任意擴張。
ALLOWED_PACKAGES = frozenset({"pillow", "mido", "midiutil", "numpy", "networkx"})
_PINNED_REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9_.!+~-]*)$")


class PackageInstallationError(RuntimeError):
    """受控套件安裝未能安全完成。"""


def _normalise_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_package_requirements(workspace_path: Path) -> list[str]:
    """讀取嚴格 version-pinned、白名單中的需求；拒絕 URL、選項與未鎖版套件。"""
    workspace = Path(workspace_path).resolve()
    requirements_path = workspace / REQUIREMENTS_FILE
    if not requirements_path.exists():
        return []
    if requirements_path.is_symlink() or not requirements_path.is_file():
        raise PackageInstallationError("套件需求檔必須是工作區內的一般檔案。")

    requirements: list[str] = []
    for raw_line in requirements_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        match = _PINNED_REQUIREMENT.fullmatch(line)
        if match is None:
            raise PackageInstallationError(
                "套件需求必須是 `名稱==精確版本`，不允許 URL、選項或未鎖定版本。"
            )
        if _normalise_package_name(match.group(1)) not in ALLOWED_PACKAGES:
            raise PackageInstallationError(f"套件 {match.group(1)!r} 不在受控白名單內。")
        requirements.append(line)
    return requirements


def install_workspace_packages(workspace_path: Path, timeout_seconds: float = 120.0) -> Path | None:
    """在沙盒外的短暫 bootstrap 階段安裝核准 wheels，之後世界離線執行。

    只允許 PyPI 的二進位 wheel，禁止依賴遞迴、建置 source distribution 與互動輸入。
    """
    workspace = Path(workspace_path).resolve()
    requirements = read_package_requirements(workspace)
    if not requirements:
        return None

    digest = hashlib.sha256("\n".join(requirements).encode("utf-8")).hexdigest()
    vendor_dir = workspace / VENDOR_DIRECTORY
    marker = vendor_dir / ".evo-install.json"
    if vendor_dir.exists() and (vendor_dir.is_symlink() or not vendor_dir.is_dir()):
        raise PackageInstallationError("受控套件目錄必須是工作區內非符號連結目錄。")
    if marker.is_file():
        try:
            if json.loads(marker.read_text(encoding="utf-8")).get("requirements_sha256") == digest:
                return vendor_dir
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    stage_dir = Path(tempfile.mkdtemp(prefix=".evo-packages-stage-", dir=workspace))
    bootstrap_home = Path(tempfile.mkdtemp(prefix=".evo-packages-home-", dir=workspace))
    backup_dir = workspace / f"{VENDOR_DIRECTORY}.previous"
    moved_previous_vendor = False
    try:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(bootstrap_home),
            "TMPDIR": str(bootstrap_home),
            "PYTHONNOUSERSITE": "1",
        }
        command = [
            sys.executable,
            "-I",
            "-m",
            "pip",
            "install",
            "--isolated",
            "--disable-pip-version-check",
            "--no-input",
            "--no-deps",
            "--only-binary=:all:",
            "--index-url",
            "https://pypi.org/simple",
            "--target",
            str(stage_dir),
            *requirements,
        ]
        result = subprocess.run(
            command,
            cwd=str(bootstrap_home),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        if result.returncode != 0:
            raise PackageInstallationError(f"受控套件安裝失敗：{result.stdout[-4000:]}")
        (stage_dir / ".evo-install.json").write_text(
            json.dumps({"requirements_sha256": digest, "requirements": requirements}, indent=2),
            encoding="utf-8",
        )
        if backup_dir.exists() and (backup_dir.is_symlink() or not backup_dir.is_dir()):
            raise PackageInstallationError("受控套件備份目錄狀態無效。")
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        if vendor_dir.exists():
            vendor_dir.replace(backup_dir)
            moved_previous_vendor = True
        try:
            stage_dir.replace(vendor_dir)
        except OSError:
            if moved_previous_vendor and backup_dir.exists() and not vendor_dir.exists():
                backup_dir.replace(vendor_dir)
            raise
        shutil.rmtree(backup_dir, ignore_errors=True)
        return vendor_dir
    except (OSError, subprocess.SubprocessError) as exc:
        raise PackageInstallationError(f"無法完成受控套件安裝：{exc}") from exc
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)
        shutil.rmtree(bootstrap_home, ignore_errors=True)
