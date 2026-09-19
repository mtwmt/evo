"""事件橋接器（Event Bridge）：連接 habitat.db 與 WebSocket 串流。"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from config.settings import DB_PATH, config
from core.resource.governor import ResourceGovernor


class ObserverBridge:
    """即時監聽資料庫與系統資源，打包為觀測者端串流封包。"""

    def __init__(self, db_path: Path = DB_PATH, governor: ResourceGovernor | None = None):
        self.db_path = Path(db_path).resolve()
        self.governor = governor or ResourceGovernor()
        self._snapshot_cache: dict[str, Any] | None = None
        self._snapshot_cached_at = 0.0
        self._snapshot_ttl_seconds = 0.25

    def get_latest_scene(self) -> dict[str, Any] | None:
        """從資料庫讀取最新寫入的通用渲染原語。"""
        if not self.db_path.exists():
            return None

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT json_data, updated_at FROM scene_primitives WHERE id = 'current_scene'"
                )
                row = cursor.fetchone()
                if row:
                    data = json.loads(row[0])
                    data["timestamp"] = row[1]
                    return data
        except Exception as exc:
            print(f"[ObserverBridge] 讀取場景原語異常：{exc}")

        return None

    def get_telemetry_snapshot(self, target_pid: int | None = None) -> dict[str, Any]:
        """彙整包含幾何場景、外層治理狀態與資源消耗之完整觀測封包。"""
        now = time.monotonic()
        if self._snapshot_cache and now - self._snapshot_cached_at < self._snapshot_ttl_seconds:
            return self._snapshot_cache

        scene = self.get_latest_scene()
        res = self.governor.check_limits(target_pid)

        snapshot = {
            "scene": scene,
            "runtime": {
                "active_cli": config.active_cli,
                "active_model": config.active_model,
                "codex_effort": config.codex_effort,
                "claude_effort": config.claude_effort,
                "speed_mode": config.speed_mode,
                "paused": config.paused,
                "tick_interval": config.universe_tick_interval,
                "heartbeat_cooldown": config.heartbeat_cooldown,
            },
            "resources": {
                "cpu_percent": round(res.cpu_percent, 1),
                "ram_mb": round(res.ram_bytes / (1024 * 1024), 1),
                "subprocesses": res.subprocess_count,
                "habitat_size_mb": round(res.habitat_size_bytes / (1024 * 1024), 2),
                "is_healthy": res.is_healthy,
                "warning": res.warning_message,
            },
        }
        self._snapshot_cache = snapshot
        self._snapshot_cached_at = now
        return snapshot
