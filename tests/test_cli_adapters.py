"""CLI 適配器切換、模型選擇與可用性檢查單元測試。"""

import pytest

from cli.factory import (
    get_adapter,
    get_models_map,
    switch_adapter,
    switch_effort,
    switch_model,
)
from config.settings import config


@pytest.fixture(autouse=True)
def reset_adapter_models(tmp_path, monkeypatch):
    """每次測試前重設各適配器之預設模型與啟動設定。"""
    monkeypatch.setattr("config.settings.RUNTIME_CONFIG_PATH", tmp_path / "runtime_config.json")
    get_adapter("agy").set_model("gemini-3.1-pro-high")
    get_adapter("codex").set_model("gpt-5.6-terra")
    get_adapter("codex").set_effort("medium")
    get_adapter("claude").set_model("sonnet")
    get_adapter("claude").set_effort("medium")
    config.active_model = "gemini-3.1-pro-high"
    config.codex_effort = "medium"
    config.claude_effort = "medium"
    config.active_cli = "agy"
    config.speed_mode = "1x"
    config.paused = False


def test_default_cli_is_agy():
    """驗證系統初始預設適配器為 agy。"""
    adapter = get_adapter("agy")
    assert adapter.name == "agy"
    assert adapter.current_model == "gemini-3.1-pro-high"


def test_switch_to_available_or_raise_without_fallback():
    """驗證切換 CLI 時，若目標 CLI 不可用則拋出異常且嚴格不自動 fallback。"""
    # 測試切換至不存在之適配器
    with pytest.raises(ValueError, match="未知的 CLI 適配器"):
        switch_adapter("non_existent_cli")

    # 嘗試切換至系統未安裝的適配器（例如 claude 若未安裝），確認其拋出明確錯誤且不 fallback
    adapter_claude = get_adapter("claude")
    if not adapter_claude.is_available():
        with pytest.raises(RuntimeError, match="尚未安裝或在當前系統中無法使用"):
            switch_adapter("claude")


def test_codex_adapter_recognition_and_model():
    """驗證 codex 適配器之二進制檔偵測與模型選擇。"""
    codex_adapter = get_adapter("codex")
    assert codex_adapter.name == "codex"
    assert codex_adapter.is_available() is True
    assert "gpt-5.6-terra" in codex_adapter.available_models


def test_model_selection_and_validation():
    """驗證模型清單取得與動態切換模型功能。"""
    models_map = get_models_map()
    assert "agy" in models_map
    assert "codex" in models_map
    assert "claude" in models_map

    # 切換最新旗艦模型
    switch_model("gemini-3.8-flash-low", adapter_name="agy")
    assert config.active_model == "gemini-3.8-flash-low"
    assert get_adapter("agy").current_model == "gemini-3.8-flash-low"

    switch_effort("high", "codex")
    assert config.codex_effort == "high"
    assert get_adapter("codex").current_effort == "high"

    switch_effort("high", "claude")
    assert config.claude_effort == "high"
    assert get_adapter("claude").current_effort == "high"

    # 切換自訂模型
    switch_model("custom-model-2026", adapter_name="agy")
    assert config.active_model == "custom-model-2026"
    assert get_adapter("agy").current_model == "custom-model-2026"

    # 空白模型名稱應拋出 ValueError
    with pytest.raises(ValueError, match="模型名稱不能為空"):
        switch_model("   ", adapter_name="agy")


def test_agy_models_excludes_other_cli_providers(monkeypatch):
    """Agy 模型選單只應呈現 Gemini，不可混入 Claude 或 GPT。"""
    adapter = get_adapter("agy")
    monkeypatch.setattr(adapter, "is_available", lambda: True)

    class Result:
        returncode = 0
        stdout = "\n".join((
            "gemini-3.6-flash-low Gemini Flash",
            "claude-sonnet-4-6 Claude Sonnet",
            "gpt-oss-120b-medium GPT OSS",
            "Fetching models...",
        ))

    monkeypatch.setattr("cli.agy.adapter.subprocess.run", lambda *args, **kwargs: Result())
    adapter._models_cache_updated_at = 0

    assert adapter.fetch_available_models() == ["gemini-3.6-flash-low"]
