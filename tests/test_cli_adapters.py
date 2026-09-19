"""CLI 適配器切換、模型選擇與可用性檢查單元測試。"""

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, RLock

import pytest

from cli.adapters.base import ProcessCancelledError, run_cancellable_process
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
    """隔離 CLI、帳號模型目錄、快取與全域設定，測試後還原單例狀態。"""
    monkeypatch.setattr("config.settings.RUNTIME_CONFIG_PATH", tmp_path / "runtime_config.json")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    for module in ("cli.agy.adapter", "cli.codex.adapter", "cli.claude.adapter"):
        monkeypatch.setattr(
            f"{module}.shutil.which",
            lambda name: f"/mock/bin/{name}" if name in {"agy", "codex", "claude"} else None,
        )

    class ModelsResult:
        returncode = 0
        stdout = "gemini-3.8-flash-low Gemini Flash\nclaude-sonnet-4-6 Claude Sonnet"
        stderr = ""

    monkeypatch.setattr(
        "cli.agy.adapter.AgyAdapter.run_process",
        lambda self, *args, **kwargs: ModelsResult(),
    )
    codex_cache = tmp_path / ".codex" / "models_cache.json"
    codex_cache.parent.mkdir()
    codex_cache.write_text(
        '{"models": [{"slug": "gpt-5.6-terra", "visibility": "list"}, '
        '{"slug": "private-model", "visibility": "hidden"}]}',
        encoding="utf-8",
    )

    adapters = {name: get_adapter(name) for name in ("agy", "codex", "claude")}
    original_adapters = {
        name: {
            "current_model": adapter.current_model,
            "current_effort": adapter.current_effort,
            "models_cache": getattr(adapter, "_models_cache", None),
            "models_cache_updated_at": getattr(adapter, "_models_cache_updated_at", None),
        }
        for name, adapter in adapters.items()
    }
    original_config = {
        "active_model": config.active_model,
        "codex_effort": config.codex_effort,
        "claude_effort": config.claude_effort,
        "active_cli": config.active_cli,
        "speed_mode": config.speed_mode,
        "paused": config.paused,
    }

    get_adapter("agy").set_model("gemini-3.1-pro-high")
    get_adapter("codex").set_model("gpt-5.6-terra")
    get_adapter("codex").set_effort("medium")
    get_adapter("claude").set_model("sonnet")
    get_adapter("claude").set_effort("medium")
    get_adapter("agy")._models_cache_updated_at = -1e9
    get_adapter("codex")._models_cache_updated_at = -1e9
    config.active_model = "gemini-3.1-pro-high"
    config.codex_effort = "medium"
    config.claude_effort = "medium"
    config.active_cli = "agy"
    config.speed_mode = "1x"
    config.paused = False
    yield

    for name, adapter in adapters.items():
        state = original_adapters[name]
        adapter.current_model = state["current_model"]
        adapter.current_effort = state["current_effort"]
        if state["models_cache"] is not None:
            adapter._models_cache = state["models_cache"]
            adapter._models_cache_updated_at = state["models_cache_updated_at"]
    for key, value in original_config.items():
        setattr(config, key, value)


def test_default_cli_is_agy():
    """驗證系統初始預設適配器為 agy。"""
    adapter = get_adapter("agy")
    assert adapter.name == "agy"
    assert adapter.current_model == "gemini-3.1-pro-high"


def test_switch_to_available_or_raise_without_fallback(monkeypatch):
    """驗證切換 CLI 時，若目標 CLI 不可用則拋出異常且嚴格不自動 fallback。"""
    # 測試切換至不存在之適配器
    with pytest.raises(ValueError, match="未知的 CLI 適配器"):
        switch_adapter("non_existent_cli")

    # 使用模擬的未安裝狀態驗證錯誤路徑，不依賴本機 CLI 安裝情況。
    adapter_claude = get_adapter("claude")
    monkeypatch.setattr(adapter_claude, "is_available", lambda: False)
    with pytest.raises(RuntimeError, match="尚未安裝或在當前系統中無法使用"):
        switch_adapter("claude")
    assert config.active_cli == "agy"

    monkeypatch.setattr(adapter_claude, "is_available", lambda: True)
    assert switch_adapter("claude", model="sonnet") is adapter_claude
    assert config.active_cli == "claude"


def test_codex_adapter_recognition_and_model():
    """驗證 codex 適配器之二進制偵測與本機模型快取解析。"""
    codex_adapter = get_adapter("codex")
    assert codex_adapter.name == "codex"
    assert codex_adapter.is_available() is True
    assert codex_adapter.fetch_available_models() == ["gpt-5.6-terra"]


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


def test_agy_models_includes_all_models_exposed_by_agy(monkeypatch):
    """Agy 模型選單應完整呈現該 CLI 實際提供的模型。"""
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

    monkeypatch.setattr(
        adapter,
        "run_process",
        lambda *args, **kwargs: Result(),
    )
    adapter._models_cache_updated_at = -1e9

    assert adapter.fetch_available_models() == [
        "gemini-3.6-flash-low",
        "claude-sonnet-4-6",
        "gpt-oss-120b-medium",
    ]


def test_agy_turn_uses_accept_edits_mode(tmp_path, monkeypatch):
    """Agy 取得候選程式碼，並回傳經驗證的 JSON 結果。"""
    adapter = get_adapter("agy")
    monkeypatch.setattr(adapter, "is_available", lambda: True)
    captured: dict[str, object] = {}
    source_path = tmp_path / "main.py"
    source_path.write_text("print('candidate')\n", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs["cwd"]
        captured["context"] = kwargs["context"]
        return subprocess.CompletedProcess(
            cmd, 0, '{"status":"SUCCESS","response":"完成","error":""}', ""
        )

    monkeypatch.setattr(adapter, "run_process", fake_run)
    cancel_event = Event()
    context = {"cancel_event": cancel_event}
    assert adapter.execute_turn("請修改世界", tmp_path, context=context) == "完成"
    assert captured["cwd"] == str(tmp_path)
    assert "accept-edits" in captured["cmd"]
    assert "--add-dir" in captured["cmd"]
    assert str(tmp_path.resolve()) in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--output-format") + 1] == "json"
    assert captured["context"] is context
    prompt = captured["cmd"][captured["cmd"].index("--prompt") + 1]
    assert str(source_path) in prompt
    assert "print('candidate')" in prompt
    assert "native file read, write, and edit tools" in prompt
    assert "RunCommand, shell, terminal, tests, or ls" in prompt


def test_agy_turn_allows_blank_genesis_workspace(tmp_path, monkeypatch):
    """空白生態池不需預先放置 main.py，AI 會收到明確的創世指示。"""
    adapter = get_adapter("agy")
    monkeypatch.setattr(adapter, "is_available", lambda: True)
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0, '{"status":"SUCCESS","response":"已建立創世程式","error":""}', ""
        )

    monkeypatch.setattr(adapter, "run_process", fake_run)

    assert adapter.execute_turn("開始創世", tmp_path) == "已建立創世程式"
    prompt = captured["cmd"][captured["cmd"].index("--prompt") + 1]
    assert "does not exist" in prompt
    assert "blank genesis workspace" in prompt
    assert "Create main.py and any supporting modules" in prompt


@pytest.mark.parametrize(
    ("stdout", "stderr", "message"),
    [
        (
            '{"status":"ERROR","response":"","error":"tool failed"}',
            "",
            "回報狀態 'ERROR'.*tool failed",
        ),
        ("", "", "JSON 格式無效"),
        ("not json", "", "JSON 格式無效"),
        ('{"status":"SUCCESS","response":""}', "", "response 為空"),
        ("", "soft-denying tool confirmation RunCommand", "權限提示阻擋"),
    ],
)
def test_agy_turn_rejects_soft_denies_and_invalid_results(
    tmp_path, monkeypatch, stdout, stderr, message
):
    adapter = get_adapter("agy")
    monkeypatch.setattr(adapter, "is_available", lambda: True)
    (tmp_path / "main.py").write_text("pass\n", encoding="utf-8")
    monkeypatch.setattr(
        adapter,
        "run_process",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, stdout, stderr),
    )
    with pytest.raises(RuntimeError, match=message):
        adapter.execute_turn("change", tmp_path)


def test_agy_turn_accepts_ordinary_warning_stderr(tmp_path, monkeypatch):
    adapter = get_adapter("agy")
    monkeypatch.setattr(adapter, "is_available", lambda: True)
    (tmp_path / "main.py").write_text("pass\n", encoding="utf-8")
    monkeypatch.setattr(
        adapter,
        "run_process",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            cmd, 0, '{"status":"SUCCESS","response":"done","error":""}',
            "warning: model response may be incomplete; RunCommand is available",
        ),
    )
    assert adapter.execute_turn("change", tmp_path) == "done"


@pytest.mark.parametrize("kind", ["symlink", "fifo", "hardlink", "oversize"])
def test_agy_refuses_unsafe_or_oversize_candidate_source(tmp_path, monkeypatch, kind):
    adapter = get_adapter("agy")
    monkeypatch.setattr(adapter, "is_available", lambda: True)
    source = tmp_path / "main.py"
    if kind == "symlink":
        target = tmp_path / "target.py"
        target.write_text("pass\n", encoding="utf-8")
        source.symlink_to(target)
        expected = "無法安全讀取候選程式碼"
    elif kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("此平台不支援 FIFO")
        os.mkfifo(source)
        expected = "不是一般檔案"
    elif kind == "hardlink":
        source.write_text("pass\n", encoding="utf-8")
        os.link(source, tmp_path / "main-alias.py")
        expected = "不可為硬連結"
    else:
        source.write_bytes(b"x" * (128 * 1024 + 1))
        expected = "超過 131072 位元組上限"
    calls = []
    monkeypatch.setattr(adapter, "run_process", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(RuntimeError, match=expected):
        adapter.execute_turn("change", tmp_path)
    assert calls == []


def test_cancelled_process_is_not_spawned(monkeypatch, tmp_path):
    """啟動鎖中的取消檢查會阻止已取消回合建立 CLI 子程序。"""
    cancel_event = Event()
    cancel_event.set()
    popen_calls = []
    monkeypatch.setattr(
        "cli.adapters.base.subprocess.Popen",
        lambda *args, **kwargs: popen_calls.append((args, kwargs)),
    )

    with pytest.raises(ProcessCancelledError, match="未啟動程序"):
        run_cancellable_process(
            [sys.executable, "-c", "pass"],
            cwd=tmp_path,
            context={"cancel_event": cancel_event, "cancel_lock": RLock()},
        )

    assert popen_calls == []


@pytest.mark.skipif(os.name == "nt", reason="此案例驗證 POSIX 程序群組的子程序清理")
def test_cancelled_process_group_stops_and_reaps_child(tmp_path):
    """取消 CLI 時會停止其子程序群組，子程序可執行 SIGTERM 清理。"""
    child_pid_path = tmp_path / "child.pid"
    child_stopped_path = tmp_path / "child.stopped"
    cancel_event = Event()
    child_code = (
        "import signal,sys,time; "
        "signal.signal(signal.SIGTERM, lambda *_: ("
        "open(sys.argv[1], 'w').write('stopped'), sys.exit(0))); "
        "open(sys.argv[2], 'w').write('ready'); time.sleep(30)"
    )
    parent_code = (
        "import pathlib,subprocess,sys,time; "
        "subprocess.Popen([sys.executable, '-c', sys.argv[2], sys.argv[3], sys.argv[1]]); "
        "time.sleep(30)"
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            run_cancellable_process,
            [
                sys.executable,
                "-c",
                parent_code,
                str(child_pid_path),
                child_code,
                str(child_stopped_path),
            ],
            cwd=tmp_path,
            context={"cancel_event": cancel_event},
            timeout=20,
        )
        deadline = time.monotonic() + 3
        while not child_pid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert child_pid_path.exists(), "測試 CLI 未啟動子程序"

        started = time.monotonic()
        cancel_event.set()
        with pytest.raises(ProcessCancelledError):
            future.result(timeout=3)

    assert time.monotonic() - started < 3
    assert child_stopped_path.exists(), "子程序未收到程序群組終止訊號"


@pytest.mark.skipif(os.name == "nt", reason="驗證 POSIX 強制終止訊號")
def test_force_cancel_kills_process_ignoring_sigterm(tmp_path):
    """CLI 忽略正常終止訊號時，取消仍會升級為 SIGKILL 並回收。"""
    import psutil

    ready = tmp_path / "ready"
    cancelled = Event()
    code = (
        "import os,pathlib,signal,sys,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)"
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            run_cancellable_process,
            [sys.executable, "-c", code, str(ready)],
            cwd=tmp_path, context={"cancel_event": cancelled}, timeout=3,
        )
        deadline = time.monotonic() + 2
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        cancelled.set()
        with pytest.raises(ProcessCancelledError):
            future.result(timeout=3)
    assert ready.exists()
    assert not psutil.pid_exists(int(ready.read_text()))


@pytest.mark.skipif(os.name == "nt", reason="驗證 POSIX 殘留程序群組")
def test_successful_root_cleans_up_remaining_child(tmp_path):
    """CLI 根程序正常結束，也不遺留會在暫停後繼續執行的子程序。"""
    import psutil

    ready = tmp_path / "child-ready"
    child = (
        "import os,pathlib,sys,time; "
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)"
    )
    parent = """
import pathlib, subprocess, sys, time
subprocess.Popen([sys.executable, '-c', sys.argv[2], sys.argv[1]],
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
while not pathlib.Path(sys.argv[1]).exists():
    time.sleep(0.01)
print('done')
"""
    result = run_cancellable_process(
        [sys.executable, "-c", parent, str(ready), child], cwd=tmp_path, timeout=3,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "done"
    assert not psutil.pid_exists(int(ready.read_text()))


def test_timeout_reaps_cli_process(tmp_path):
    """逾時路徑同樣停止並回收程序。"""
    import psutil

    ready = tmp_path / "timeout-ready"
    with pytest.raises(subprocess.TimeoutExpired):
        run_cancellable_process(
            [sys.executable, "-c", "import os,pathlib,sys,time; "
             "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)", str(ready)],
            cwd=tmp_path, timeout=0.3,
        )
    assert ready.exists()
    assert not psutil.pid_exists(int(ready.read_text()))
