"""Evo 全域配置、持久化控制設定與環境設定。"""

import json
import tempfile
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

# 專案根目錄為 evo 倉庫的根目錄
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 可完整重置的執行期資料集中在同一個根目錄。
RUNTIME_DIR = PROJECT_ROOT / "runtime"
HABITAT_DIR = RUNTIME_DIR / "habitat"
BACKUPS_DIR = RUNTIME_DIR / "backups"
HISTORY_DIR = RUNTIME_DIR / "history"
RUNTIME_CONFIG_PATH = HISTORY_DIR / "runtime_config.json"
SKILLS_DIR = PROJECT_ROOT / "skills"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
OBSERVER_DIR = PROJECT_ROOT / "observer"

# 資料庫路徑（唯一 DB）
DB_PATH = HABITAT_DIR / "habitat.db"

# 速度預設值（依據 README 5.1）
TICK_SPEEDS = {
    "1x": 1.0,       # 宇宙物理每 1.0 秒推進一個 tick
    "3x": 1.0 / 3.0, # 精準三倍速
    "MAX": 0.05,     # 20 ticks/s 的安全上限
}

# AI 改程式時必須暫停世界；因此不可隨模擬速度一同加快，否則快轉反而
# 讓宇宙長時間停在 AI 思考中。速度按鈕只控制 TICK_SPEEDS。
AI_EVOLUTION_COOLDOWN = 300.0

# 資源硬性限制（依據 README 5.5）
MAX_CPU_PERCENT = 25.0
MAX_RAM_BYTES = 1024 * 1024 * 1024  # 1 GB
MAX_SUBPROCESSES = 4
MAX_HABITAT_SIZE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB


class EvoConfig(BaseModel):
    """Evo 控制器運行時動態配置模型。"""
    active_cli: Literal["agy", "codex", "claude"] = Field(
        default="agy",
        description="當前啟用的 CLI 適配器名稱（預設 agy）"
    )
    active_model: str = Field(
        default="gemini-3.1-pro-high",
        description="當前 CLI 適配器使用的模型名稱"
    )
    codex_effort: Literal["low", "medium", "high", "xhigh", "max", "ultra"] = Field(
        default="medium",
        description="Codex CLI 的推理強度"
    )
    claude_effort: Literal["low", "medium", "high", "xhigh", "max"] = Field(
        default="medium",
        description="Claude Code CLI 的推理強度"
    )
    speed_mode: Literal["1x", "3x", "MAX"] = Field(
        default="1x",
        description="當前宇宙模擬速度模式"
    )
    paused: bool = Field(
        default=False,
        description="宇宙執行是否暫停"
    )
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    observer_web_port: int = 5173

    @property
    def universe_tick_interval(self) -> float:
        """取得當前物理 tick 間隔秒數。"""
        return TICK_SPEEDS[self.speed_mode]

    @property
    def heartbeat_cooldown(self) -> float:
        """取得與模擬速度獨立的 AI 演化冷卻秒數。"""
        return AI_EVOLUTION_COOLDOWN


_PERSISTED_FIELDS = frozenset({
    "active_cli",
    "active_model",
    "codex_effort",
    "claude_effort",
    "speed_mode",
    "paused",
})
_CONFIG_SAVE_LOCK = threading.RLock()


def _load_config() -> EvoConfig:
    """讀取上次控制設定；檔案毀損時安全回到預設值。"""
    try:
        raw = json.loads(RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("設定檔根節點必須是物件")
        return EvoConfig(**{key: raw[key] for key in _PERSISTED_FIELDS if key in raw})
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return EvoConfig()


# 全域配置實例
config = _load_config()


def save_config() -> None:
    """原子寫入可由使用者調整的控制設定，供下次啟動還原。"""
    with _CONFIG_SAVE_LOCK:
        RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {field: getattr(config, field) for field in _PERSISTED_FIELDS}
        pending_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=RUNTIME_CONFIG_PATH.parent,
                prefix=f".{RUNTIME_CONFIG_PATH.name}.",
                suffix=".tmp",
                delete=False,
            ) as pending_file:
                pending_path = Path(pending_file.name)
                pending_file.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            pending_path.replace(RUNTIME_CONFIG_PATH)
        finally:
            if pending_path is not None and pending_path.exists():
                pending_path.unlink()
