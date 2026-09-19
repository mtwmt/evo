"""快照與回滾機制單元測試。"""

import sqlite3
from pathlib import Path

from core.lifecycle.snapshot import MAX_SNAPSHOT_SETS, LifecycleManager


def test_first_deployment_preserves_generated_database(tmp_path: Path):
    habitat = tmp_path / "habitat"
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "main.py").write_text("print('genesis')")
    with sqlite3.connect(staging / "habitat.db") as conn:
        conn.execute("CREATE TABLE state (value TEXT)")
        conn.execute("INSERT INTO state VALUES ('genesis')")
    LifecycleManager(habitat, tmp_path / "backups").atomic_deploy_staging(staging)
    with sqlite3.connect(habitat / "habitat.db") as conn:
        assert conn.execute("SELECT value FROM state").fetchone()[0] == "genesis"


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
    (habitat_dir / ".world-memory").mkdir()
    (habitat_dir / ".world-memory" / "state.json").write_text('{"stage": 1}')

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
    (staging_dir / ".world-memory").mkdir()
    (staging_dir / ".world-memory" / "state.json").write_text('{"stage": 2}')
    with sqlite3.connect(staging_dir / "habitat.db") as conn:
        conn.execute("CREATE TABLE world (val TEXT);")
        conn.execute("INSERT INTO world VALUES ('corrupted_v2_data');")
        conn.commit()

    manager.atomic_deploy_staging(staging_dir)
    assert (habitat_dir / "main.py").read_text(encoding="utf-8") == "print('version 2.0 broken')"
    assert not (habitat_dir / "obsolete.py").exists()
    assert (habitat_dir / ".world-memory" / "state.json").read_text() == '{"stage": 2}'
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT val FROM world").fetchone()[0] == "v1_data"

    # 4. 觸發一鍵回滾
    manager.rollback(db_snap, code_snap)

    # 5. 驗證代碼與資料庫完整復原為 v1
    assert (habitat_dir / "main.py").read_text(encoding="utf-8") == "print('version 1.0')"
    assert (habitat_dir / ".world-memory" / "state.json").read_text() == '{"stage": 1}'
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


def test_code_snapshots_exclude_database_sidecars_recursively(tmp_path: Path):
    """程式碼快照不收錄根目錄或巢狀 SQLite DB/WAL/SHM。"""
    habitat = tmp_path / "habitat"
    backups = tmp_path / "backups"
    habitat.mkdir()
    (habitat / "main.py").write_text("print('world')", encoding="utf-8")
    with sqlite3.connect(habitat / "habitat.db") as conn:
        conn.execute("CREATE TABLE state (value TEXT)")
        conn.commit()
    nested = habitat / "data"
    nested.mkdir()
    (nested / "cache.sqlite").write_bytes(b"sqlite")
    (nested / "cache.sqlite-wal").write_bytes(b"wal")
    (nested / "cache.sqlite-shm").write_bytes(b"shm")
    (nested / "rules.py").write_text("VALUE = 1", encoding="utf-8")

    db_snapshot, code_snapshot = LifecycleManager(habitat, backups).create_snapshot()

    assert db_snapshot is not None and db_snapshot.exists()
    assert (code_snapshot / "main.py").exists()
    assert (code_snapshot / "data" / "rules.py").exists()
    assert not (code_snapshot / "habitat.db").exists()
    assert not (code_snapshot / "data" / "cache.sqlite").exists()
    assert not (code_snapshot / "data" / "cache.sqlite-wal").exists()
    assert not (code_snapshot / "data" / "cache.sqlite-shm").exists()


def test_snapshot_rejects_nested_symlinks_without_copying_external_data(tmp_path: Path):
    """快照拒絕巢狀 symlink，不會追蹤或複製外部目錄內容。"""
    habitat = tmp_path / "habitat"
    backups = tmp_path / "backups"
    outside = tmp_path / "outside"
    habitat.mkdir()
    outside.mkdir()
    (habitat / "main.py").write_text("print('world')", encoding="utf-8")
    (outside / "secret.py").write_text("SECRET = True", encoding="utf-8")
    (habitat / "linked").symlink_to(outside, target_is_directory=True)
    manager = LifecycleManager(habitat, backups)

    try:
        manager.create_snapshot()
    except ValueError as exc:
        assert "符號連結" in str(exc)
    else:
        raise AssertionError("快照應拒絕巢狀符號連結")

    assert not any(backups.glob("code_*/linked/secret.py"))
