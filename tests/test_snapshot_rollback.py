"""快照與回滾機制單元測試。"""

import sqlite3
from pathlib import Path

from core.lifecycle.snapshot import MAX_SNAPSHOT_SETS, LifecycleManager


def test_snapshot_and_rollback(tmp_path: Path):
    """驗證當候選發布異常時，資料庫與程式碼檔案能夠一鍵完整回滾。"""
    habitat_dir = tmp_path / "habitat"
    backups_dir = tmp_path / "backups"
    staging_dir = tmp_path / "staging"
    habitat_dir.mkdir()
    backups_dir.mkdir()
    staging_dir.mkdir()

    # 1. 建立初始穩定版本世界代碼與資料庫
    main_code = "print('version 1.0')"
    (habitat_dir / "main.py").write_text(main_code, encoding="utf-8")
    (habitat_dir / "obsolete.py").write_text("print('old')", encoding="utf-8")

    db_path = habitat_dir / "habitat.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE world (val TEXT);")
        conn.execute("INSERT INTO world VALUES ('v1_data');")
        conn.commit()

    manager = LifecycleManager(habitat_dir=habitat_dir, backups_dir=backups_dir)

    # 2. 建立快照
    db_snap, code_snap = manager.create_snapshot()
    assert db_snap is not None
    assert db_snap.exists()
    assert code_snap.exists()

    # 3. 模擬候選版本（版本 2.0）進行破壞性修改
    (staging_dir / "main.py").write_text("print('version 2.0 broken')", encoding="utf-8")
    with sqlite3.connect(staging_dir / "habitat.db") as conn:
        conn.execute("CREATE TABLE world (val TEXT);")
        conn.execute("INSERT INTO world VALUES ('corrupted_v2_data');")
        conn.commit()

    manager.atomic_deploy_staging(staging_dir)
    assert (habitat_dir / "main.py").read_text(encoding="utf-8") == "print('version 2.0 broken')"
    assert not (habitat_dir / "obsolete.py").exists()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT val FROM world").fetchone()[0] == "v1_data"

    # 4. 觸發一鍵回滾
    manager.rollback(db_snap, code_snap)

    # 5. 驗證代碼與資料庫完整復原為 v1
    assert (habitat_dir / "main.py").read_text(encoding="utf-8") == "print('version 1.0')"
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT val FROM world").fetchone()
        assert row[0] == "v1_data"


def test_snapshot_retention_keeps_three_latest_sets(tmp_path: Path):
    """舊快照會和對應資料庫一併清理，保留最近三組供回滾。"""
    habitat_dir = tmp_path / "habitat"
    backups_dir = tmp_path / "backups"
    habitat_dir.mkdir()
    backups_dir.mkdir()
    manager = LifecycleManager(habitat_dir=habitat_dir, backups_dir=backups_dir)

    for timestamp in range(1, 6):
        (backups_dir / f"code_{timestamp}").mkdir()
        (backups_dir / f"db_{timestamp}.sqlite").touch()
    (backups_dir / ".gitkeep").touch()

    manager._prune_snapshots()

    retained = {str(timestamp) for timestamp in range(3, 6)}
    assert {path.name.removeprefix("code_") for path in backups_dir.glob("code_*")} == retained
    assert {path.stem.removeprefix("db_") for path in backups_dir.glob("db_*.sqlite")} == retained
    assert (backups_dir / ".gitkeep").exists()
    assert len(retained) == MAX_SNAPSHOT_SETS
