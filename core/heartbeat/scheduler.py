"""雙軌節拍引擎：Universe Tick 與 Cognitive Heartbeat 調度器。"""

import asyncio
import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from cli.factory import get_adapter
from config.settings import (
    HABITAT_STAGING_DIR,
    config,
)
from core.context.manager import ContextManager
from core.lifecycle.snapshot import LifecycleManager
from core.review.guardian import Guardian
from core.runtime.supervisor import RuntimeSupervisor


class CognitiveScheduler:
    """協調 AI 認知節拍、候選發布與運行監控器。"""

    def __init__(
        self,
        supervisor: RuntimeSupervisor,
        context_manager: ContextManager | None = None,
        guardian: Guardian | None = None,
        lifecycle_manager: LifecycleManager | None = None,
        staging_dir: Path = HABITAT_STAGING_DIR,
    ):
        self.supervisor = supervisor
        self.context_manager = context_manager or ContextManager()
        self.guardian = guardian or Guardian()
        self.lifecycle_manager = lifecycle_manager or LifecycleManager()
        self.staging_dir = Path(staging_dir).resolve()

        self.is_running = False
        self.sleep_cycles_remaining = 0
        self.last_heartbeat_time = 0.0
        self.current_turn_in_progress = False

        # 供外部 WebSocket 通知之狀態回呼
        self.on_state_change: Callable[[dict], None] | None = None

    def prepare_staging_workspace(self) -> None:
        """鏡像複製當前 habitat/ 至 habitat_staging/ 供 AI 修改。"""
        if self.staging_dir.exists():
            shutil.rmtree(self.staging_dir)
        self.staging_dir.mkdir(parents=True, exist_ok=True)

        source_dir = self.supervisor.habitat_dir
        if source_dir.exists():
            for item in source_dir.iterdir():
                if item.name.startswith("."):
                    continue
                target = self.staging_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, target, dirs_exist_ok=True)
                elif item.is_file():
                    shutil.copy2(item, target)

    def execute_cognitive_turn(self) -> dict:
        """執行單回合完整的 AI 認知節拍流程。"""
        if self.current_turn_in_progress:
            return {"status": "skipped", "reason": "上一回合仍在執行中"}

        if self.sleep_cycles_remaining > 0:
            self.sleep_cycles_remaining -= 1
            return {
                "status": "sleeping",
                "cycles_left": self.sleep_cycles_remaining,
            }

        self.current_turn_in_progress = True
        try:
            adapter = get_adapter()
            if not adapter.is_available():
                return {
                    "status": "error",
                    "error": f"當前啟用的 CLI '{adapter.name}' 不可用。",
                }

            # 1. 準備候選暫存工作區
            self.prepare_staging_workspace()
            candidate_main = self.staging_dir / "main.py"
            original_main = candidate_main.read_text(encoding="utf-8") if candidate_main.exists() else ""
            visible_world = self.context_manager.describe_visible_world()
            needs_visible_evolution = bool(visible_world.get("needs_evolution", True))

            # 2. 收集 Context 組裝提示詞
            prompt = self.context_manager.assemble_prompt()

            # 3. AI 修改循環（最多修復 3 輪）
            max_repair_rounds = 3
            current_prompt = prompt
            turn_success = False

            for round_num in range(1, max_repair_rounds + 1):
                try:
                    ai_output = adapter.execute_turn(
                        prompt=current_prompt,
                        workspace_path=self.staging_dir,
                    )
                except Exception as exc:
                    ai_output = f"執行異常：{exc}"

                # 檢查 AI 是否請求靜默休眠
                sleep_match = re.search(r"STATUS:\s*SLEEP\s+(\d+)", ai_output, re.IGNORECASE)
                if sleep_match:
                    if not needs_visible_evolution:
                        self.sleep_cycles_remaining = int(sleep_match.group(1))
                        return {
                            "status": "ai_sleep_requested",
                            "cycles": self.sleep_cycles_remaining,
                        }
                    current_prompt = (
                        "你嘗試休眠，但觀測器仍判定這是全數 node 的佔位世界。"
                        "本回合必須自行決定一個世界方向並實際修改目前工作區的 main.py；"
                        "不要只描述計畫，也不要回覆 STATUS: SLEEP。"
                    )
                    continue

                if needs_visible_evolution and candidate_main.read_text(encoding="utf-8") == original_main:
                    current_prompt = (
                        "尚未偵測到 main.py 的實際變更。請在這個 staging 工作區中直接實作"
                        "你選擇的世界規則與可見場景，不要只在回答文字中說明。"
                    )
                    continue

                # 透過 Guardian 守門員檢驗候選版本
                verif_res = self.guardian.verify(self.staging_dir)
                if verif_res.passed:
                    turn_success = True
                    break
                else:
                    # 失敗時將錯誤反饋給下一輪修復
                    current_prompt = (
                        f"候選版本在 '{verif_res.step}' 步驟未通過檢驗：\n"
                        f"{verif_res.error_message}\n\n"
                        f"請修復 `habitat/main.py` 中的問題以通過驗收。"
                    )

            if turn_success:
                # 4. 安全發布：快照 DB 並進行 Atomic 替換
                db_snap, code_snap = self.lifecycle_manager.create_snapshot()
                self.supervisor.stop()
                try:
                    self.lifecycle_manager.atomic_deploy_staging(self.staging_dir)
                    self.supervisor.start(tick_interval=config.universe_tick_interval)
                    return {"status": "deployed", "rounds": round_num}
                except Exception as dep_err:
                    # Runtime 已停止，回滾不會與 SQLite WAL 或程式碼檔競爭。
                    self.lifecycle_manager.rollback(db_snap, code_snap)
                    self.supervisor.start(tick_interval=config.universe_tick_interval)
                    return {"status": "rollback", "error": str(dep_err)}
            else:
                # 捨棄候選版本，保留原有 Stable Version
                return {
                    "status": "abandoned",
                    "reason": f"經歷 {max_repair_rounds} 輪修復仍未通過驗收：{verif_res.error_message}",
                }

        finally:
            self.last_heartbeat_time = time.time()
            self.current_turn_in_progress = False

    async def run_loop(self) -> None:
        """非同步排程循環：依據速度模式之冷卻時間定期觸發 AI 節拍。"""
        self.is_running = True
        while self.is_running:
            if not config.paused:
                cooldown = config.heartbeat_cooldown
                elapsed = time.time() - self.last_heartbeat_time

                if elapsed >= cooldown and not self.current_turn_in_progress:
                    # 於獨立執行緒執行認知節拍，避免阻塞主事件循環
                    await asyncio.to_thread(self.execute_cognitive_turn)

            await asyncio.sleep(0.5)

    def stop_loop(self) -> None:
        """停止排程循環。"""
        self.is_running = False
