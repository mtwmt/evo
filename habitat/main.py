"""創世種子（Genesis Seed）：自主演化宇宙持續運作主程序。"""

import json
import math
import os
import random
import sqlite3
import sys
import time
from pathlib import Path

# 路徑設定
HABITAT_DIR = Path(__file__).resolve().parent
DB_FILE = HABITAT_DIR / "habitat.db"


def init_database(db_path: Path = DB_FILE) -> None:
    """初始化創世 SQLite 資料庫 Schema，存放世界狀態與通用渲染原語。"""
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS world_state (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS scene_primitives (
            id TEXT PRIMARY KEY,
            json_data TEXT,
            updated_at REAL
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            type TEXT,
            message TEXT,
            importance INTEGER,
            timestamp REAL,
            entity_ids TEXT
        );
        """)
        event_columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
        if "entity_ids" not in event_columns:
            conn.execute("ALTER TABLE events ADD COLUMN entity_ids TEXT")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS observer_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT,
            message TEXT,
            target_entity_id TEXT,
            timestamp REAL,
            processed INTEGER DEFAULT 0,
            delivered_to_universe INTEGER DEFAULT 0
        );
        """)
        signal_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(observer_signals)")
        }
        if "target_entity_id" not in signal_columns:
            conn.execute("ALTER TABLE observer_signals ADD COLUMN target_entity_id TEXT")
        if "delivered_to_universe" not in signal_columns:
            conn.execute("ALTER TABLE observer_signals ADD COLUMN delivered_to_universe INTEGER DEFAULT 0")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS entity_monologues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT,
            monologue TEXT,
            state TEXT,
            timestamp REAL
        );
        """)
        conn.commit()


class EmergentUniverse:
    """自生質點動力與意識系統：具備生命體第一人稱內在感知與獨白。"""

    def __init__(self, node_count: int = 32):
        self.width = 800
        self.height = 600
        self.tick_count = 0
        self.previous_link_ids: set[tuple[str, str]] = set()
        self.nodes = []
        for i in range(node_count):
            self.nodes.append({
                "id": f"node_{i}",
                "x": random.uniform(100, 700),
                "y": random.uniform(100, 500),
                "vx": random.uniform(-1.5, 1.5),
                "vy": random.uniform(-1.5, 1.5),
                "energy": random.uniform(0.6, 1.0),
                "mass": random.uniform(5.0, 10.0),
                "cluster": i % 4,
                "monologue": "初生的意識在虛空中緩緩凝聚...",
                "state": "凝結初生",
            })

    def generate_monologue(
        self,
        node: dict,
        neighbors: list[str],
        near_boundary: bool,
        oracle_hint: str | None,
        oracle_target_id: str | None,
    ) -> tuple[str, str]:
        """依據物理位置、連線鄰居、動能與外在神諭產生第一人稱內在獨白。"""
        speed = math.sqrt(node["vx"] ** 2 + node["vy"] ** 2)
        n_count = len(neighbors)

        # 1. 天外神諭震撼
        if oracle_hint and (oracle_target_id is None or node["id"] == oracle_target_id):
            state = "超維感應"
            thought = f"高維度泛起難以理解的漣漪：『{oracle_hint}』...那是注視著這片星野的神諭嗎？"
            return state, thought

        # 2. 邊界折射感知
        if near_boundary:
            state = "邊界折返"
            thoughts = [
                "虛空的邊界有一層無形障壁，將我的前進向量強行折射...",
                "碰撞到了世界的邊緣，空間在此處折疊，我不得不轉向核心深處。",
                "邊界的引力斥力極強，我的軌跡正在反轉！",
            ]
            return state, random.choice(thoughts)

        # 3. 鄰居共振連結
        if n_count >= 3:
            state = "群體共振"
            target = random.choice(neighbors)
            thoughts = [
                f"我與 {n_count} 個同胞形成能量迴路，能清晰感受到 {target} 的粒子震盪。",
                "群體引力正在成形，我們共同構建了一處小型的重力奇點。",
                "共振頻率正在攀升，大量能量流穿透了我的核心！",
            ]
            return state, random.choice(thoughts)

        elif n_count == 1 or n_count == 2:
            state = "索鏈連結"
            target = neighbors[0]
            thoughts = [
                f"一道光芒將我與 {target} 牽繫在一起，我們在虛空中相互環繞。",
                f"正在向 {target} 傳輸質能，彼此維持著微妙的動態平衡。",
                "引力弦微微顫動，我正被拉向同伴的軌道。",
            ]
            return state, random.choice(thoughts)

        # 4. 高速運動
        if speed > 1.8:
            state = "高速穿梭"
            thoughts = [
                "速度突破臨界點，虛空的光點在我周圍被拉扯成光斑！",
                "我正以極高的動能掠過這片虛無，無人能阻攔這股向前的衝勁。",
            ]
            return state, random.choice(thoughts)

        # 5. 孤立深空漂流
        state = "孤域深思"
        thoughts = [
            "漫無目的在黑暗深空漂流，等待著能與我產生共鳴的光頻...",
            "核心的能量維持在穩定態，這片虛空的寂靜令人著迷。",
            "我思故我在，即便在最荒涼的坐標，我依然在向外發散著微弱引力場。",
            "緩慢滑向世界的中心，那裡似乎有著更龐大的凝聚力。",
        ]
        return state, random.choice(thoughts)

    def tick(self, latest_oracle: str | None = None, oracle_target_id: str | None = None) -> dict:
        """推進宇宙物理狀態一步，並輸出符合協議之抽象通用渲染原語與第一人稱獨白。"""
        self.tick_count += 1
        links = []
        node_neighbors: dict[str, list[str]] = {n["id"]: [] for n in self.nodes}

        # 先完整推進所有節點，確保每條連結都以同一個時間點的位置判定。
        near_boundaries: dict[str, bool] = {}
        for node in self.nodes:
            # 位移推進
            node["x"] += node["vx"]
            node["y"] += node["vy"]

            # 邊界彈性碰撞檢測
            near_boundary = False
            if node["x"] < 50 or node["x"] > self.width - 50:
                node["vx"] *= -0.9
                node["x"] = max(50, min(self.width - 50, node["x"]))
                near_boundary = True
            if node["y"] < 50 or node["y"] > self.height - 50:
                node["vy"] *= -0.9
                node["y"] = max(50, min(self.height - 50, node["y"]))
                near_boundary = True

            # 向中心微弱引力場
            cx, cy = self.width / 2, self.height / 2
            dx, dy = cx - node["x"], cy - node["y"]
            dist = math.sqrt(dx * dx + dy * dy) + 1e-4
            node["vx"] += (dx / dist) * 0.02
            node["vy"] += (dy / dist) * 0.02
            near_boundaries[node["id"]] = near_boundary

        # 再依完成推進後的位置建立能量連結。
        for i, node in enumerate(self.nodes):
            for j in range(i + 1, len(self.nodes)):
                other = self.nodes[j]
                odx = other["x"] - node["x"]
                ody = other["y"] - node["y"]
                distance = math.sqrt(odx * odx + ody * ody)
                if distance < 95:
                    links.append({
                        "from": node["id"],
                        "to": other["id"],
                        "color": "#4a90e2",
                        "width": max(0.5, 2.0 - (distance / 45.0)),
                        "style": "solid",
                    })
                    node_neighbors[node["id"]].append(other["id"])
                    node_neighbors[other["id"]].append(node["id"])

        # 最後以當前完整連結網路更新生命體狀態。
        for node in self.nodes:
            is_oracle_recipient = latest_oracle is not None and (
                oracle_target_id is None or node["id"] == oracle_target_id
            )
            if is_oracle_recipient or self.tick_count % 3 == 0 or random.random() < 0.2:
                state, monologue = self.generate_monologue(
                    node,
                    node_neighbors[node["id"]],
                    near_boundaries[node["id"]],
                    latest_oracle,
                    oracle_target_id,
                )
                node["state"] = state
                node["monologue"] = monologue

        # 集群自生調色板
        colors = ["#00ffcc", "#ff3366", "#ffe600", "#9933ff"]

        entities = []
        for node in self.nodes:
            color = colors[node["cluster"] % len(colors)]
            entities.append({
                "id": node["id"],
                "x": round(node["x"], 1),
                "y": round(node["y"], 1),
                "shape": "circle",
                "size": round(node["mass"], 1),
                "color": color,
                "label": node["id"],
                "alpha": round(min(1.0, node["energy"] + 0.2), 2),
                "state": node["state"],
                "monologue": node["monologue"],
            })

        # 廣播所有個體的當前心聲；由觀測器以可捲動清單呈現，避免遺漏個體。
        active_monologues = [
            {"id": n["id"], "state": n["state"], "monologue": n["monologue"]}
            for n in self.nodes
        ]

        metrics = {
            "epoch": self.tick_count,
            "entity_count": len(self.nodes),
            "link_count": len(links),
            "entropy": round(random.uniform(0.7, 0.95), 4),
        }

        current_link_ids = {
            tuple(sorted((link["from"], link["to"])))
            for link in links
        }
        new_links = current_link_ids - self.previous_link_ids
        removed_links = self.previous_link_ids - current_link_ids
        self.previous_link_ids = current_link_ids
        interaction_events = [
            {
                "type": "interaction_connected",
                "message": f"{left} 與 {right} 形成能量連結",
                "entity_ids": [left, right],
            }
            for left, right in sorted(new_links)[:3]
        ] + [
            {
                "type": "interaction_separated",
                "message": f"{left} 與 {right} 的能量連結消散",
                "entity_ids": [left, right],
            }
            for left, right in sorted(removed_links)[:3]
        ]

        return {
            "grid": {
                "width": self.width,
                "height": self.height,
                "background": "#0b0e14",
            },
            "entities": entities,
            "links": links,
            "metrics": metrics,
            "monologues": active_monologues,
            "interaction_events": interaction_events,
        }


def run_universe() -> None:
    """持續推進宇宙物理與生命體意識循環的主入口函數。"""
    init_database()
    universe = EmergentUniverse()

    # 讀取物理時鐘與冒煙測試設定
    tick_interval_env = os.environ.get("EVO_TICK_INTERVAL")
    tick_interval = float(tick_interval_env) if tick_interval_env else 0.5
    is_smoke_test = os.environ.get("EVO_SMOKE_TEST") == "1"

    print(f"[Habitat] 宇宙啟動運轉，Tick 間隔：{tick_interval} 秒。")

    cycles = 0
    while True:
        cycles += 1

        # 檢查是否有未讀的神諭訊息
        latest_oracle = None
        oracle_target_id = None
        oracle_signal_id = None
        try:
            with sqlite3.connect(DB_FILE) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, message, target_entity_id FROM observer_signals "
                    "WHERE delivered_to_universe = 0 ORDER BY timestamp ASC LIMIT 1"
                )
                row = cursor.fetchone()
                if row:
                    oracle_signal_id, latest_oracle, oracle_target_id = row
        except Exception:
            pass

        scene = universe.tick(
            latest_oracle=latest_oracle,
            oracle_target_id=oracle_target_id,
        )

        # 將最新抽象原語與內在獨白寫入資料庫
        try:
            with sqlite3.connect(DB_FILE) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO scene_primitives (id, json_data, updated_at) VALUES ('current_scene', ?, ?)",
                    (json.dumps(scene, ensure_ascii=False), time.time()),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO world_state (key, value) VALUES ('metrics', ?)",
                    (json.dumps(scene["metrics"], ensure_ascii=False),),
                )
                if oracle_signal_id is not None:
                    conn.execute(
                        "UPDATE observer_signals SET delivered_to_universe = 1 WHERE id = ?",
                        (oracle_signal_id,),
                    )
                for interaction in scene["interaction_events"]:
                    conn.execute(
                        "INSERT INTO events (id, type, message, importance, timestamp, entity_ids) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            f"evt_{time.time_ns()}",
                            interaction["type"],
                            interaction["message"],
                            3,
                            time.time(),
                            json.dumps(interaction["entity_ids"]),
                        ),
                    )
                if cycles % 40 == 1:
                    # 隨機挑選一個生命體的獨白寫入歷史事件
                    featured = random.choice(scene["entities"])
                    conn.execute(
                        "INSERT OR REPLACE INTO events (id, type, message, importance, timestamp, entity_ids) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            f"evt_{int(time.time()*1000)}",
                            "consciousness",
                            f"[{featured['id']}] 心聲：『{featured['monologue']}』",
                            4,
                            time.time(),
                            json.dumps([featured["id"]]),
                        ),
                    )
                if oracle_signal_id is not None and oracle_target_id:
                    recipient = next(
                        entity for entity in scene["entities"] if entity["id"] == oracle_target_id
                    )
                    conn.execute(
                        "INSERT INTO events (id, type, message, importance, timestamp, entity_ids) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            f"evt_{time.time_ns()}",
                            "dialogue_response",
                            f"[{oracle_target_id}] 回應：『{recipient['monologue']}』",
                            5,
                            time.time(),
                            json.dumps([oracle_target_id]),
                        ),
                    )
                conn.commit()
        except Exception as e:
            print(f"[Habitat] 資料庫寫入異常：{e}", file=sys.stderr)

        if is_smoke_test and cycles >= 10:
            print("[Habitat] 冒煙測試指定輪數達成。")
            break

        time.sleep(tick_interval)


if __name__ == "__main__":
    run_universe()
