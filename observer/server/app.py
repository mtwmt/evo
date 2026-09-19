"""FastAPI 觀測服務端主程式：提供 WebSocket 串流與 REST 控制 API。"""

import asyncio
import json
import os
import sqlite3
import stat
import threading
import time
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cli.factory import (
    cancel_all_cli_processes,
    get_models_map,
    switch_adapter,
    switch_effort,
    switch_model,
)
from config.settings import DB_PATH, HABITAT_DIR, OBSERVER_DIR, config, save_config
from core.context.manager import ContextManager
from core.heartbeat.scheduler import CognitiveScheduler
from core.lifecycle.snapshot import LifecycleManager
from core.resource.governor import ResourceGovernor, _resource_monitor_interval
from core.review.guardian import Guardian
from core.runtime.supervisor import RuntimeSupervisor
from observer.bridge.creative_archive import list_creative_works, read_creative_work
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
_model_discovery_cancelled = threading.Event()
_model_discovery_tasks: set[asyncio.Task] = set()
_pause_control_lock = asyncio.Lock()


async def disconnect_universe_clients() -> None:
    """暫停時關閉所有場景串流，避免前端誤把舊快照當成仍在演化。"""
    clients = connected_clients.copy()
    connected_clients.clear()
    for client in clients:
        try:
            await client.close(code=1001, reason="宇宙已暫停")
        except Exception:
            pass


async def resource_monitor_loop() -> None:
    """無論是否有 WebSocket 用戶端，都定期檢查並執行資源停止措施。"""
    while True:
        try:
            pid = supervisor.get_pid()
            metrics = await asyncio.to_thread(governor.check_limits, pid)
            if not metrics.is_healthy and metrics.warning_message:
                if supervisor.resource_block_reason != metrics.warning_message:
                    await asyncio.to_thread(
                        scheduler.block_runtime_for_resources,
                        metrics.warning_message,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            scheduler._publish_status(
                {"status": "resource_monitor_error", "reason": str(exc)}
            )
        await asyncio.sleep(_resource_monitor_interval(governor))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """應用程式生命週期管理：啟動宇宙進程與後台節拍調度。"""
    print("[Evo Server] 服務啟動，初始化宇宙進程...")
    # 若 runtime/habitat/main.py 存在，嘗試啟動宇宙主程序
    if (HABITAT_DIR / "main.py").exists() and not config.paused:
        with scheduler.lifecycle_lock:
            supervisor.start(tick_interval=config.universe_tick_interval)

    # 啟動非同步認知節拍排程
    scheduler_task = asyncio.create_task(scheduler.run_loop())
    resource_task = asyncio.create_task(resource_monitor_loop())
    try:
        yield
    finally:
        print("[Evo Server] 正在停止宇宙進程與排程任務...")
        scheduler.stop_loop()
        _model_discovery_cancelled.set()
        resource_task.cancel()
        await asyncio.gather(resource_task, return_exceptions=True)
        await scheduler.shutdown()
        if _model_discovery_tasks:
            await asyncio.gather(*_model_discovery_tasks, return_exceptions=True)
        await asyncio.gather(scheduler_task, return_exceptions=True)


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
    snapshot = bridge.get_telemetry_snapshot(target_pid=pid)
    snapshot["runtime"]["scheduler_status"] = dict(scheduler.last_status)
    snapshot["runtime"]["resource_block_reason"] = supervisor.resource_block_reason
    return snapshot


@app.post("/api/observer/signal")
async def send_observer_signal(req: SignalRequest):
    """將觀測者神諭對話訊號寫入資料庫信箱。"""
    if not DB_PATH.exists():
        raise HTTPException(status_code=503, detail="宇宙資料庫尚未初始化")

    try:
        timestamp = time.time()
        with sqlite3.connect(DB_PATH) as conn:
            epoch = 0
            metrics_row = conn.execute(
                "SELECT value FROM world_state WHERE key = 'metrics'"
            ).fetchone()
            if metrics_row:
                try:
                    epoch = int(json.loads(metrics_row[0]).get("epoch", 0))
                except (TypeError, ValueError, json.JSONDecodeError):
                    epoch = 0
            conn.execute(
                "INSERT INTO observer_signals (sender, message, target_entity_id, timestamp, processed, delivered_to_universe) VALUES (?, ?, ?, ?, 0, 0)",
                (req.sender, req.message, req.target_entity_id, timestamp),
            )
            if req.target_entity_id:
                event_values = (
                    f"evt_{time.time_ns()}",
                    "dialogue_request",
                    f"[{req.target_entity_id}] 收到觀測者訊號：『{req.message}』",
                    5,
                    timestamp,
                    json.dumps([req.target_entity_id]),
                )
                conn.execute(
                    "INSERT INTO events "
                    "(id, type, message, importance, timestamp, entity_ids, epoch) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (*event_values, epoch),
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
    models = {}
    if not config.paused and not _model_discovery_cancelled.is_set():
        task = asyncio.create_task(asyncio.to_thread(
            get_models_map, cancel_event=_model_discovery_cancelled,
        ))
        _model_discovery_tasks.add(task)
        task.add_done_callback(_model_discovery_tasks.discard)
        # 用戶端中止 HTTP 時，保留工作追蹤，暫停仍可等待子程序清理完成。
        models = await asyncio.shield(task)
    return {
        "active_cli": config.active_cli,
        "active_model": config.active_model,
        "codex_effort": config.codex_effort,
        "claude_effort": config.claude_effort,
        "models": models,
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
    def apply_speed() -> None:
        with scheduler.lifecycle_lock:
            previous_speed = config.speed_mode
            was_running = supervisor.is_running()
            config.speed_mode = req.speed
            try:
                if was_running and not config.paused:
                    scheduler.restart_runtime(tick_interval=config.universe_tick_interval)
                save_config()
            except Exception as exc:
                config.speed_mode = previous_speed
                restore_errors = []
                try:
                    save_config()
                except Exception as persist_exc:
                    restore_errors.append(f"設定檔還原失敗：{persist_exc}")

                if was_running and not config.paused:
                    try:
                        restored = scheduler.restart_runtime(
                            tick_interval=config.universe_tick_interval
                        )
                        if not restored:
                            restore_errors.append("原速度宇宙重啟因生命週期停止要求而跳過")
                    except Exception as restart_exc:
                        config.paused = True
                        try:
                            save_config()
                        except Exception as persist_exc:
                            restore_errors.append(f"暫停狀態保存失敗：{persist_exc}")
                        restore_errors.append(f"原速度宇宙重啟失敗：{restart_exc}")

                failure_reason = str(exc)
                if restore_errors:
                    failure_reason += "；" + "；".join(restore_errors)
                scheduler._publish_status({
                    "status": "speed_switch_failed",
                    "reason": failure_reason,
                    "restored_speed": previous_speed,
                    "runtime_running": supervisor.is_running(),
                })
                raise RuntimeError(
                    f"速度切換失敗，已恢復至 {previous_speed} 模式：{failure_reason}"
                ) from exc

    try:
        await asyncio.to_thread(apply_speed)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {
        "status": "success",
        "speed_mode": config.speed_mode,
        "tick_interval": config.universe_tick_interval,
        "heartbeat_cooldown": config.heartbeat_cooldown,
    }


@app.post("/api/control/pause")
async def toggle_pause(req: PauseRequest):
    """暫停或恢復宇宙運行。"""
    global _model_discovery_cancelled
    async with _pause_control_lock:
        if req.paused:
            _model_discovery_cancelled.set()
            try:
                await asyncio.to_thread(scheduler.pause_runtime)
                if _model_discovery_tasks:
                    await asyncio.wait_for(asyncio.shield(asyncio.gather(
                        *_model_discovery_tasks,
                    )), timeout=5.0)
                await asyncio.to_thread(cancel_all_cli_processes)
            except Exception as exc:
                raise HTTPException(status_code=503, detail=f"暫停清理未完成：{exc}") from exc
            finally:
                await disconnect_universe_clients()
                save_config()
        else:
            try:
                await asyncio.to_thread(
                    scheduler.resume_runtime,
                    config.universe_tick_interval,
                )
            except Exception as exc:
                raise HTTPException(status_code=503, detail=str(exc))
            # 舊回合持有的取消訊號維持 set，新連線使用新的 Event。
            _model_discovery_cancelled = threading.Event()
            save_config()
        return {"status": "success", "paused": config.paused}


@app.get("/api/habitat/code")
async def get_habitat_code():
    """唯讀檢視 runtime/habitat/ 目錄下之所有程式碼內容。"""
    if not HABITAT_DIR.exists():
        return {"files": {}}

    files: dict[str, str] = {}
    # 以目錄描述符讀取，避免世界在檢查與讀取之間替換 symlink。
    try:
        root_fd = os.open(HABITAT_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return {"files": files}
    remaining = 2 * 1024 * 1024
    try:
        for directory, dirs, names, directory_fd in os.fwalk(".", dir_fd=root_fd):
            dirs[:] = [name for name in dirs if name not in {".evo-packages", "__pycache__"}]
            for name in sorted(names):
                if not name.endswith(".py") or remaining <= 0:
                    continue
                try:
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory_fd)
                    with os.fdopen(fd, "rb") as source:
                        info = os.fstat(source.fileno())
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            continue
                        content = source.read(min(remaining, 256 * 1024))
                    remaining -= len(content)
                    relative = os.path.normpath(os.path.join(directory, name))
                    files[relative] = content.decode("utf-8")
                except (OSError, UnicodeError):
                    continue
    finally:
        os.close(root_fd)
    return {"files": files}


@app.get("/api/habitat/works")
async def get_creative_works(limit: int = 100):
    """列出意識實體永久保存的創作中繼資料。"""
    if not DB_PATH.exists():
        return {"works": []}
    try:
        with sqlite3.connect(DB_PATH) as conn:
            return {"works": list_creative_works(conn, limit)}
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"讀取創作檔案庫失敗：{exc}") from exc


@app.get("/api/habitat/works/{work_id}")
async def get_creative_work(work_id: str):
    """取回創作原始檔，依保存的 MIME type 交由瀏覽器顯示或下載。"""
    if not DB_PATH.exists():
        raise HTTPException(status_code=404, detail="創作檔案庫尚未初始化")
    try:
        with sqlite3.connect(DB_PATH) as conn:
            work = read_creative_work(conn, work_id)
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"讀取創作原檔失敗：{exc}") from exc
    if work is None:
        raise HTTPException(status_code=404, detail="找不到這件作品")
    content, mime_type, title = work
    safe_inline_types = {
        "image/png", "image/jpeg", "image/gif", "image/webp",
        "audio/mpeg", "audio/ogg", "audio/wav", "video/mp4", "video/webm", "text/plain",
    }
    safe_type = mime_type.split(";", 1)[0].strip().lower()
    disposition = "inline" if safe_type in safe_inline_types else "attachment"
    return Response(
        content=content,
        media_type=safe_type if disposition == "inline" else "application/octet-stream",
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(title, safe='')}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


@app.get("/api/habitat/history")
async def get_history_timeline(limit: int = 50, entity_id: str | None = None):
    """撈取歷史事件與重大里程碑。"""
    if not DB_PATH.exists():
        return {"events": [], "milestones": [], "current_epoch": 0, "civilization_stage": ""}

    events = []
    milestones = []
    current_epoch = 0
    civilization_stage = ""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            safe_limit = max(1, min(limit, 200))
            fields = (
                "id, type, message, importance, timestamp, entity_ids, epoch"
            )
            if entity_id:
                cursor.execute(
                    f"SELECT {fields} FROM events "
                    "WHERE entity_ids LIKE ? OR message LIKE ? ORDER BY timestamp DESC LIMIT ?",
                    (f'%"{entity_id}"%', f"%[{entity_id}]%", safe_limit),
                )
            else:
                cursor.execute(
                    f"SELECT {fields} FROM events "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (safe_limit,),
                )
            for row in cursor.fetchall():
                events.append({
                    "id": row[0],
                    "type": row[1],
                    "message": row[2],
                    "importance": row[3],
                    "timestamp": row[4],
                    "entity_ids": json.loads(row[5]) if row[5] else [],
                    "epoch": row[6],
                })

            cursor.execute(
                f"SELECT {fields} FROM events "
                "WHERE type IN ('epoch_transition', 'epoch_change', 'chronicle') "
                "ORDER BY timestamp DESC LIMIT 30"
            )
            milestone_by_epoch = {}
            milestone_priority = {
                "chronicle": 2,
                "epoch_transition": 1,
                "epoch_change": 1,
            }
            for row in cursor.fetchall():
                event = {
                    "id": row[0],
                    "type": row[1],
                    "message": row[2],
                    "importance": row[3],
                    "timestamp": row[4],
                    "entity_ids": json.loads(row[5]) if row[5] else [],
                    "epoch": row[6],
                }
                epoch_key = event["epoch"] if event["epoch"] is not None else event["id"]
                existing = milestone_by_epoch.get(epoch_key)
                if (
                    existing is None
                    or milestone_priority.get(event["type"], 0)
                    > milestone_priority.get(existing["type"], 0)
                ):
                    milestone_by_epoch[epoch_key] = event
            milestones = sorted(
                milestone_by_epoch.values(),
                key=lambda event: event["timestamp"] or 0,
                reverse=True,
            )[:30]

            metrics_row = cursor.execute(
                "SELECT value FROM world_state WHERE key = 'metrics'"
            ).fetchone()
            if metrics_row:
                metrics = json.loads(metrics_row[0])
                try:
                    current_epoch = int(metrics.get("epoch", 0))
                except (TypeError, ValueError):
                    current_epoch = 0
                civilization_stage = str(
                    metrics.get("epoch_name")
                    or metrics.get("era_name")
                    or metrics.get("civilization_stage", "")
                )

            state_rows = cursor.execute(
                "SELECT key, value FROM world_state "
                "WHERE key IN ('epoch', 'epoch_name', 'era_name', 'civilization_stage')"
            ).fetchall()
            state = {row[0]: row[1] for row in state_rows}
            if current_epoch == 0:
                try:
                    current_epoch = int(state.get("epoch", 0))
                except (TypeError, ValueError):
                    pass
            if not civilization_stage:
                civilization_stage = str(
                    state.get("epoch_name")
                    or state.get("era_name")
                    or state.get("civilization_stage")
                    or (metrics.get("紀元", "") if metrics_row else "")
                )
    except Exception:
        pass
    return {
        "events": events,
        "milestones": milestones,
        "current_epoch": current_epoch,
        "civilization_stage": civilization_stage,
    }


# === WebSocket 串流 ===
@app.websocket("/ws/universe")
async def websocket_universe_stream(websocket: WebSocket):
    """透過 WebSocket 持續向觀測前端推送抽象幾何原語與遙測數據。"""
    await websocket.accept()
    if config.paused:
        await websocket.close(code=1001, reason="宇宙已暫停")
        return
    connected_clients.append(websocket)
    try:
        while True:
            if config.paused:
                await websocket.close(code=1001, reason="宇宙已暫停")
                break
            pid = supervisor.get_pid()
            snapshot = bridge.get_telemetry_snapshot(target_pid=pid)
            snapshot["runtime"]["scheduler_status"] = dict(scheduler.last_status)
            snapshot["runtime"]["resource_block_reason"] = supervisor.resource_block_reason
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
