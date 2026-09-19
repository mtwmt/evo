"""雙軌節拍引擎與調度器單元測試。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from core.context.manager import ContextManager
from core.heartbeat import scheduler as scheduler_module
from core.heartbeat.scheduler import CognitiveScheduler
from core.lifecycle.snapshot import LifecycleManager
from core.review.guardian import Guardian, VerificationResult
from core.runtime import supervisor as supervisor_module
from core.runtime.supervisor import RuntimeSupervisor


class StubContext:
    def __init__(self, needs_evolution=True, signal_ids=None, events=None):
        self.needs_evolution = needs_evolution
        self.signal_ids = signal_ids or []
        self.events = events if events is not None else []

    def describe_visible_world(self):
        return {"needs_evolution": self.needs_evolution}

    def prepare_turn_context(self):
        return "原始世界上下文", list(self.signal_ids)

    def acknowledge_observer_signals(self, ids):
        self.events.append(("ack", list(ids)))

def test_staging_workspace_mirroring(tmp_path: Path):
    """驗證每次 AI 修改前，staging 目錄皆能完整鏡像複製當前 habitat 代碼。"""
    habitat = tmp_path / "habitat"
    staging = tmp_path / "staging"
    habitat.mkdir()
    (habitat / "main.py").write_text("print('stable')", encoding="utf-8")

    supervisor = RuntimeSupervisor(habitat_dir=habitat)
    scheduler = CognitiveScheduler(
        supervisor=supervisor,
        context_manager=ContextManager(db_path=habitat / "habitat.db"),
        guardian=Guardian(),
        lifecycle_manager=LifecycleManager(habitat_dir=habitat, backups_dir=tmp_path / "backups"),
        staging_dir=staging,
    )

    scheduler.prepare_staging_workspace()
    assert (staging / "main.py").exists()
    assert (staging / "main.py").read_text(encoding="utf-8") == "print('stable')"


def test_blank_habitat_is_created_reviewed_and_deployed(tmp_path: Path, monkeypatch):
    """首次創世可從不存在的 habitat 開始，候選仍須先通過 Guardian。"""
    habitat = tmp_path / "habitat"
    staging = tmp_path / "staging"
    reviewed: list[Path] = []
    lifecycle_events: list[str] = []

    class GenesisAdapter:
        def is_available(self):
            return True

        def execute_turn(self, **kwargs):
            workspace = kwargs["workspace_path"]
            (workspace / "world.py").write_text("WORLD = 'self-authored'\n", encoding="utf-8")
            (workspace / "main.py").write_text("from world import WORLD\n", encoding="utf-8")
            return "完成第一次創世"

    class ReviewingGuardian:
        def verify(self, workspace):
            reviewed.append(workspace)
            assert (workspace / "main.py").is_file()
            assert (workspace / "world.py").is_file()
            return VerificationResult(True, "smoke_test", None)

    supervisor = RuntimeSupervisor(habitat_dir=habitat)
    supervisor.stop = lambda: lifecycle_events.append("stop")
    supervisor.start = lambda **kwargs: lifecycle_events.append("start")
    scheduler = CognitiveScheduler(
        supervisor=supervisor,
        context_manager=StubContext(needs_evolution=True),
        guardian=ReviewingGuardian(),
        lifecycle_manager=LifecycleManager(
            habitat_dir=habitat,
            backups_dir=tmp_path / "backups",
        ),
        staging_dir=staging,
    )
    monkeypatch.setattr(scheduler_module, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(scheduler_module.config, "paused", False)
    monkeypatch.setattr(scheduler_module, "get_adapter", GenesisAdapter)

    result = scheduler.execute_cognitive_turn()

    assert result == {"status": "deployed", "rounds": 1}
    assert reviewed == [staging]
    assert (habitat / "main.py").read_text(encoding="utf-8") == "from world import WORLD\n"
    assert (habitat / "world.py").read_text(encoding="utf-8") == "WORLD = 'self-authored'\n"
    assert lifecycle_events == ["stop", "start"]


def test_ai_sleep_command_parsing(tmp_path: Path, monkeypatch):
    """驗證當 AI 回覆 STATUS: SLEEP <N> 時，排程器能進入靜默休眠狀態節省 Quota。"""
    habitat = tmp_path / "habitat"
    staging = tmp_path / "staging"
    habitat.mkdir()

    supervisor = RuntimeSupervisor(habitat_dir=habitat)
    scheduler = CognitiveScheduler(
        supervisor=supervisor,
        context_manager=ContextManager(db_path=habitat / "habitat.db"),
        guardian=Guardian(),
        lifecycle_manager=LifecycleManager(habitat_dir=habitat, backups_dir=tmp_path / "backups"),
        staging_dir=staging,
    )

    monkeypatch.setattr(scheduler_module.config, "paused", False)
    # 模擬設定休眠 5 週期
    scheduler.sleep_cycles_remaining = 5
    res = scheduler.execute_cognitive_turn()

    assert res["status"] == "sleeping"
    assert res["cycles_left"] == 4
    assert scheduler.sleep_cycles_remaining == 4


def build_scheduler(tmp_path, context, guardian=None):
    habitat = tmp_path / "habitat"
    habitat.mkdir(exist_ok=True)
    (habitat / "main.py").write_text("print('stable')", encoding="utf-8")
    supervisor = RuntimeSupervisor(habitat_dir=habitat)
    scheduler = CognitiveScheduler(
        supervisor=supervisor,
        context_manager=context,
        guardian=guardian,
        lifecycle_manager=LifecycleManager(
            habitat_dir=habitat,
            backups_dir=tmp_path / "backups",
        ),
        staging_dir=tmp_path / "staging",
    )
    return scheduler, supervisor, habitat


def test_abandoned_turn_does_not_kill_later_scheduling(tmp_path, monkeypatch):
    """三輪沒有修改時明確放棄，下一個呼叫仍可開始新回合。"""
    monkeypatch.setattr(scheduler_module, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(scheduler_module.config, "paused", False)
    context = StubContext(needs_evolution=True, signal_ids=[7])
    scheduler, _, _ = build_scheduler(tmp_path, context)

    class NoChangeAdapter:
        calls = 0

        def is_available(self):
            return True

        def execute_turn(self, **kwargs):
            self.calls += 1
            return "沒有修改 main.py"

    adapter = NoChangeAdapter()
    monkeypatch.setattr(scheduler_module, "get_adapter", lambda: adapter)

    first = scheduler.execute_cognitive_turn()
    second = scheduler.execute_cognitive_turn()

    assert first["status"] == "abandoned"
    assert second["status"] == "abandoned"
    assert adapter.calls == 6
    assert context.events == []
    assert scheduler.current_turn_in_progress is False


def test_module_only_code_change_is_accepted(tmp_path, monkeypatch):
    """可見演化可由新模組完成，無須修改入口 main.py。"""
    monkeypatch.setattr(scheduler_module, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(scheduler_module.config, "paused", False)
    scheduler, _, habitat = build_scheduler(tmp_path, StubContext(needs_evolution=True))
    (habitat / ".habitat-config").write_text("stable", encoding="utf-8")
    verified = []

    class PassGuardian:
        def verify(self, workspace):
            verified.append(workspace)
            return VerificationResult(True, "smoke", None)

    class ModuleAdapter:
        def is_available(self):
            return True

        def execute_turn(self, **kwargs):
            plugin = kwargs["workspace_path"] / "plugins" / "growth.py"
            plugin.parent.mkdir()
            plugin.write_text("def grow(): return True\n", encoding="utf-8")
            return "新增生態模組"

    scheduler.guardian = PassGuardian()
    monkeypatch.setattr(scheduler_module, "get_adapter", ModuleAdapter)
    monkeypatch.setattr(
        scheduler,
        "_deploy_candidate",
        lambda generation, rounds, signal_ids: {"status": "deployed", "rounds": rounds},
    )

    result = scheduler.execute_cognitive_turn()

    assert result == {"status": "deployed", "rounds": 1}
    assert len(verified) == 1
    assert (tmp_path / "staging" / ".habitat-config").read_text(encoding="utf-8") == "stable"
    assert (habitat / "main.py").read_text(encoding="utf-8") == "print('stable')"


def test_candidate_symlink_is_rejected_without_reading_target(tmp_path, monkeypatch):
    """候選連結會被拒絕，且不會將連結目標內容納入讀取。"""
    monkeypatch.setattr(scheduler_module, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(scheduler_module.config, "paused", False)
    scheduler, _, _ = build_scheduler(tmp_path, StubContext(needs_evolution=True))
    outside = tmp_path / "outside-secret"
    outside.write_text("must not be read", encoding="utf-8")
    guardian_calls = []

    class GuardianSpy:
        def verify(self, workspace):
            guardian_calls.append(workspace)
            return VerificationResult(True, "smoke", None)

    class LinkAdapter:
        def is_available(self):
            return True

        def execute_turn(self, **kwargs):
            (kwargs["workspace_path"] / "module.py").symlink_to(outside)
            return "新增模組"

    scheduler.guardian = GuardianSpy()
    monkeypatch.setattr(scheduler_module, "get_adapter", LinkAdapter)

    result = scheduler.execute_cognitive_turn()

    assert result["status"] == "abandoned"
    assert "符號連結" in result["reason"]
    assert guardian_calls == []
    assert outside.read_text(encoding="utf-8") == "must not be read"


def test_cli_exception_cannot_verify_or_deploy_partial_changes(tmp_path, monkeypatch):
    """CLI 例外留下的部分檔案不會被當成成功候選或確認訊息。"""
    monkeypatch.setattr(scheduler_module, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(scheduler_module.config, "paused", False)
    context = StubContext(signal_ids=[9])
    scheduler, _, _ = build_scheduler(tmp_path, context)
    candidate = tmp_path / "staging" / "main.py"

    class FailingAdapter:
        def is_available(self):
            return True

        def execute_turn(self, **kwargs):
            candidate.write_text("partial edit", encoding="utf-8")
            raise RuntimeError("mock CLI failure")

    class GuardianSpy:
        calls = 0

        def verify(self, workspace):
            self.calls += 1
            return VerificationResult(True, "mock", None)

    guardian = GuardianSpy()
    scheduler.guardian = guardian
    monkeypatch.setattr(scheduler_module, "get_adapter", FailingAdapter)

    result = scheduler.execute_cognitive_turn()

    assert result["status"] == "abandoned"
    assert guardian.calls == 0
    assert context.events == []
    assert (tmp_path / "habitat" / "main.py").read_text(encoding="utf-8") == "print('stable')"


def test_repairs_keep_original_context_and_ack_after_ready_deploy(tmp_path, monkeypatch):
    """修復 prompt 保留原 context，ready 之後才確認訊息。"""
    monkeypatch.setattr(scheduler_module, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(scheduler_module.config, "paused", False)
    events = []
    context = StubContext(needs_evolution=True, signal_ids=[3, 4], events=events)

    class RepairGuardian:
        calls = 0

        def verify(self, workspace):
            self.calls += 1
            if self.calls == 1:
                return VerificationResult(False, "smoke", "首次驗證失敗")
            return VerificationResult(True, "smoke", None)

    scheduler, supervisor, _ = build_scheduler(tmp_path, context, RepairGuardian())
    prompts = []
    statuses = []
    scheduler.on_state_change = lambda status: statuses.append(status["status"])

    class EditingAdapter:
        def is_available(self):
            return True

        def execute_turn(self, prompt, workspace_path, context=None):
            prompts.append(prompt)
            (workspace_path / "main.py").write_text("print('candidate')", encoding="utf-8")
            return "已修改"

    monkeypatch.setattr(scheduler_module, "get_adapter", EditingAdapter)
    supervisor.stop = lambda: events.append(("stop",))
    supervisor.start = lambda **kwargs: events.append(("ready",))

    result = scheduler.execute_cognitive_turn()

    assert result["status"] == "deployed"
    assert prompts[0] == "原始世界上下文"
    assert prompts[1].startswith("原始世界上下文")
    assert "首次驗證失敗" in prompts[1]
    assert events.index(("ready",)) < events.index(("ack", [3, 4]))
    assert statuses == ["thinking", "verifying", "thinking", "verifying", "deploying", "deployed"]


def test_failed_candidate_start_rolls_back_without_ack(tmp_path, monkeypatch):
    """正式候選啟動失敗時先回滾，修復中的訊息仍保持未確認。"""
    monkeypatch.setattr(scheduler_module, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(scheduler_module.config, "paused", False)
    context = StubContext(needs_evolution=True, signal_ids=[13])
    scheduler, supervisor, habitat = build_scheduler(
        tmp_path,
        context,
        guardian=type("PassGuardian", (), {
            "verify": lambda self, workspace: VerificationResult(True, "smoke", None)
        })(),
    )

    class EditingAdapter:
        def is_available(self):
            return True

        def execute_turn(self, **kwargs):
            (kwargs["workspace_path"] / "main.py").write_text("candidate", encoding="utf-8")
            return "完成修改"

    starts = 0

    def start(**kwargs):
        nonlocal starts
        starts += 1
        if starts == 1:
            raise RuntimeError("candidate exited during startup")

    monkeypatch.setattr(scheduler_module, "get_adapter", EditingAdapter)
    supervisor.stop = lambda: None
    supervisor.start = start

    result = scheduler.execute_cognitive_turn()

    assert result["status"] == "rollback"
    assert starts == 2
    assert (habitat / "main.py").read_text(encoding="utf-8") == "print('stable')"
    assert context.events == []


def test_failed_first_genesis_rolls_back_to_blank_without_restart(tmp_path, monkeypatch):
    """首次候選啟動失敗後回到空白池，不能重啟不存在的穩定入口。"""
    monkeypatch.setattr(scheduler_module, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(scheduler_module.config, "paused", False)
    habitat = tmp_path / "habitat"
    context = StubContext(needs_evolution=True)

    class GenesisAdapter:
        def is_available(self):
            return True

        def execute_turn(self, **kwargs):
            (kwargs["workspace_path"] / "main.py").write_text("candidate", encoding="utf-8")
            return "完成創世候選"

    scheduler = CognitiveScheduler(
        supervisor=RuntimeSupervisor(habitat_dir=habitat),
        context_manager=context,
        guardian=type("PassGuardian", (), {
            "verify": lambda self, workspace: VerificationResult(True, "smoke", None)
        })(),
        lifecycle_manager=LifecycleManager(
            habitat_dir=habitat,
            backups_dir=tmp_path / "backups",
        ),
        staging_dir=tmp_path / "staging",
    )
    starts = []
    scheduler.supervisor.stop = lambda: None
    scheduler.supervisor.start = lambda **kwargs: (
        starts.append(kwargs),
        (_ for _ in ()).throw(RuntimeError("candidate startup failed")),
    )[1]
    monkeypatch.setattr(scheduler_module, "get_adapter", GenesisAdapter)

    result = scheduler.execute_cognitive_turn()

    assert result["status"] == "rollback"
    assert len(starts) == 1
    assert habitat.is_dir()
    assert list(habitat.iterdir()) == []


def test_pause_during_slow_cli_prevents_deploy_and_ack(tmp_path, monkeypatch):
    """暫停會取消慢速 CLI 的發布權，並在恢復執行前停止正式進程。"""
    monkeypatch.setattr(scheduler_module, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(scheduler_module.config, "paused", False)
    context = StubContext(needs_evolution=True, signal_ids=[21])
    scheduler, supervisor, habitat = build_scheduler(tmp_path, context)
    entered = Event()

    class SlowAdapter:
        def is_available(self):
            return True

        def execute_turn(self, **kwargs):
            entered.set()
            assert kwargs["context"]["cancel_event"].wait(timeout=3)
            (kwargs["workspace_path"] / "main.py").write_text("late candidate", encoding="utf-8")
            return "修改完成"

    class PassGuardian:
        calls = 0

        def verify(self, workspace):
            self.calls += 1
            return VerificationResult(True, "smoke", None)

    guardian = PassGuardian()
    scheduler.guardian = guardian
    monkeypatch.setattr(scheduler_module, "get_adapter", SlowAdapter)
    supervisor.stop = lambda: None

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(scheduler.execute_cognitive_turn)
        assert entered.wait(timeout=3)
        scheduler.pause_runtime()
        result = future.result(timeout=3)

    assert result["status"] == "cancelled"
    assert guardian.calls == 0
    assert context.events == []
    assert (habitat / "main.py").read_text(encoding="utf-8") == "print('stable')"


def test_sleep_cycles_update_monotonic_cooldown_and_ack(tmp_path, monkeypatch):
    """合法休眠及每個休眠週期都重新設定 monotonic 冷卻基準。"""
    monkeypatch.setattr(scheduler_module, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(scheduler_module.config, "paused", False)
    context = StubContext(needs_evolution=False, signal_ids=[31])
    scheduler, _, _ = build_scheduler(tmp_path, context)
    now = [100.0]
    monkeypatch.setattr(scheduler_module.time, "monotonic", lambda: now[0])

    class SleepingAdapter:
        def is_available(self):
            return True

        def execute_turn(self, **kwargs):
            return "STATUS: SLEEP 2"

    monkeypatch.setattr(scheduler_module, "get_adapter", SleepingAdapter)

    requested = scheduler.execute_cognitive_turn()
    assert requested == {"status": "ai_sleep_requested", "cycles": 2}
    assert context.events == [("ack", [31])]
    assert scheduler.last_heartbeat_time == 100.0

    now[0] = 106.0
    sleeping = scheduler.execute_cognitive_turn()
    assert sleeping == {"status": "sleeping", "cycles_left": 1}
    assert scheduler.last_heartbeat_time == 106.0


def test_supervisor_rejects_process_that_exits_during_startup(tmp_path, monkeypatch):
    """Popen 後立即崩潰會回報啟動失敗並關閉日誌檔案。"""
    habitat = tmp_path / "habitat"
    habitat.mkdir()
    (habitat / "main.py").write_text("pass", encoding="utf-8")

    class StubSandbox:
        def get_isolated_environ(self):
            return {}

        def build_command(self, main_py):
            return ["mock-cli", str(main_py)]

    class ExitedProcess:
        pid = 45678

        def poll(self):
            return 17

        def wait(self, timeout=None):
            return 17

    monkeypatch.setattr(supervisor_module, "verify_sandbox_boundaries", lambda path: True)
    monkeypatch.setattr(supervisor_module, "get_sandbox", lambda path: StubSandbox())
    monkeypatch.setattr(supervisor_module.subprocess, "Popen", lambda *args, **kwargs: ExitedProcess())
    supervisor = RuntimeSupervisor(habitat_dir=habitat, startup_timeout=0.05)

    with pytest.raises(RuntimeError, match="立即退出"):
        supervisor.start()

    assert supervisor.process is None
    assert supervisor._log_handle is None


@pytest.mark.asyncio
async def test_shutdown_waits_for_worker_thread_cleanup(tmp_path, monkeypatch):
    """shutdown 等待不可中斷的 CLI 執行緒完成，不重疊下一個回合。"""
    monkeypatch.setattr(scheduler_module, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(scheduler_module.config, "paused", False)
    context = StubContext(needs_evolution=True, signal_ids=[44])
    scheduler, supervisor, _ = build_scheduler(tmp_path, context)
    entered = Event()
    release = Event()

    class SlowAdapter:
        def is_available(self):
            return True

        def execute_turn(self, **kwargs):
            entered.set()
            assert release.wait(timeout=3)
            return "尚未修改"

    monkeypatch.setattr(scheduler_module, "get_adapter", SlowAdapter)
    scheduler.last_heartbeat_time = -100.0
    supervisor.stop = lambda: None
    loop_task = asyncio.create_task(scheduler.run_loop())
    assert await asyncio.to_thread(entered.wait, 3)

    shutdown_task = asyncio.create_task(scheduler.shutdown())
    await asyncio.sleep(0.02)
    assert not shutdown_task.done()
    release.set()
    await asyncio.wait_for(shutdown_task, timeout=3)
    await asyncio.wait_for(loop_task, timeout=1)

    assert context.events == []
    assert scheduler.current_turn_in_progress is False


def test_resource_violation_stops_runtime_and_blocks_new_cli_turns(tmp_path, monkeypatch):
    """資源違規會停止受管進程並阻止後續 CLI 配額消耗。"""
    monkeypatch.setattr(scheduler_module.config, "paused", False)
    scheduler, supervisor, _ = build_scheduler(tmp_path, StubContext())
    stop_calls = []
    cli_calls = []
    supervisor.stop = lambda: stop_calls.append(True)
    monkeypatch.setattr(scheduler_module, "get_adapter", lambda: cli_calls.append(True))

    scheduler.block_runtime_for_resources("RAM 超過測試限制")
    result = scheduler.execute_cognitive_turn()

    assert stop_calls == [True]
    assert result == {"status": "skipped", "reason": "RAM 超過測試限制"}
    assert cli_calls == []
    assert scheduler.last_status == {
        "status": "resource_limited",
        "reason": "RAM 超過測試限制",
    }
    with pytest.raises(RuntimeError, match="RAM 超過測試限制"):
        supervisor.start()


def test_resume_start_failure_restores_pause_and_resource_reason(tmp_path, monkeypatch):
    """恢復失敗會留在暫停狀態，並保留原資源違規原因。"""
    monkeypatch.setattr(scheduler_module.config, "paused", True)
    scheduler, supervisor, _ = build_scheduler(tmp_path, StubContext())
    reason = "磁碟用量超過限制"
    scheduler._pause_requested = True
    scheduler._resource_blocked = True
    scheduler._resource_block_reason = reason
    supervisor.block_for_resource_violation(reason)
    supervisor.start = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("startup failed"))

    with pytest.raises(RuntimeError, match="startup failed"):
        scheduler.resume_runtime()

    assert scheduler_module.config.paused is True
    assert scheduler._pause_requested is True
    assert scheduler._resource_blocked is True
    assert scheduler._resource_block_reason == reason
    assert supervisor.resource_block_reason == reason
    assert scheduler.last_status == {"status": "resume_failed", "reason": "startup failed"}


def test_resume_blank_habitat_enables_external_genesis_without_starting_world(
    tmp_path,
    monkeypatch,
):
    """空白池恢復時只重啟外部認知節拍，不要求世界入口預先存在。"""
    monkeypatch.setattr(scheduler_module.config, "paused", True)
    habitat = tmp_path / "habitat"
    habitat.mkdir()
    scheduler = CognitiveScheduler(
        supervisor=RuntimeSupervisor(habitat_dir=habitat),
        context_manager=StubContext(),
        lifecycle_manager=LifecycleManager(
            habitat_dir=habitat,
            backups_dir=tmp_path / "backups",
        ),
        staging_dir=tmp_path / "staging",
    )
    scheduler._pause_requested = True
    starts = []
    scheduler.supervisor.start = lambda **kwargs: starts.append(kwargs)

    scheduler.resume_runtime()

    assert starts == []
    assert scheduler_module.config.paused is False
    assert scheduler._pause_requested is False
    assert scheduler.last_status == {"status": "running"}
