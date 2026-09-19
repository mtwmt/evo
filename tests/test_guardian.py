"""審查門神（Guardian）驗收功能單元測試。"""

from pathlib import Path

from core.review.guardian import Guardian


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
    res = guardian.run_smoke_test(candidate_dir, timeout_seconds=2.0)

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

    res = Guardian().run_smoke_test(candidate_dir, timeout_seconds=0.2)

    assert res.passed is False
    assert "提早結束" in str(res.error_message)


def test_guardian_passes_valid_candidate(tmp_path: Path):
    """驗證健康、正常連線 SQLite 的候選版本能順利通過靜態分析與冒煙測試。"""
    candidate_dir = tmp_path / "candidate"
    candidate_dir.mkdir()

    valid_code = """
import sqlite3
import time
from pathlib import Path

db = Path(__file__).parent / "habitat.db"
with sqlite3.connect(db) as conn:
    conn.execute("CREATE TABLE IF NOT EXISTS test (id INT);")
    conn.commit()

while True:
    time.sleep(0.1)
"""
    (candidate_dir / "main.py").write_text(valid_code, encoding="utf-8")

    guardian = Guardian()
    res = guardian.verify(candidate_dir)

    assert res.passed is True
    assert res.step == "complete"
