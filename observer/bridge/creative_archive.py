"""觀測器端的創作封存唯讀協議，不依賴 habitat 內的可變程式。"""

import json
import sqlite3
from typing import Any


def _creative_work_columns(connection: sqlite3.Connection) -> set[str]:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'creative_works'"
    ).fetchone()
    if table is None:
        return set()
    return {str(row[1]) for row in connection.execute("PRAGMA table_info(creative_works)")}


def list_creative_works(
    connection: sqlite3.Connection,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """列出作品中繼資料；世界尚未建立作品表時回傳空清單。"""
    columns = _creative_work_columns(connection)
    required = {"id", "creator_id", "title", "medium", "mime_type", "content",
                "metadata_json", "epoch", "created_at"}
    if not required.issubset(columns):
        return []

    rows = connection.execute(
        "SELECT id, creator_id, title, medium, mime_type, "
        "metadata_json, epoch, created_at, "
        "length(content) FROM creative_works "
        "ORDER BY created_at DESC "
        "LIMIT ?",
        (max(1, min(int(limit), 500)),),
    ).fetchall()

    works = []
    for row in rows:
        metadata: Any = row[5]
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                pass
        works.append({
            "id": row[0],
            "creator_id": row[1],
            "title": row[2],
            "medium": row[3],
            "mime_type": row[4],
            "metadata": metadata,
            "epoch": row[6],
            "created_at": row[7],
            "size_bytes": row[8],
        })
    return works


def read_creative_work(
    connection: sqlite3.Connection,
    work_id: str,
) -> tuple[bytes, str, str] | None:
    """依作品 ID 讀取完整原始內容；尚未建立作品表時視為找不到。"""
    columns = _creative_work_columns(connection)
    required = {"id", "title", "mime_type", "content"}
    if not required.issubset(columns):
        return None
    row = connection.execute(
        "SELECT content, mime_type, title FROM creative_works WHERE id = ?",
        (work_id,),
    ).fetchone()
    if row is None:
        return None
    content = row[0]
    if isinstance(content, str):
        content = content.encode("utf-8")
    return bytes(content), str(row[1] or "application/octet-stream"), str(row[2] or work_id)
