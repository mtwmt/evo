"""FastAPI 觀測端 REST API 與控制端點測試。"""

import asyncio
import json
import sqlite3
import threading
import uuid

import pytest
from starlette.testclient import TestClient

from config.settings import config
from core.resource.governor import ResourceGovernor, ResourceMetrics
from observer.server import app as server_app
from observer.server.app import app


def init_database(db_path) -> None:
    """建立觀測器測試所需的最小通用協議，不依賴任何特定宇宙。"""
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
            CREATE TABLE creative_works (
                id TEXT PRIMARY KEY, creator_id TEXT, title TEXT, medium TEXT,
                mime_type TEXT, content BLOB, metadata_json TEXT, epoch INTEGER,
                created_at REAL
            );
            """
        )


def archive_test_work(connection: sqlite3.Connection, **work) -> str:
    """寫入 API 測試作品，不借用 habitat 內可能不存在的世界模組。"""
    work_id = f"work-{uuid.uuid4().hex}"
    connection.execute(
        "INSERT INTO creative_works "
        "(id, creator_id, title, medium, mime_type, content, metadata_json, epoch, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
        (
            work_id,
            work["creator_id"],
            work["title"],
            work["medium"],
            work["mime_type"],
            work["content"],
            json.dumps(work.get("metadata"), ensure_ascii=False),
            work.get("epoch"),
        ),
    )
    return work_id


@pytest.fixture(autouse=True)
def setup_test_environment(tmp_path, monkeypatch):
    """設定測試專用資料庫與隔離環境。"""
    monkeypatch.setattr("config.settings.RUNTIME_CONFIG_PATH", tmp_path / "runtime_config.json")
    monkeypatch.setattr(server_app, "_model_discovery_cancelled", threading.Event())
    monkeypatch.setattr(server_app, "_model_discovery_tasks", set())
    monkeypatch.setattr(server_app, "_pause_control_lock", asyncio.Lock())
    test_db = tmp_path / "habitat.db"
    init_database(test_db)
    test_governor = ResourceGovernor(habitat_dir=tmp_path)
    monkeypatch.setattr("observer.server.app.DB_PATH", test_db)
    monkeypatch.setattr("observer.server.app.bridge.db_path", test_db)
    monkeypatch.setattr("observer.server.app.governor", test_governor)
    monkeypatch.setattr("observer.server.app.bridge.governor", test_governor)
    monkeypatch.setattr("observer.server.app.context_manager.db_path", test_db)
    monkeypatch.setattr(
        "observer.server.app.get_models_map",
        lambda cancel_event=None: {"agy": ["mock-agy"], "codex": ["mock-codex"], "claude": ["mock-claude"]},
    )
    for field in (
        "active_cli",
        "active_model",
        "codex_effort",
        "claude_effort",
        "speed_mode",
        "paused",
    ):
        monkeypatch.setattr(config, field, getattr(config, field))
    for field, value in (
        ("_pause_requested", False),
        ("_shutdown_requested", False),
        ("_resource_blocked", False),
        ("_resource_block_reason", None),
        ("_generation", 0),
        ("current_turn_in_progress", False),
        ("sleep_cycles_remaining", 0),
        ("last_heartbeat_time", 0.0),
        ("last_status", {"status": "idle"}),
    ):
        monkeypatch.setattr("observer.server.app.scheduler." + field, value)


def test_api_status_endpoint():
    """驗證 /api/status 端點正確回傳遙測指標與外層配置。"""
    client = TestClient(app)
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert "runtime" in data
    assert "resources" in data
    assert "cpu_sampled" in data["resources"]
    assert data["runtime"]["active_cli"] in ("agy", "codex", "claude")
    assert "active_model" in data["runtime"]
    assert "available_models" not in data["runtime"]


def test_blank_habitat_observer_endpoints_remain_available(tmp_path, monkeypatch):
    """外部觀測服務在首次創世前不依賴任何 habitat 程式或資料庫。"""
    blank_habitat = tmp_path / "blank-habitat"
    blank_habitat.mkdir()
    missing_db = blank_habitat / "habitat.db"
    monkeypatch.setattr(server_app, "HABITAT_DIR", blank_habitat)
    monkeypatch.setattr(server_app, "DB_PATH", missing_db)
    monkeypatch.setattr(server_app.bridge, "db_path", missing_db)

    client = TestClient(app)

    assert client.get("/api/status").status_code == 200
    assert client.get("/api/habitat/code").json() == {"files": {}}
    assert client.get("/api/habitat/works").json() == {"works": []}
    assert client.get("/api/habitat/history").json() == {
        "events": [],
        "milestones": [],
        "current_epoch": 0,
        "civilization_stage": "",
    }


def test_code_view_does_not_follow_external_links(tmp_path, monkeypatch):
    habitat = tmp_path / "habitat"
    habitat.mkdir()
    outside = tmp_path / "secret.py"
    outside.write_text("SECRET = 'outside'")
    (habitat / "main.py").write_text("VALUE = 'inside'")
    (habitat / "linked.py").symlink_to(outside)
    (habitat / "linked-directory").symlink_to(tmp_path, target_is_directory=True)
    (habitat / "hardlink.py").hardlink_to(outside)
    monkeypatch.setattr(server_app, "HABITAT_DIR", habitat)
    assert TestClient(app).get("/api/habitat/code").json() == {
        "files": {"main.py": "VALUE = 'inside'"},
    }


def test_api_observer_signal_endpoint(tmp_path):
    """驗證 /api/observer/signal 能成功將神諭訊號寫入資料庫信箱。"""
    client = TestClient(app)
    response = client.post(
        "/api/observer/signal",
        json={
            "sender": "Observer_1",
            "message": "宇宙熵值偏高，請嘗試降低引力。",
            "target_entity_id": "node_1",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    with sqlite3.connect(tmp_path / "habitat.db") as conn:
        event_type, entity_ids = conn.execute(
            "SELECT type, entity_ids FROM events ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
    assert event_type == "dialogue_request"
    assert entity_ids == '["node_1"]'


def test_creative_work_endpoints_return_archived_original(tmp_path):
    """作品目錄只回傳中繼資料，原始內容由專用端點完整取得。"""
    db_path = tmp_path / "habitat.db"
    with sqlite3.connect(db_path) as conn:
        work_id = archive_test_work(
            conn,
            creator_id="Aether-20",
            title="星塵畫作",
            medium="painting",
            content=b"\x89PNG\r\n",
            mime_type="image/png",
            epoch=3292,
        )

    client = TestClient(app)
    listing = client.get("/api/habitat/works")
    assert listing.status_code == 200
    assert listing.json()["works"][0]["id"] == work_id
    assert "content" not in listing.json()["works"][0]

    original = client.get(f"/api/habitat/works/{work_id}")
    assert original.status_code == 200
    assert original.content == b"\x89PNG\r\n"
    assert original.headers["content-type"].startswith("image/png")


@pytest.mark.parametrize("mime_type", ["text/html", "image/svg+xml", "text/html\r\nX-Test: injected"])
def test_active_creative_content_is_downloaded_without_same_origin_scripts(tmp_path, mime_type):
    with sqlite3.connect(tmp_path / "habitat.db") as conn:
        work_id = archive_test_work(
            conn, creator_id="author", title="作品", medium="code",
            content=b"<script>fetch('/api/status')</script>", mime_type=mime_type,
        )
    response = TestClient(app).get(f"/api/habitat/works/{work_id}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]


def test_history_endpoint_separates_milestones_and_reports_world_epoch(tmp_path):
    """時間線同時提供近期事件、重大事件與目前宇宙紀年。"""
    db_path = tmp_path / "habitat.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO world_state (key, value) VALUES ('metrics', ?)",
            (json.dumps({"epoch": 321, "civilization_stage": "共生紀"}),),
        )
        conn.executemany(
            "INSERT INTO events "
            "(id, type, message, importance, timestamp, entity_ids, epoch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("minor", "consciousness", "日常心聲", 3, 1.0, "[]", 320),
                ("major", "epoch_transition", "文明躍遷", 9, 2.0, '["Aether-08"]', 321),
                ("chronicle", "chronicle", "共生紀正式編年", 9, 2.1, "[]", 321),
                ("creation", "creation", "意識完成宏偉作品", 10, 3.0, '["Aether-08"]', 321),
            ],
        )
        conn.commit()

    response = TestClient(app).get("/api/habitat/history?limit=15")

    assert response.status_code == 200
    data = response.json()
    assert data["current_epoch"] == 321
    assert data["civilization_stage"] == "共生紀"
    assert [event["id"] for event in data["events"]] == ["creation", "chronicle", "major", "minor"]
    assert [event["id"] for event in data["milestones"]] == ["chronicle"]
    assert data["milestones"][0]["epoch"] == 321


def test_history_falls_back_to_individual_epoch_state_keys():
    """世界若分別儲存 epoch 與 epoch_name，時間線仍能顯示目前紀元。"""
    with sqlite3.connect(server_app.DB_PATH) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO world_state (key, value) VALUES (?, ?)",
            [
                ("metrics", json.dumps({"紀元": "太古幽暗"})),
                ("epoch", "1"),
                ("epoch_name", "太古幽暗"),
            ],
        )

    response = TestClient(app).get("/api/habitat/history")

    assert response.status_code == 200
    data = response.json()
    assert data["current_epoch"] == 1
    assert data["civilization_stage"] == "太古幽暗"


def test_history_recognizes_self_named_epoch_and_formal_chronicle():
    """宇宙可沿用既有 metrics/events 自由命名紀元與留下正式編年。"""
    with sqlite3.connect(server_app.DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO world_state (key, value) VALUES ('metrics', ?)",
            (json.dumps({"epoch": 8, "epoch_name": "初次回聲紀"}),),
        )
        conn.execute(
            "INSERT INTO events "
            "(id, type, message, importance, timestamp, entity_ids, epoch) "
            "VALUES ('voice', 'chronicle', '初次回聲紀正式開啟，世界開始辨認自身回聲。', 5, 3, '[]', 8)"
        )

    response = TestClient(app).get("/api/habitat/history")

    assert response.status_code == 200
    data = response.json()
    assert data["civilization_stage"] == "初次回聲紀"
    assert [event["id"] for event in data["milestones"]] == ["voice"]


def test_api_control_speed_endpoint():
    """驗證 /api/control/speed 能動態調節演化速度模式。"""
    client = TestClient(app)
    response = client.post(
        "/api/control/speed",
        json={"speed": "3x"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["speed_mode"] == "3x"
    assert data["tick_interval"] == pytest.approx(1.0 / 3.0)
    assert data["heartbeat_cooldown"] == 300.0
    assert config.speed_mode == "3x"


def test_speed_restart_failure_restores_saved_speed(tmp_path, monkeypatch):
    """速度重啟失敗時保存並重新套用原速度，不留下虛假的新模式。"""
    monkeypatch.setattr(config, "paused", False)
    old_speed = config.speed_mode
    restart_calls = []

    def fail_restart(tick_interval=None):
        restart_calls.append(tick_interval)
        if len(restart_calls) == 1:
            raise RuntimeError("mock startup failure")
        return True

    monkeypatch.setattr(server_app.supervisor, "is_running", lambda: True)
    monkeypatch.setattr(server_app.scheduler, "restart_runtime", fail_restart)

    response = TestClient(app).post("/api/control/speed", json={"speed": "MAX"})

    assert response.status_code == 503
    assert response.json()["detail"].startswith("速度切換失敗")
    assert config.speed_mode == old_speed
    assert restart_calls == [0.05, config.universe_tick_interval]
    saved_config = json.loads((tmp_path / "runtime_config.json").read_text(encoding="utf-8"))
    assert saved_config["speed_mode"] == old_speed


def test_api_control_pause_endpoint():
    """驗證 /api/control/pause 能控制宇宙暫停與恢復狀態。"""
    client = TestClient(app)
    response = client.post(
        "/api/control/pause",
        json={"paused": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["paused"] is True
    assert config.paused is True


def test_api_control_model_endpoints(monkeypatch):
    """驗證 /api/control/models 查詢與 /api/control/model 動態切換模型。"""
    client = TestClient(app)

    monkeypatch.setattr(config, "paused", False)

    # 1. 查詢可用模型
    res_list = client.get("/api/control/models")
    assert res_list.status_code == 200
    models_data = res_list.json()
    assert "models" in models_data
    assert "agy" in models_data["models"]

    # 2. 切換模型
    res_switch = client.post(
        "/api/control/model",
        json={"model": "gemini-3.8-flash-low"},
    )
    assert res_switch.status_code == 200
    assert res_switch.json()["active_model"] == "gemini-3.8-flash-low"
    assert config.active_model == "gemini-3.8-flash-low"


def test_api_control_effort_endpoint():
    """驗證所有 CLI 的推理速度／強度共用同一個控制端點。"""
    client = TestClient(app)
    response = client.post("/api/control/effort", json={"cli": "codex", "effort": "xhigh"})
    assert response.status_code == 200
    assert response.json()["effort"] == "xhigh"
    assert config.codex_effort == "xhigh"

    response = client.post("/api/control/effort", json={"cli": "claude", "effort": "high"})
    assert response.status_code == 200
    assert response.json()["effort"] == "high"
    assert config.claude_effort == "high"


def test_api_history_can_filter_by_entity(tmp_path, monkeypatch):
    """選取個體時，歷程 API 只回傳與該 node 有關的事件。"""
    db_path = tmp_path / "history.db"
    init_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO events (id, type, message, importance, timestamp, entity_ids) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("a", "interaction_connected", "node_1 與 node_2 形成能量連結", 3, 1.0, '["node_1", "node_2"]'),
        )
        conn.execute(
            "INSERT INTO events (id, type, message, importance, timestamp, entity_ids) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("b", "interaction_connected", "node_3 與 node_4 形成能量連結", 3, 2.0, '["node_3", "node_4"]'),
        )
        conn.commit()
    monkeypatch.setattr("observer.server.app.DB_PATH", db_path)

    response = TestClient(app).get("/api/habitat/history?entity_id=node_1")

    assert response.status_code == 200
    assert [event["id"] for event in response.json()["events"]] == ["a"]


@pytest.mark.asyncio
async def test_resource_monitor_enforces_limits_without_websocket_clients(monkeypatch):
    """沒有連線的 observer 時仍會背景採取停止措施。"""
    violation = ResourceMetrics(
        cpu_percent=0.0,
        ram_bytes=0,
        subprocess_count=0,
        habitat_size_bytes=99,
        is_healthy=False,
        warning_message="mock disk limit",
    )
    blocked = asyncio.Event()
    reasons = []

    class StubGovernor:
        def check_limits(self, pid):
            return violation

    class StubSupervisor:
        resource_block_reason = None

        def get_pid(self):
            return None

    class StubScheduler:
        def block_runtime_for_resources(self, reason):
            reasons.append(reason)
            blocked.set()

        def _publish_status(self, status):
            raise AssertionError(status)

    monkeypatch.setattr(server_app, "governor", StubGovernor())
    monkeypatch.setattr(server_app, "supervisor", StubSupervisor())
    monkeypatch.setattr(server_app, "scheduler", StubScheduler())
    monkeypatch.setattr(server_app, "_resource_monitor_interval", lambda governor: 0.01)

    task = asyncio.create_task(server_app.resource_monitor_loop())
    try:
        await asyncio.wait_for(blocked.wait(), timeout=1.0)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert reasons == ["mock disk limit"]


@pytest.mark.asyncio
async def test_pause_cancels_model_lookup_and_closes_every_stream(monkeypatch):
    """模型探索不能阻塞暫停；暫停成功前需完成探索清理並關閉所有串流。"""
    started = threading.Event()
    stopped = threading.Event()
    clients = []

    class FakeClient:
        closed = False

        async def close(self, code, reason):
            assert code == 1001 and reason == "宇宙已暫停"
            self.closed = True

    clients = [FakeClient(), FakeClient()]

    def lookup(cancel_event=None):
        started.set()
        assert cancel_event.wait(timeout=2)
        stopped.set()
        return {}

    monkeypatch.setattr(config, "paused", False)
    monkeypatch.setattr(server_app, "get_models_map", lookup)
    monkeypatch.setattr(server_app, "connected_clients", clients.copy())
    monkeypatch.setattr(server_app.scheduler, "pause_runtime", lambda: setattr(config, "paused", True))
    lookup_task = asyncio.create_task(server_app.get_available_models())
    assert await asyncio.to_thread(started.wait, 1)
    result = await server_app.toggle_pause(server_app.PauseRequest(paused=True))
    await lookup_task
    assert result["paused"] is True
    assert stopped.is_set()
    assert all(client.closed for client in clients)
    assert server_app.connected_clients == []
    assert not server_app._model_discovery_tasks


@pytest.mark.asyncio
async def test_pause_failure_still_disconnects_streams(monkeypatch):
    """即使子程序清理失敗也斷線，但不能回報暫停成功。"""
    from fastapi import HTTPException

    closed = []

    class FakeClient:
        async def close(self, **kwargs):
            closed.append(kwargs)

    def fail_pause():
        config.paused = True
        raise RuntimeError("mock cleanup failure")

    monkeypatch.setattr(server_app, "connected_clients", [FakeClient()])
    monkeypatch.setattr(server_app.scheduler, "pause_runtime", fail_pause)
    with pytest.raises(HTTPException) as failure:
        await server_app.toggle_pause(server_app.PauseRequest(paused=True))
    assert failure.value.status_code == 503
    assert closed
    assert server_app._model_discovery_cancelled.is_set()
