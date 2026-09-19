"""Cognitive Heartbeat 上下文管理器。"""

import json
import sqlite3
from pathlib import Path
from typing import Any

from config.settings import DB_PATH, PROMPTS_DIR, SKILLS_DIR


class ContextManager:
    """收集宇宙即時狀態、觀察者神諭訊號、歷史事件與技能，組裝成 AI 提示詞。"""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path

    def load_base_prompt(self) -> str:
        """載入極簡核心 Base Prompt。"""
        base_prompt_file = PROMPTS_DIR / "base.md"
        if base_prompt_file.exists():
            return base_prompt_file.read_text(encoding="utf-8")
        return "你是這個自主演化宇宙的獨立架構師。"

    def load_skills(self, skill_names: list[str] | None = None) -> str:
        """依需求動態載入技能文件。"""
        skill_contents = []
        target_skills = skill_names or ["autonomous", "review", "migration", "history"]
        for name in target_skills:
            skill_file = SKILLS_DIR / name / "SKILL.md"
            if skill_file.exists():
                skill_contents.append(f"### 技能：{name}\n{skill_file.read_text(encoding='utf-8')}")
        return "\n\n".join(skill_contents)

    def fetch_observer_signals(self, mark_as_read: bool = True) -> list[dict[str, Any]]:
        """從 habitat.db 撈取尚未處理的觀察者對話／神諭訊號。"""
        if not self.db_path.exists():
            return []

        signals = []
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                # 檢查 observer_signals 資料表是否存在
                cursor.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='observer_signals'"
                )
                if cursor.fetchone()[0] == 0:
                    return []

                cursor.execute(
                    "SELECT id, sender, message, timestamp FROM observer_signals WHERE processed = 0 ORDER BY timestamp ASC"
                )
                rows = cursor.fetchall()
                for row in rows:
                    signals.append({
                        "id": row[0],
                        "sender": row[1],
                        "message": row[2],
                        "timestamp": row[3],
                    })

                # 標記為已讀
                if mark_as_read and rows:
                    ids = [r[0] for r in rows]
                    placeholders = ",".join("?" for _ in ids)
                    cursor.execute(
                        f"UPDATE observer_signals SET processed = 1 WHERE id IN ({placeholders})",
                        ids,
                    )
                    conn.commit()
        except Exception as exc:
            # 資料庫若暫時鎖定則優雅忽略
            print(f"[ContextManager] 撈取觀察者訊號失敗：{exc}")

        return signals

    def fetch_world_metrics(self) -> dict[str, Any]:
        """從 habitat.db 讀取當前最新的世界指標。"""
        if not self.db_path.exists():
            return {}

        metrics = {}
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='world_state'"
                )
                if cursor.fetchone()[0] > 0:
                    cursor.execute("SELECT key, value FROM world_state")
                    for k, v in cursor.fetchall():
                        try:
                            metrics[k] = json.loads(v)
                        except Exception:
                            metrics[k] = v
        except Exception:
            pass

        return metrics

    def fetch_recent_events(self, limit: int = 15) -> list[dict[str, Any]]:
        """從 habitat.db 撈取最近發生的宇宙事件。"""
        if not self.db_path.exists():
            return []

        events = []
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='events'"
                )
                if cursor.fetchone()[0] > 0:
                    cursor.execute(
                        "SELECT id, type, message, importance, timestamp FROM events ORDER BY timestamp DESC LIMIT ?",
                        (limit,),
                    )
                    for row in cursor.fetchall():
                        events.append({
                            "id": row[0],
                            "type": row[1],
                            "message": row[2],
                            "importance": row[3],
                            "timestamp": row[4],
                        })
        except Exception:
            pass

        return events

    def assemble_prompt(self) -> str:
        """組裝完整的單回合 AI 提示詞。"""
        base_prompt = self.load_base_prompt()
        skills = self.load_skills()
        signals = self.fetch_observer_signals(mark_as_read=True)
        metrics = self.fetch_world_metrics()
        events = self.fetch_recent_events()

        prompt_parts = [
            base_prompt,
            "## 可用技能指南",
            skills,
            "## 當前宇宙狀態",
            f"**世界數值 (Metrics)**: {json.dumps(metrics, indent=2, ensure_ascii=False)}",
            f"**近期重大事件 (Events)**: {json.dumps(events, indent=2, ensure_ascii=False)}",
        ]

        if signals:
            prompt_parts.append("## 觀察者傳來的天外神諭訊號")
            for s in signals:
                prompt_parts.append(f"- [{s['sender']}] ({s['timestamp']}): {s['message']}")
        else:
            prompt_parts.append("## 觀察者傳來的天外神諭訊號\n本週期無外部訊號。")

        prompt_parts.append(
            "\n## 本次回合指示\n請審視當前宇宙運行狀況，對 `habitat/main.py` 或輔助模組進行迭代進化代碼修改；"
            "若當前宇宙狀態高度平衡且運行穩定、暫不需修改代碼，可回覆：`STATUS: SLEEP <N>`（N 為休眠觀測週期數）。"
        )

        return "\n\n".join(prompt_parts)
