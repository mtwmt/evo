"""FastAPI 觀測端 REST API 與控制端點測試。"""

import sqlite3

import pytest
from starlette.testclient import TestClient

from config.settings import config
from habitat.main import init_database
from observer.server.app import app


@pytest.fixture(autouse=True)
def setup_test_environment(tmp_path, monkeypatch):
    """設定測試專用資料庫與隔離環境。"""
    monkeypatch.setattr("config.settings.RUNTIME_CONFIG_PATH", tmp_path / "runtime_config.json")
    test_db = tmp_path / "habitat.db"
    init_database(test_db)
    monkeypatch.setattr("observer.server.app.DB_PATH", test_db)
    monkeypatch.setattr("observer.server.app.bridge.db_path", test_db)
    monkeypatch.setattr("observer.server.app.context_manager.db_path", test_db)


def test_api_status_endpoint():
    """驗證 /api/status 端點正確回傳遙測指標與外層配置。"""
    client = TestClient(app)
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert "runtime" in data
    assert "resources" in data
    assert data["runtime"]["active_cli"] in ("agy", "codex", "claude")
    assert "active_model" in data["runtime"]
    assert "available_models" not in data["runtime"]


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
    assert config.speed_mode == "3x"


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


def test_api_control_model_endpoints():
    """驗證 /api/control/models 查詢與 /api/control/model 動態切換模型。"""
    client = TestClient(app)

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
