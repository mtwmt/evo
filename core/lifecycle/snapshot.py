"""快照與回滾管理器：負責 Habitat 代碼與 SQLite 資料庫之安全備份與 Atomic 部署。"""

import shutil
import sqlite3
import time
import uuid
from pathlib import Path

from config.settings import BACKUPS_DIR, HABITAT_DIR

MAX_SNAPSHOT_SETS = 3


class LifecycleManager:
    """處理 Atomic 部署、資料庫快照備份以及一鍵回滾機制。"""

    def __init__(self, habitat_dir: Path = HABITAT_DIR, backups_dir: Path = BACKUPS_DIR):
        self.habitat_dir = Path(habitat_dir).resolve()
        self.backups_dir = Path(backups_dir).resolve()
        self.backups_dir.mkdir(parents=True, exist_ok=True)

    def create_snapshot(self) -> tuple[Path | None, Path]:
        """於候選版本部署前，同時建立資料庫與程式碼快照。"""
        timestamp = int(time.time() * 1000)

        # 1. 透過 SQLite 線上備份 API 建立熱備份，避免讀寫鎖定衝突
        db_snapshot_path = None
        db_file = self.habitat_dir / "habitat.db"
        if db_file.exists():
            db_snapshot_path = self.backups_dir / f"db_{timestamp}.sqlite"
            try:
                with sqlite3.connect(db_file) as src, sqlite3.connect(db_snapshot_path) as dst:
                    src.backup(dst)
            except Exception:
                # 若線上備份失敗，降級為底層檔案拷貝
                shutil.copy2(db_file, db_snapshot_path)

        # 2. 程式碼目錄快照
        code_snapshot_path = self.backups_dir / f"code_{timestamp}"
        code_snapshot_path.mkdir(parents=True, exist_ok=True)
        for item in self.habitat_dir.iterdir():
            if item.name.endswith(".db") or item.name.startswith("."):
                continue
            if item.is_dir():
                shutil.copytree(item, code_snapshot_path / item.name, dirs_exist_ok=True)
            elif item.is_file():
                shutil.copy2(item, code_snapshot_path / item.name)

        self._prune_snapshots()
        return db_snapshot_path, code_snapshot_path

    def _prune_snapshots(self) -> None:
        """僅保留最新的程式碼／資料庫快照組，避免備份目錄無限成長。"""
        snapshot_ids = sorted(
            (
                path.name.removeprefix("code_")
                for path in self.backups_dir.glob("code_*")
                if path.is_dir() and path.name.removeprefix("code_").isdigit()
            ),
            key=int,
            reverse=True,
        )
        retained_ids = set(snapshot_ids[:MAX_SNAPSHOT_SETS])

        for path in self.backups_dir.iterdir():
            if path.name == ".gitkeep":
                continue
            if path.is_dir() and path.name.startswith("code_"):
                snapshot_id = path.name.removeprefix("code_")
            elif path.is_file() and path.name.startswith("db_") and path.suffix == ".sqlite":
                snapshot_id = path.stem.removeprefix("db_")
            else:
                continue

            if snapshot_id.isdigit() and snapshot_id not in retained_ids:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()

    def atomic_deploy_staging(self, staging_dir: Path) -> None:
        """以目錄交換部署候選程式碼，保留正式世界資料庫。"""
        staging_dir = Path(staging_dir).resolve()
        if not staging_dir.is_dir():
            raise FileNotFoundError(f"找不到候選目錄：'{staging_dir}'")

        self.habitat_dir.parent.mkdir(parents=True, exist_ok=True)
        deploy_dir = self.habitat_dir.parent / f".{self.habitat_dir.name}.deploy-{uuid.uuid4().hex}"
        previous_dir = self.habitat_dir.parent / f".{self.habitat_dir.name}.previous-{uuid.uuid4().hex}"
        deploy_dir.mkdir()

        try:
            # 候選僅可替換程式碼；世界 DB 與 WAL 檔須保留在正式版本。
            for item in staging_dir.iterdir():
                if item.name.startswith(".") or item.name.startswith("habitat.db"):
                    continue
                target = deploy_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, target)
                elif item.is_file():
                    shutil.copy2(item, target)

            if self.habitat_dir.exists():
                for item in self.habitat_dir.glob("habitat.db*"):
                    shutil.copy2(item, deploy_dir / item.name)
                self.habitat_dir.replace(previous_dir)

            deploy_dir.replace(self.habitat_dir)
        except Exception:
            if previous_dir.exists() and not self.habitat_dir.exists():
                previous_dir.replace(self.habitat_dir)
            raise
        finally:
            if deploy_dir.exists():
                shutil.rmtree(deploy_dir)
            if previous_dir.exists():
                shutil.rmtree(previous_dir)

    def rollback(self, db_snapshot: Path | None, code_snapshot: Path) -> None:
        """於 runtime 已停止時，以快照完整還原程式碼與資料庫。"""
        if not code_snapshot.exists():
            raise FileNotFoundError(f"找不到程式碼快照：'{code_snapshot}'")

        restore_dir = self.habitat_dir.parent / f".{self.habitat_dir.name}.restore-{uuid.uuid4().hex}"
        previous_dir = self.habitat_dir.parent / f".{self.habitat_dir.name}.failed-{uuid.uuid4().hex}"
        shutil.copytree(code_snapshot, restore_dir)
        if db_snapshot and db_snapshot.exists():
            shutil.copy2(db_snapshot, restore_dir / "habitat.db")

        try:
            if self.habitat_dir.exists():
                self.habitat_dir.replace(previous_dir)
            restore_dir.replace(self.habitat_dir)
        except Exception:
            if previous_dir.exists() and not self.habitat_dir.exists():
                previous_dir.replace(self.habitat_dir)
            raise
        finally:
            if restore_dir.exists():
                shutil.rmtree(restore_dir)
            if previous_dir.exists():
                shutil.rmtree(previous_dir)
