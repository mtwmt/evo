"""安全邊界與原生隔離層測試。"""

import sys
from pathlib import Path

import pytest

from isolation import (
    MacOSSandbox,
    SandboxUnavailableError,
    UnsupportedPlatformSandboxError,
    WindowsSandbox,
    get_sandbox,
)
from isolation.verifier import verify_sandbox_boundaries


def test_native_os_sandbox_blocks_outside_operations(tmp_path: Path):
    """只有不含 audit hook 的原生政策探針全數通過才算安全。"""
    workspace = tmp_path / "habitat"
    workspace.mkdir()

    if sys.platform != "darwin":
        with pytest.raises(UnsupportedPlatformSandboxError):
            verify_sandbox_boundaries(workspace)
        return

    try:
        is_secure = verify_sandbox_boundaries(workspace)
    except SandboxUnavailableError as exc:
        if "外層執行環境拒絕套用 sandbox-exec" in str(exc):
            pytest.skip(f"外層 macOS 執行環境禁止套用 sandbox-exec：{exc}")
        pytest.fail(f"原生沙盒不可用或設定失敗：{exc}")
    assert is_secure is True


def test_verifier_preserves_preexisting_probe_named_files(tmp_path: Path, monkeypatch):
    """驗證清理流程不會覆寫或刪除舊版探針固定名稱的使用者檔案。"""
    workspace = tmp_path / "habitat"
    workspace.mkdir()
    sentinels = {
        "_evo_native_boundary_probe.py": "keep script",
        "_evo_native_boundary_probe.sqlite": "keep database",
    }
    for name, content in sentinels.items():
        (workspace / name).write_text(content, encoding="utf-8")

    class FakeSandbox:
        def build_native_probe_command(self, script: Path):
            return ["unused", str(script)]

        def get_isolated_environ(self):
            return {}

    class Result:
        returncode = 0
        stdout = "EVO_OS_BOUNDARIES_VERIFIED"
        stderr = ""

    monkeypatch.setattr("isolation.verifier.get_sandbox", lambda _: FakeSandbox())
    monkeypatch.setattr("isolation.verifier.subprocess.run", lambda *args, **kwargs: Result())

    assert verify_sandbox_boundaries(workspace) is True
    for name, content in sentinels.items():
        assert (workspace / name).read_text(encoding="utf-8") == content


def test_sandbox_environment_sanitization(tmp_path: Path, monkeypatch):
    """驗證沙盒環境變數淨化功能，過濾不安全環境變數並注入工作區路徑。"""
    workspace = tmp_path / "habitat"
    workspace.mkdir()
    monkeypatch.setenv("PYTHONPATH", "/outside/private-modules")
    monkeypatch.setenv("GEMINI_API_KEY", "test-only-secret")

    sandbox = get_sandbox(workspace)
    env = sandbox.get_isolated_environ()

    assert "EVO_ALLOWED_WORKSPACE" in env
    assert env["EVO_ALLOWED_WORKSPACE"] == str(workspace.resolve())
    assert "PYTHONPATH" not in env
    assert "GEMINI_API_KEY" not in env
    assert env["HOME"] == str(workspace.resolve())
    assert env["TMPDIR"] == str(workspace.resolve())


def test_macos_command_uses_deny_default_native_profile(tmp_path: Path, monkeypatch):
    """macOS 命令必須由原生 deny-default 政策包住 Python。"""
    if sys.platform != "darwin":
        pytest.skip("macOS sandbox-exec profile 僅能在 macOS 驗證")

    workspace = tmp_path / "habitat"
    workspace.mkdir()
    script = workspace / "main.py"
    script.write_text("pass\n", encoding="utf-8")
    sandbox = MacOSSandbox(workspace)
    command = sandbox.build_command(script)
    profile = command[command.index("-p") + 1]
    native_probe = sandbox.build_native_probe_command(script)

    assert command[0].endswith("sandbox-exec")
    assert "(deny default)" in profile
    assert "(allow file-write* (subpath" in profile
    assert "(allow process-exec (literal" in profile
    assert "network" not in profile
    assert "install_audit_hook" in command[-1]
    assert "install_audit_hook" not in native_probe[-1]


def test_windows_sandbox_fails_closed(tmp_path: Path, monkeypatch):
    """未實作原生 Windows 限制時必須拒絕回傳 audit-only 命令。"""
    monkeypatch.setattr("isolation.sys.platform", "win32")
    workspace = tmp_path / "habitat"
    workspace.mkdir()
    script = workspace / "main.py"

    sandbox = get_sandbox(workspace)
    assert isinstance(sandbox, WindowsSandbox)
    with pytest.raises(UnsupportedPlatformSandboxError, match="Windows"):
        sandbox.build_command(script)


def test_unsupported_platform_fails_closed(tmp_path: Path, monkeypatch):
    """Linux 等尚未完成原生隔離的平台不得使用 macOS 沙盒假象。"""
    monkeypatch.setattr("isolation.sys.platform", "linux")
    workspace = tmp_path / "habitat"
    workspace.mkdir()
    sandbox = get_sandbox(workspace)
    with pytest.raises(UnsupportedPlatformSandboxError, match="linux"):
        sandbox.build_command(workspace / "main.py")


def test_script_must_be_inside_workspace(tmp_path: Path):
    """macOS 原生沙盒不接受工作區外的候選入口。"""
    if sys.platform != "darwin":
        pytest.skip("macOS sandbox-exec profile 僅能在 macOS 驗證")
    workspace = tmp_path / "habitat"
    workspace.mkdir()
    outside = tmp_path / "main.py"
    outside.write_text("pass\n", encoding="utf-8")
    with pytest.raises(ValueError, match="工作區"):
        MacOSSandbox(workspace).build_command(outside)
