"""雙軌節拍引擎與調度器單元測試。"""

from pathlib import Path

from core.context.manager import ContextManager
from core.heartbeat.scheduler import CognitiveScheduler
from core.lifecycle.snapshot import LifecycleManager
from core.review.guardian import Guardian
from core.runtime.supervisor import RuntimeSupervisor


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


def test_ai_sleep_command_parsing(tmp_path: Path):
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

    # 模擬設定休眠 5 週期
    scheduler.sleep_cycles_remaining = 5
    res = scheduler.execute_cognitive_turn()

    assert res["status"] == "sleeping"
    assert res["cycles_left"] == 4
    assert scheduler.sleep_cycles_remaining == 4

