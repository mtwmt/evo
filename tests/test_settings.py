"""設定原子持久化並行寫入測試。"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

import config.settings as settings


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
