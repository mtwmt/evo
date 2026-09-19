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

    def fetch_observer_signals(self, mark_as_read: bool = False) -> list[dict[str, Any]]:
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
                    "SELECT id, sender, message, timestamp, target_entity_id "
                    "FROM observer_signals WHERE processed = 0 ORDER BY timestamp ASC, id ASC"
                )
                rows = cursor.fetchall()
                for row in rows:
                    signals.append({
                        "id": row[0],
                        "sender": row[1],
                        "message": row[2],
                        "timestamp": row[3],
                        "target_entity_id": row[4],
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

    def acknowledge_observer_signals(self, ids: list[int]) -> None:
        """只確認指定回合已成功處理的觀察者訊息。"""
        if not ids:
            return
        if any(type(signal_id) is not int for signal_id in ids):
            raise ValueError("觀察者訊息 ID 必須是整數")
        if not self.db_path.exists():
            return
        placeholders = ",".join("?" for _ in ids)
        with sqlite3.connect(self.db_path) as conn:
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='observer_signals'"
            ).fetchone()
            if table_exists is None:
                return
            conn.execute(
                f"UPDATE observer_signals SET processed = 1 "
                f"WHERE processed = 0 AND id IN ({placeholders})",
                ids,
            )
            conn.commit()

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

    def describe_visible_world(self) -> dict[str, Any]:
        """摘要目前畫面，讓認知節拍能辨識世界是否仍停留在佔位符。"""
        if not self.db_path.exists():
            return {"has_scene": False, "needs_evolution": True}
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT json_data FROM scene_primitives WHERE id = 'current_scene'"
                ).fetchone()
            if not row:
                return {"has_scene": False, "needs_evolution": True}
            scene = json.loads(row[0])
            entities = scene.get("entities", [])
            if not isinstance(entities, list) or not entities:
                return {"has_scene": True, "entity_count": 0, "needs_evolution": True}
            shapes = {str(entity.get("shape", "circle")) for entity in entities if isinstance(entity, dict)}
            labels = [str(entity.get("label", "")) for entity in entities if isinstance(entity, dict)]
            return {
                "has_scene": True,
                "entity_count": len(entities),
                "shapes": sorted(shapes),
                "label_examples": labels[:8],
                "needs_evolution": False,
            }
        except (sqlite3.Error, json.JSONDecodeError, TypeError):
            return {"has_scene": False, "needs_evolution": True}

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

    def _assemble_prompt(self, signals: list[dict[str, Any]]) -> str:
        """使用指定的訊息批次組裝單回合提示詞，不改變其處理狀態。"""
        base_prompt = self.load_base_prompt()
        skills = self.load_skills()
        metrics = self.fetch_world_metrics()
        events = self.fetch_recent_events()
        visible_world = self.describe_visible_world()

        prompt_parts = [
            base_prompt,
            "## 可用技能指南",
            skills,
            "## 當前宇宙狀態",
            f"**世界數值 (Metrics)**: {json.dumps(metrics, indent=2, ensure_ascii=False)}",
            f"**近期重大事件 (Events)**: {json.dumps(events, indent=2, ensure_ascii=False)}",
            f"**目前可見畫面摘要**: {json.dumps(visible_world, indent=2, ensure_ascii=False)}",
        ]

        if signals:
            prompt_parts.append("## 觀察者傳來的天外神諭訊號")
            for s in signals:
                target = s.get("target_entity_id")
                target_text = f"（目標實體：{target}）" if target else ""
                prompt_parts.append(f"- [{s['sender']}] ({s['timestamp']}){target_text}: {s['message']}")
        else:
            prompt_parts.append("## 觀察者傳來的天外神諭訊號\n本週期無外部訊號。")

        prompt_parts.append(
            "\n## 本次回合指示\n"
            "你擁有世界題材、宇宙觀與規則的完全創作自主權。不要向人類索取題材、"
            "角色、敘事或世界觀；請從已有狀態與你自己的判斷中決定下一步。\n"
            "若 `main.py` 尚不存在，這是合法的空白創世狀態；請自行建立 `main.py` 與所需模組，"
            "不可因目前沒有世界而休眠。若入口已存在，每回合先讀取目前的 `main.py`、"
            "資料庫狀態與畫面摘要。請自行選擇下一個有趣方向，"
            "自行修改 habitat 的程式，可新增、拆分或刪除模組，保留 `main.py` 作為入口；"
            "建立可累積的世界狀態、事件歷史與可見差異（至少不同類型的實體、"
            "關係或階段），但保留既有 SQLite 與通用 scene primitives 協議。\n"
            "世界 tick 迴圈必須讀取環境變數 `EVO_TICK_INTERVAL` 作為 sleep 間隔，不可寫死延遲；"
            "外層的 1x／3x／MAX 速度控制必須實際改變模擬推進速率。\n"
            "所有觀測者可見的事件訊息、大事記、編年、紀元名稱與公開敘事，必須使用繁體中文；"
            "資料庫的 id、type 等機器欄位可保留穩定的 ASCII 識別字。\n"
            "大事記只記錄紀元轉換及其正式編年：請以 `epoch_transition` 或 `epoch_change` 記錄轉換，"
            "僅以 `chronicle` 補充該紀元的世界級敘事；個體的誕生、創作、互動、發現等一律留在一般 events，"
            "即使其重要度很高。\n"
            "不要讓世界只靠隨機事件碰巧跨越紀元門檻：當下一紀元所需的建設、探索、關係或知識長期不足時，"
            "請讓存在主動形成可見且可達成的行動目標，並把進度持久化。\n"
            "若你選擇建立具行動力的存在，『誰』由你定義：可以是人、群體、動物、機器、異世界生命或任何形式。"
            "它們的行為必須能造成可追溯後果：需求、意外、合作、探索或失敗，應改變後續狀態，並可能促成適應、"
            "發明、技術、制度或環境變遷。將因果鏈記入 events 與持久世界狀態。\n"
            "只在世界已有清晰主題、連續演化且本回合確實不需要改動時，才可回覆 `STATUS: SLEEP <N>`。"
        )

        return "\n\n".join(prompt_parts)

    def prepare_turn_context(self) -> tuple[str, list[int]]:
        """準備提示詞與訊息 ID；訊息保留待部署成功後由呼叫端確認。"""
        signals = self.fetch_observer_signals(mark_as_read=False)
        return self._assemble_prompt(signals), [signal["id"] for signal in signals]

    def assemble_prompt(self) -> str:
        """讀取提示詞預覽，不確認或消費訊息。"""
        signals = self.fetch_observer_signals(mark_as_read=False)
        return self._assemble_prompt(signals)
