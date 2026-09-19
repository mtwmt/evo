"""審查門神（Guardian）驗收功能單元測試。"""

import importlib.util
import sys
from pathlib import Path

import pytest

from core.review.guardian import Guardian


def run_smoke_or_skip_outer_sandbox_limit(guardian: Guardian, candidate_dir: Path, timeout: float):
    """外層 macOS 權限拒絕 sandbox_apply 時，標記原生整合測試不可執行。"""
    result = guardian.run_smoke_test(candidate_dir, timeout_seconds=timeout)
    if (
        sys.platform == "darwin"
        and result.step == "sandbox_setup"
        and "sandbox_apply: Operation not permitted" in str(result.error_message)
    ):
        pytest.skip("外層執行環境禁止 sandbox-exec；需在具原生沙盒權限的環境驗證")
    return result


def test_guardian_catches_syntax_error(tmp_path: Path):
    """驗證 Guardian 在靜態分析階段能精確捕捉語法錯誤並拒絕候選版本。"""
    candidate_dir = tmp_path / "candidate"
    candidate_dir.mkdir()

    # 寫入包含嚴重語法錯誤的程式碼
    bad_code = "def broken_func(:\n    print('error')"
    (candidate_dir / "main.py").write_text(bad_code, encoding="utf-8")

    guardian = Guardian()
    res = guardian.run_static_analysis(candidate_dir)

    assert res.passed is False
    assert res.step == "static_analysis"
    assert "Ruff" in str(res.error_message) or "error" in str(res.error_message).lower()


def test_candidate_cannot_disable_external_static_review(tmp_path: Path):
    (tmp_path / "ruff.toml").write_text('exclude = ["*.py"]\n', encoding="utf-8")
    (tmp_path / "main.py").write_text("missing_name()  # noqa\n", encoding="utf-8")
    result = Guardian().run_static_analysis(tmp_path)
    assert not result.passed
    assert "F821" in result.error_message


def test_guardian_catches_crashing_main(tmp_path: Path):
    """驗證 Guardian 在冒煙測試階段能攔截執行時拋出例外並崩潰的代碼。"""
    candidate_dir = tmp_path / "candidate"
    candidate_dir.mkdir()

    crashing_code = """
import sys
raise RuntimeError("模擬宇宙啟動時崩潰")
"""
    (candidate_dir / "main.py").write_text(crashing_code, encoding="utf-8")

    guardian = Guardian()
    res = run_smoke_or_skip_outer_sandbox_limit(guardian, candidate_dir, timeout=2.0)

    assert res.passed is False
    assert res.step == "smoke_test"
    assert "崩潰" in str(res.error_message) or "crashed" in str(res.error_message).lower()


def test_guardian_rejects_early_successful_exit(tmp_path: Path):
    """候選即使以 exit code 0 結束，也不能被當成持續運作的宇宙。"""
    candidate_dir = tmp_path / "candidate"
    candidate_dir.mkdir()
    (candidate_dir / "main.py").write_text(
        "from pathlib import Path\nPath('habitat.db').touch()\n",
        encoding="utf-8",
    )

    res = run_smoke_or_skip_outer_sandbox_limit(Guardian(), candidate_dir, timeout=0.2)

    assert res.passed is False
    assert "提早結束" in str(res.error_message)


def test_guardian_passes_valid_candidate(tmp_path: Path):
    """驗證健康 SQLite、場景資料及候選同目錄模組能順利通過冒煙測試。"""
    candidate_dir = tmp_path / "candidate"
    candidate_dir.mkdir()
    (candidate_dir / "helper.py").write_text("VALUE = 'loaded'\n", encoding="utf-8")

    optional_imports = []
    for module_name in ("numpy", "networkx"):
        if importlib.util.find_spec(module_name) is not None:
            optional_imports.append(f"import {module_name}")

    valid_code = f"""
import json
import sqlite3
import time
from pathlib import Path
import helper
{chr(10).join(optional_imports)}

db = Path(__file__).parent / "habitat.db"
with sqlite3.connect(db) as conn:
    conn.execute("CREATE TABLE scene_primitives (id TEXT PRIMARY KEY, json_data TEXT)")
    scene = {{"entities": [], "metrics": {{"tick": 1}}}}
    conn.execute(
        "INSERT INTO scene_primitives VALUES ('current_scene', ?)",
        (json.dumps(scene),),
    )
    conn.commit()

assert helper.VALUE == "loaded"
while True:
    time.sleep(0.1)
"""
    (candidate_dir / "main.py").write_text(valid_code, encoding="utf-8")

    guardian = Guardian()
    res = run_smoke_or_skip_outer_sandbox_limit(guardian, candidate_dir, timeout=0.5)

    assert res.passed is True
    assert res.step == "smoke_test"


def test_guardian_rejects_database_without_observation_data(tmp_path: Path):
    """SQLite 可連線但缺少完整場景觀測資料時，候選仍須失敗。"""
    candidate_dir = tmp_path / "candidate"
    candidate_dir.mkdir()
    (candidate_dir / "main.py").write_text(
        """
import sqlite3
import time
from pathlib import Path

with sqlite3.connect(Path(__file__).parent / 'habitat.db') as connection:
    connection.execute('CREATE TABLE scene_primitives (id TEXT, json_data TEXT)')
    connection.commit()
while True:
    time.sleep(0.1)
""",
        encoding="utf-8",
    )

    result = run_smoke_or_skip_outer_sandbox_limit(Guardian(), candidate_dir, timeout=0.3)
    assert result.passed is False
    assert "current_scene" in str(result.error_message)


def test_guardian_rejects_corrupt_database(tmp_path: Path):
    """候選資料庫完整性損壞時不得只依檔案存在判定健康。"""
    candidate_dir = tmp_path / "candidate"
    candidate_dir.mkdir()
    (candidate_dir / "main.py").write_text(
        """
import time
from pathlib import Path

(Path(__file__).parent / 'habitat.db').write_bytes(b'not a sqlite database')
while True:
    time.sleep(0.1)
""",
        encoding="utf-8",
    )

    result = run_smoke_or_skip_outer_sandbox_limit(Guardian(), candidate_dir, timeout=0.3)
    assert result.passed is False
    assert "完整性" in str(result.error_message) or "無法讀取" in str(result.error_message)


def test_guardian_refuses_symlinked_database(tmp_path: Path):
    """父程序驗證 SQLite 前先拒絕連向工作區外的符號連結。"""
    outside_db = tmp_path / "outside.db"
    outside_db.write_bytes(b"keep unchanged")
    candidate_db = tmp_path / "candidate" / "habitat.db"
    candidate_db.parent.mkdir()
    candidate_db.symlink_to(outside_db)

    error = Guardian._validate_candidate_database(candidate_db)

    assert "符號連結" in str(error)
    assert outside_db.read_bytes() == b"keep unchanged"


def test_guardian_refuses_hardlinked_database(tmp_path: Path):
    """父程序不開啟候選建立的外部 SQLite 硬連結。"""
    candidate_db = tmp_path / "candidate" / "habitat.db"
    candidate_db.parent.mkdir()
    candidate_db.write_bytes(b"candidate bytes")
    outside_link = tmp_path / "outside.db"
    outside_link.hardlink_to(candidate_db)

    error = Guardian._validate_candidate_database(candidate_db)

    assert "硬連結" in str(error)
    assert outside_link.read_bytes() == b"candidate bytes"
