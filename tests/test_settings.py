"""設定原子持久化並行寫入測試。"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

import pytest

import config.settings as settings


def test_resettable_paths_share_runtime_root():
    """世界、歷史與快照集中在同一個可完整 reset 的目錄。"""
    assert settings.RUNTIME_DIR == settings.PROJECT_ROOT / "runtime"
    assert settings.HABITAT_DIR == settings.RUNTIME_DIR / "habitat"
    assert settings.HISTORY_DIR == settings.RUNTIME_DIR / "history"
    assert settings.BACKUPS_DIR == settings.RUNTIME_DIR / "backups"
    assert settings.RUNTIME_CONFIG_PATH == settings.HISTORY_DIR / "runtime_config.json"


def test_simulation_speed_does_not_accelerate_ai_evolution() -> None:
    """快轉只影響世界 tick，避免頻繁 AI 回合停住世界。"""
    cooldowns = {
        settings.EvoConfig(speed_mode=speed).heartbeat_cooldown
        for speed in ("1x", "3x", "MAX")
    }
    assert cooldowns == {settings.AI_EVOLUTION_COOLDOWN}


def test_simulation_speed_multipliers_are_exact_and_bounded() -> None:
    """3x 必須精準為三倍速，MAX 維持明確的安全上限。"""
    assert settings.TICK_SPEEDS["3x"] == pytest.approx(settings.TICK_SPEEDS["1x"] / 3)
    assert 1 / settings.TICK_SPEEDS["MAX"] == pytest.approx(20.0)
    assert settings.AI_EVOLUTION_COOLDOWN == 300.0


def test_concurrent_save_config_uses_independent_atomic_temporary_files(tmp_path, monkeypatch):
    """並行儲存不會共用 tmp 路徑或遺失最終設定檔。"""
    config_path = tmp_path / "runtime_config.json"
    monkeypatch.setattr(settings, "RUNTIME_CONFIG_PATH", config_path)
    replaced_sources = []
    source_lock = Lock()
    original_replace = Path.replace

    def track_replace(source, target):
        if Path(target) == config_path:
            with source_lock:
                replaced_sources.append(source)
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", track_replace)
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: settings.save_config(), range(16)))

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert set(payload) == settings._PERSISTED_FIELDS
    assert len(replaced_sources) == 16
    assert len(set(replaced_sources)) == 16
    assert list(tmp_path.glob("*.tmp")) == []
