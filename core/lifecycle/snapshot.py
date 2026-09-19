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

    def create_snapshot(self) -> tuple[Path | None, Path]:
        """於候選版本部署前，同時建立資料庫與程式碼快照。"""
        timestamp = time.time_ns()
        self.backups_dir.mkdir(parents=True, exist_ok=True)

        # 1. 透過 SQLite 線上備份 API 建立熱備份，避免讀寫鎖定衝突
        code_snapshot_path = self.backups_dir / f"code_{timestamp}"
        db_snapshot_path = None
        try:
            db_file = self.habitat_dir / "habitat.db"
            if db_file.exists():
                db_snapshot_path = self.backups_dir / f"db_{timestamp}.sqlite"
                self._sqlite_backup(db_file, db_snapshot_path)

            # 程式碼目錄快照不收錄 DB、WAL 或 SHM 檔案。首次創世時
            # habitat 可以尚未建立，此時空快照仍可作為部署回滾基準。
            code_snapshot_path.mkdir(parents=True, exist_ok=False)
            if self.habitat_dir.exists():
                for item in self.habitat_dir.iterdir():
                    if self.is_database_file(item.name):
                        continue
                    if item.is_dir():
                        self._copy_tree(item, code_snapshot_path / item.name)
                    elif item.is_file():
                        self._copy_file(item, code_snapshot_path / item.name)
        except Exception:
            if code_snapshot_path.exists():
                shutil.rmtree(code_snapshot_path)
            if db_snapshot_path and db_snapshot_path.exists():
                db_snapshot_path.unlink()
            raise

        self._prune_snapshots()
        return db_snapshot_path, code_snapshot_path

    @staticmethod
    def is_database_file(name: str) -> bool:
        base_name = (
            name.removesuffix("-wal")
            .removesuffix("-shm")
            .removesuffix("-journal")
        )
        return base_name.endswith((".db", ".sqlite", ".sqlite3"))

    @staticmethod
    def _sqlite_backup(source: Path, destination: Path) -> None:
        """用 SQLite 備份 API 複製一致資料，不複製 WAL/SHM sidecar。"""
        if source.is_symlink() or destination.is_symlink():
            raise ValueError("拒絕沿著 SQLite 符號連結讀寫資料")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
            src.backup(dst)

    @staticmethod
    def _copy_file(source: Path, destination: Path) -> None:
        if source.is_symlink():
            raise ValueError(f"拒絕複製符號連結：'{source}'")
        if LifecycleManager.is_database_file(source.name):
            return
        shutil.copy2(source, destination)

    @classmethod
    def _copy_tree(cls, source: Path, destination: Path) -> None:
        if source.is_symlink():
            raise ValueError(f"拒絕複製符號連結：'{source}'")
        destination.mkdir(parents=True, exist_ok=False)
        for item in source.iterdir():
            if cls.is_database_file(item.name):
                continue
            target = destination / item.name
            if item.is_symlink():
                raise ValueError(f"拒絕複製巢狀符號連結：'{item}'")
            if item.is_dir():
                cls._copy_tree(item, target)
            elif item.is_file():
                cls._copy_file(item, target)

    def copy_database_backup(self, source: Path, destination: Path) -> None:
        """將 SQLite 線上備份寫入指定位置。"""
        self._sqlite_backup(Path(source), Path(destination))

    def copy_code_tree(self, source: Path, destination: Path) -> None:
        """安全複製程式目錄，拒絕 symlink 並略過資料庫檔與 sidecar。"""
        self._copy_tree(Path(source), Path(destination))

    def copy_code_file(self, source: Path, destination: Path) -> None:
        """安全複製單一程式檔，拒絕 symlink 並略過資料庫檔。"""
        self._copy_file(Path(source), Path(destination))

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
                if self.is_database_file(item.name):
                    continue
                target = deploy_dir / item.name
                if item.is_dir():
                    self._copy_tree(item, target)
                elif item.is_file():
                    self._copy_file(item, target)

            db_file = self.habitat_dir / "habitat.db"
            if db_file.exists():
                self._sqlite_backup(db_file, deploy_dir / "habitat.db")
            elif (staging_dir / "habitat.db").exists():
                # 首次創世保留已驗證的初始資料；已有世界則只沿用正式 DB。
                self._sqlite_backup(staging_dir / "habitat.db", deploy_dir / "habitat.db")

            if self.habitat_dir.exists():
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
        try:
            self._copy_tree(code_snapshot, restore_dir)
            if db_snapshot and db_snapshot.exists():
                self._sqlite_backup(db_snapshot, restore_dir / "habitat.db")
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
