"""受控線上套件 bootstrap 的離線安全測試。"""

import subprocess
from pathlib import Path

import pytest

from isolation.package_installer import (
    REQUIREMENTS_FILE,
    PackageInstallationError,
    install_workspace_packages,
    read_package_requirements,
)


def test_package_requirements_only_accept_pinned_allowlisted_wheels(tmp_path: Path) -> None:
    (tmp_path / REQUIREMENTS_FILE).write_text(
        "Pillow==11.1.0\nMIDIUtil==1.2.1\n",
        encoding="utf-8",
    )

    assert read_package_requirements(tmp_path) == ["Pillow==11.1.0", "MIDIUtil==1.2.1"]


@pytest.mark.parametrize(
    "requirement",
    ["Pillow>=11", "https://example.invalid/package.whl", "torch==2.6.0"],
)
def test_package_requirements_reject_unpinned_or_unapproved_sources(
    tmp_path: Path,
    requirement: str,
) -> None:
    (tmp_path / REQUIREMENTS_FILE).write_text(requirement + "\n", encoding="utf-8")

    with pytest.raises(PackageInstallationError):
        read_package_requirements(tmp_path)


def test_installer_uses_isolated_python_and_explicit_registry(tmp_path, monkeypatch):
    (tmp_path / REQUIREMENTS_FILE).write_text("Pillow==11.1.0\n", encoding="utf-8")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert command[1:4] == ["-I", "-m", "pip"]
        assert command[command.index("--index-url") + 1] == "https://pypi.org/simple"
        assert Path(kwargs["cwd"]) != tmp_path
        assert "PYTHONPATH" not in kwargs["env"]
        return subprocess.CompletedProcess(command, 0, "installed")

    monkeypatch.setattr("isolation.package_installer.subprocess.run", run)
    vendor = install_workspace_packages(tmp_path)
    assert vendor.is_dir()
    assert install_workspace_packages(tmp_path) == vendor
    assert len(calls) == 1
