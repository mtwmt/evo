"""FastAPI 觀測服務端主程式：提供 WebSocket 串流與 REST 控制 API。"""

import asyncio
import json
import sqlite3
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cli.factory import (
    get_models_map,
    switch_adapter,
    switch_effort,
    switch_model,
)
from config.settings import DB_PATH, HABITAT_DIR, OBSERVER_DIR, config, save_config
from core.context.manager import ContextManager
from core.heartbeat.scheduler import CognitiveScheduler
from core.lifecycle.snapshot import LifecycleManager
from core.resource.governor import ResourceGovernor
from core.review.guardian import Guardian
from core.runtime.supervisor import RuntimeSupervisor
from observer.bridge.event_bridge import ObserverBridge

# 初始化外層治理實例
governor = ResourceGovernor()
bridge = ObserverBridge(db_path=DB_PATH, governor=governor)
supervisor = RuntimeSupervisor(habitat_dir=HABITAT_DIR)
context_manager = ContextManager(db_path=DB_PATH)
guardian = Guardian()
lifecycle_mgr = LifecycleManager(habitat_dir=HABITAT_DIR)
scheduler = CognitiveScheduler(
    supervisor=supervisor,
    context_manager=context_manager,
    guardian=guardian,
    lifecycle_manager=lifecycle_mgr,
)

# 追蹤連線中的 WebSocket 用戶端
connected_clients: list[WebSocket] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    """應用程式生命週期管理：啟動宇宙進程與後台節拍調度。"""
    print("[Evo Server] 服務啟動，初始化宇宙進程...")
    # 若 habitat/main.py 存在，嘗試啟動宇宙主程序
    if (HABITAT_DIR / "main.py").exists() and not config.paused:
        supervisor.start(tick_interval=config.universe_tick_interval)

    # 啟動非同步認知節拍排程
    scheduler_task = asyncio.create_task(scheduler.run_loop())
    yield
    print("[Evo Server] 正在停止宇宙進程與排程任務...")
    scheduler.stop_loop()
    scheduler_task.cancel()
    supervisor.stop()


app = FastAPI(title="Evo Universe Observer API", lifespan=lifespan)

# 跨來源資源共用（CORS）中介層
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# === 請求模型定義 ===
class SignalRequest(BaseModel):
    sender: str = "Observer"
    message: str
    target_entity_id: str | None = None




class CLISwitchRequest(BaseModel):
    cli: Literal["agy", "codex", "claude"]
    model: str | None = None


class ModelSwitchRequest(BaseModel):
    model: str


class SpeedRequest(BaseModel):
    speed: Literal["1x", "3x", "MAX"]


class EffortRequest(BaseModel):
    cli: Literal["codex", "claude"]
    effort: str


class PauseRequest(BaseModel):
    paused: bool


# === REST API 路由 ===
@app.get("/api/status")
async def get_system_status():
    """取得當前外層系統與宇宙運行狀態。"""
    pid = supervisor.get_pid()
    return bridge.get_telemetry_snapshot(target_pid=pid)


@app.post("/api/observer/signal")
async def send_observer_signal(req: SignalRequest):
    """將觀測者神諭對話訊號寫入資料庫信箱。"""
    if not DB_PATH.exists():
        raise HTTPException(status_code=503, detail="宇宙資料庫尚未初始化")

    try:
        timestamp = time.time()
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO observer_signals (sender, message, target_entity_id, timestamp, processed, delivered_to_universe) VALUES (?, ?, ?, ?, 0, 0)",
                (req.sender, req.message, req.target_entity_id, timestamp),
            )
            if req.target_entity_id:
                conn.execute(
                    "INSERT INTO events (id, type, message, importance, timestamp, entity_ids) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        f"evt_{time.time_ns()}",
                        "dialogue_request",
                        f"[{req.target_entity_id}] 收到觀測者訊號：『{req.message}』",
                        5,
                        timestamp,
                        json.dumps([req.target_entity_id]),
                    ),
                )
            conn.commit()
        target = f"角色 {req.target_entity_id}" if req.target_entity_id else "宇宙"
        return {"status": "success", "message": f"訊號已送往{target}，將於下一個 tick 回應"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"寫入訊號失敗：{exc}")


@app.post("/api/control/cli")
async def switch_cli_adapter(req: CLISwitchRequest):
    """切換當前啟用的 CLI 適配器（下個節拍生效，失敗不自動 fallback）。"""
    try:
        adapter = switch_adapter(req.cli, model=req.model)
        return {
            "status": "success",
            "active_cli": adapter.name,
            "active_model": adapter.current_model,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/control/model")
async def switch_cli_model(req: ModelSwitchRequest):
    """切換當前 CLI 適配器使用之模型。"""
    try:
        current = switch_model(req.model)
        return {"status": "success", "active_model": current}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/control/models")
async def get_available_models():
    """取得當前各適配器所支援之可用模型清單。"""
    return {
        "active_cli": config.active_cli,
        "active_model": config.active_model,
        "codex_effort": config.codex_effort,
        "claude_effort": config.claude_effort,
        "models": get_models_map(),
    }


@app.post("/api/control/effort")
async def set_cli_effort(req: EffortRequest):
    """設定指定 CLI 的推理速度／強度。"""
    try:
        effort = switch_effort(req.effort, req.cli)
        return {"status": "success", "cli": req.cli, "effort": effort}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/control/speed")
async def set_speed_mode(req: SpeedRequest):
    """設定宇宙模擬與思考速度模式（1x / 3x / MAX）。"""
    config.speed_mode = req.speed
    save_config()
    # 若宇宙運作中，即時重啟以套用新 tick 間隔
    if supervisor.is_running():
        supervisor.restart(tick_interval=config.universe_tick_interval)
    return {
        "status": "success",
        "speed_mode": config.speed_mode,
        "tick_interval": config.universe_tick_interval,
        "heartbeat_cooldown": config.heartbeat_cooldown,
    }


@app.post("/api/control/pause")
async def toggle_pause(req: PauseRequest):
    """暫停或恢復宇宙運行。"""
    config.paused = req.paused
    save_config()
    if config.paused:
        supervisor.stop()
    else:
        supervisor.start(tick_interval=config.universe_tick_interval)
    return {"status": "success", "paused": config.paused}


@app.get("/api/habitat/code")
async def get_habitat_code():
    """唯讀檢視 habitat/ 目錄下之所有程式碼內容。"""
    if not HABITAT_DIR.exists():
        return {"files": {}}

    files: dict[str, str] = {}
    for path in HABITAT_DIR.rglob("*.py"):
        if path.is_file():
            rel_path = str(path.relative_to(HABITAT_DIR))
            try:
                files[rel_path] = path.read_text(encoding="utf-8")
            except Exception:
                pass
    return {"files": files}


@app.get("/api/habitat/history")
async def get_history_timeline(limit: int = 50, entity_id: str | None = None):
    """撈取歷史事件與重大里程碑。"""
    if not DB_PATH.exists():
        return {"events": []}

    events = []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            if entity_id:
                cursor.execute(
                    "SELECT id, type, message, importance, timestamp, entity_ids FROM events "
                    "WHERE entity_ids LIKE ? OR message LIKE ? ORDER BY timestamp DESC LIMIT ?",
                    (f'%"{entity_id}"%', f"%[{entity_id}]%", limit),
                )
            else:
                cursor.execute(
                    "SELECT id, type, message, importance, timestamp, entity_ids FROM events "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                )
            for row in cursor.fetchall():
                events.append({
                    "id": row[0],
                    "type": row[1],
                    "message": row[2],
                    "importance": row[3],
                    "timestamp": row[4],
                    "entity_ids": json.loads(row[5]) if row[5] else [],
                })
    except Exception:
        pass
    return {"events": events}


# === WebSocket 串流 ===
@app.websocket("/ws/universe")
async def websocket_universe_stream(websocket: WebSocket):
    """透過 WebSocket 持續向觀測前端推送抽象幾何原語與遙測數據。"""
    await websocket.accept()
    connected_clients.append(websocket)
    try:
        while True:
            pid = supervisor.get_pid()
            snapshot = bridge.get_telemetry_snapshot(target_pid=pid)
            await websocket.send_text(json.dumps(snapshot, ensure_ascii=False))
            # 宇宙每 0.5 秒推進一次；4Hz 足以呈現變化並避免重複 I/O。
            await asyncio.sleep(0.25)
    except WebSocketDisconnect:
        if websocket in connected_clients:
            connected_clients.remove(websocket)
    except Exception:
        if websocket in connected_clients:
            connected_clients.remove(websocket)


# 若前端已編譯完成，掛載靜態資源目錄
dist_dir = OBSERVER_DIR / "web" / "dist"
if dist_dir.exists():
    app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="static")
