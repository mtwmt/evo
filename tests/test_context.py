"""認知回合訊息準備與延後確認的回歸測試。"""

import sqlite3
from pathlib import Path

from core.context.manager import ContextManager


def init_database(db_path: Path) -> None:
    """建立 Context 測試需要的通用資料表，不依賴可變 habitat 程式。"""
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE world_state (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE scene_primitives (
                id TEXT PRIMARY KEY, json_data TEXT, updated_at REAL
            );
            CREATE TABLE events (
                id TEXT PRIMARY KEY, type TEXT, message TEXT, importance INTEGER,
                timestamp REAL, entity_ids TEXT, epoch INTEGER
            );
            CREATE TABLE observer_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT, sender TEXT, message TEXT,
                target_entity_id TEXT, timestamp REAL, processed INTEGER DEFAULT 0,
                delivered_to_universe INTEGER DEFAULT 0
            );
            """
        )


def test_prepare_keeps_messages_and_acknowledges_only_requested_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "context.db"
    init_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO observer_signals (sender, message, target_entity_id, timestamp) "
            "VALUES (?, ?, ?, ?)",
            [
                ("甲", "第一則訊息", "node_3", 1),
                ("乙", "第二則訊息", "node_7", 2),
            ],
        )

    manager = ContextManager(db_path=db_path)
    prompt, message_ids = manager.prepare_turn_context()
    assert len(message_ids) == 2
    assert "目標實體：node_3" in prompt
    assert "目標實體：node_7" in prompt
    assert "第一則訊息" in prompt and "第二則訊息" in prompt
    assert "必須使用繁體中文" in prompt
    assert "機器欄位可保留穩定的 ASCII 識別字" in prompt
    assert "EVO_TICK_INTERVAL" in prompt
    assert "大事記只記錄紀元轉換及其正式編年" in prompt
    assert "不要讓世界只靠隨機事件碰巧跨越紀元門檻" in prompt
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT processed FROM observer_signals ORDER BY id"
        ).fetchall() == [(0,), (0,)]

    manager.acknowledge_observer_signals(message_ids[:1])
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT processed FROM observer_signals ORDER BY id"
        ).fetchall() == [(1,), (0,)]

    _, remaining_ids = manager.prepare_turn_context()
    assert remaining_ids == message_ids[1:]


def test_assemble_prompt_remains_non_consuming_and_new_ids_are_not_acknowledged(tmp_path: Path) -> None:
    db_path = tmp_path / "context.db"
    init_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO observer_signals (sender, message, target_entity_id, timestamp) "
            "VALUES ('observer', '可重試', 'node_1', 1)"
        )

    manager = ContextManager(db_path=db_path)
    prompt = manager.assemble_prompt()
    assert "可重試" in prompt
    with sqlite3.connect(db_path) as conn:
        first_id = conn.execute("SELECT id FROM observer_signals").fetchone()[0]
        assert conn.execute("SELECT processed FROM observer_signals").fetchone()[0] == 0
        conn.execute(
            "INSERT INTO observer_signals (sender, message, target_entity_id, timestamp) "
            "VALUES ('observer', '稍後抵達', 'node_2', 2)"
        )

    manager.acknowledge_observer_signals([first_id])
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT processed FROM observer_signals ORDER BY id"
        ).fetchall()
    assert rows == [(1,), (0,)]
