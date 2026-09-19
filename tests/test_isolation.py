"""安全邊界與隔離層單元測試。"""

from pathlib import Path

from isolation import get_sandbox
from isolation.verifier import verify_sandbox_boundaries


def test_audit_hook_blocks_outside_write(tmp_path: Path):
    """驗證 Audit Hook 能精確阻斷工作區以外的檔案寫入操作。"""
    workspace = tmp_path / "habitat"
    workspace.mkdir()
    outside_file = tmp_path / "outside.txt"

    # 在獨立進程或直接測試驗證器中運行測試
    # 此處驗證 verify_sandbox_boundaries 能夠辨識並阻斷越界行為
    is_secure = verify_sandbox_boundaries(workspace)
    assert is_secure is True
    assert not outside_file.exists()


def test_sandbox_environment_sanitization(tmp_path: Path):
    """驗證沙盒環境變數淨化功能，過濾不安全環境變數並注入工作區路徑。"""
    workspace = tmp_path / "habitat"
    workspace.mkdir()

    sandbox = get_sandbox(workspace)
    env = sandbox.get_isolated_environ()

    assert "EVO_ALLOWED_WORKSPACE" in env
    assert env["EVO_ALLOWED_WORKSPACE"] == str(workspace.resolve())
    assert "PYTHONPATH" in env

