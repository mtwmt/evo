"""Tests for the standalone macOS CLI filesystem sandbox."""

import subprocess
import sys
from pathlib import Path

import pytest

from isolation.common.sandbox import SandboxUnavailableError
from isolation.macos.cli_sandbox import MacOSCLISandbox


def make_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MacOSCLISandbox:
    workspace = tmp_path / "habitat"
    workspace.mkdir()
    executable = tmp_path / "agy"
    executable.write_bytes(b"test binary")
    executable.chmod(0o755)
    monkeypatch.setattr("isolation.macos.cli_sandbox.sys.platform", "darwin")
    monkeypatch.setattr("isolation.macos.cli_sandbox.shutil.which", lambda _: "/usr/bin/sandbox-exec")
    monkeypatch.setattr(
        "isolation.macos.cli_sandbox.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, f"{executable.resolve()}:\n", ""
        ),
    )
    return MacOSCLISandbox(workspace, executable)


def test_profile_is_deny_default_workspace_only_and_network_free(tmp_path, monkeypatch):
    sandbox = make_sandbox(tmp_path, monkeypatch)
    profile = sandbox.build_profile()

    assert "(deny default)" in profile
    assert f'(allow file-write* (subpath "{sandbox.workspace_path}")' in profile
    assert "process-exec" in profile
    assert "network" not in profile
    assert "/Users/" not in profile


def test_command_requires_the_verified_executable(tmp_path, monkeypatch):
    sandbox = make_sandbox(tmp_path, monkeypatch)
    command = sandbox.build_command([str(sandbox.executable_path), "--version"])
    assert command[0] == "/usr/bin/sandbox-exec"
    assert command[-2:] == [str(sandbox.executable_path), "--version"]
    with pytest.raises(ValueError, match="不符"):
        sandbox.build_command(["/bin/sh", "-c", "true"])


def test_environment_has_only_habitat_local_state(tmp_path, monkeypatch):
    sandbox = make_sandbox(tmp_path, monkeypatch)
    environment = sandbox.get_isolated_environ()

    assert set(environment) == {
        "HOME", "TMPDIR", "TMP", "TEMP", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
        "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR", "LANG",
    }
    for key, value in environment.items():
        if key != "LANG":
            assert value == str(sandbox.workspace_path)
    assert "PYTHONPATH" not in environment


def test_rejects_shell_script_instead_of_mach_o(tmp_path, monkeypatch):
    workspace = tmp_path / "habitat"
    workspace.mkdir()
    executable = tmp_path / "agy"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr("isolation.macos.cli_sandbox.sys.platform", "darwin")
    monkeypatch.setattr("isolation.macos.cli_sandbox.shutil.which", lambda _: "/usr/bin/sandbox-exec")
    monkeypatch.setattr(
        "isolation.macos.cli_sandbox.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "not Mach-O"),
    )

    with pytest.raises(SandboxUnavailableError, match="Mach-O"):
        MacOSCLISandbox(workspace, executable)


@pytest.mark.skipif(sys.platform != "darwin", reason="native Seatbelt is macOS-only")
def test_real_standalone_binary_can_only_write_inside_workspace(tmp_path):
    compiler = "/usr/bin/cc"
    if not Path(compiler).exists():
        pytest.skip("Apple C compiler is not installed")
    workspace = tmp_path / "habitat"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside sentinel", encoding="utf-8")
    source = tmp_path / "probe.c"
    executable = tmp_path / "probe"
    inside = workspace / "inside.txt"
    source.write_text(
        "#include <errno.h>\n"
        "#include <stdio.h>\n"
        "int main(int argc, char **argv) {\n"
        "  FILE *r = fopen(argv[1], \"r\"); if (r) return 10;\n"
        "  if (errno != EPERM && errno != EACCES) return 13;\n"
        "  FILE *w = fopen(argv[2], \"w\"); if (w) return 11;\n"
        "  if (errno != EPERM && errno != EACCES) return 14;\n"
        "  FILE *i = fopen(argv[3], \"w\"); if (!i) return 12;\n"
        "  fputs(\"inside\", i); fclose(i); return 0;\n}\n",
        encoding="utf-8",
    )
    compiled = subprocess.run([compiler, str(source), "-o", str(executable)], capture_output=True)
    if compiled.returncode:
        pytest.skip(f"C fixture compilation failed: {compiled.stderr.decode(errors='replace')}")

    sandbox = MacOSCLISandbox(workspace, executable)
    result = subprocess.run(
        sandbox.build_command([str(executable), str(outside), str(outside), str(inside)]),
        cwd=workspace,
        env=sandbox.get_isolated_environ(),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode not in (0, 10, 11, 12, 13, 14) and "sandbox_apply" in result.stderr:
        pytest.skip(f"outer environment denies Seatbelt: {result.stderr}")
    assert result.returncode == 0, result.stderr
    assert outside.read_text(encoding="utf-8") == "outside sentinel"
    assert inside.read_text(encoding="utf-8") == "inside"
