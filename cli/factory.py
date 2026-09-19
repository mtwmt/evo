"""CLI 適配器工廠與狀態管理器。"""

from cli.adapters.base import BaseCLIAdapter
from cli.agy.adapter import AgyAdapter
from cli.claude.adapter import ClaudeAdapter
from cli.codex.adapter import CodexAdapter
from config.settings import config, save_config

# 註冊所有支援的 CLI 適配器實例
_ADAPTERS: dict[str, BaseCLIAdapter] = {
    "agy": AgyAdapter(),
    "codex": CodexAdapter(),
    "claude": ClaudeAdapter(),
}

_EFFORT_CONFIG_FIELDS = {
    "codex": "codex_effort",
    "claude": "claude_effort",
}


def get_adapter(name: str | None = None) -> BaseCLIAdapter:
    """依名稱或當前設定取得對應的 CLI 適配器。"""
    target_name = name or config.active_cli
    if target_name not in _ADAPTERS:
        raise ValueError(f"未知的 CLI 適配器 '{target_name}'。支援清單：{list(_ADAPTERS.keys())}")
    return _ADAPTERS[target_name]


def get_models_map() -> dict[str, list[str]]:
    """動態向各 CLI 適配器查詢最新模型清單，拒絕死板 Hardcode。"""
    return {name: adapter.fetch_available_models() for name, adapter in _ADAPTERS.items()}


def switch_model(model_name: str, adapter_name: str | None = None) -> str:
    """動態切換指定適配器之模型。支援任意最新發布之模型名稱。"""
    target_adapter = get_adapter(adapter_name)
    target_adapter.set_model(model_name)
    config.active_model = model_name
    save_config()
    return model_name


def switch_effort(effort: str, adapter_name: str | None = None) -> str:
    """設定指定 CLI 的推理強度。"""
    adapter = get_adapter(adapter_name)
    adapter.set_effort(effort)
    field = _EFFORT_CONFIG_FIELDS[adapter.name]
    setattr(config, field, effort)
    save_config()
    return effort


def switch_adapter(name: str, model: str | None = None) -> BaseCLIAdapter:
    """動態切換當前啟用的 CLI 適配器與模型。

    依據 README 規則 5.2：
    - 運行中可切換 CLI，於下一個 Cognitive Heartbeat 生效。
    - 世界、DB、歷史與 Context 絕不重建。
    - CLI 不可用時顯示錯誤，絕對不自動 fallback。
    """
    if name not in _ADAPTERS:
        raise ValueError(f"未知的 CLI 適配器 '{name}'。支援清單：{list(_ADAPTERS.keys())}")

    adapter = _ADAPTERS[name]
    if not adapter.is_available():
        raise RuntimeError(
            f"CLI 適配器 '{name}' 尚未安裝或在當前系統中無法使用。"
            f"切換至 '{name}' 失敗（依據規則已禁用自動 fallback）。"
        )

    config.active_cli = name  # type: ignore[assignment]
    if model:
        adapter.set_model(model)
        config.active_model = model
    else:
        if not adapter.current_model:
            avail = adapter.fetch_available_models()
            if avail:
                adapter.set_model(avail[0])
        config.active_model = adapter.current_model

    save_config()
    return adapter


def restore_adapter_state() -> None:
    """將持久化的模型與推理強度同步回本次程序的 Adapter 實例。"""
    for name, field in _EFFORT_CONFIG_FIELDS.items():
        _ADAPTERS[name].set_effort(getattr(config, field))
    get_adapter(config.active_cli).set_model(config.active_model)


restore_adapter_state()
