"""資源監控週期取樣與 CPU 進程身份回歸測試。"""

from pathlib import Path

from core.resource.governor import ResourceGovernor


def test_cpu_sampling_uses_delta_and_resets_for_new_process(tmp_path: Path, monkeypatch):
    """CPU 首次取樣只建立基準，後續增量非零且新 PID 身份重新校準。"""
    governor = ResourceGovernor(
        habitat_dir=tmp_path,
        cpu_limit_percent=200.0,
        sample_interval=1.0,
    )
    monkeypatch.setattr(governor, "get_habitat_disk_usage", lambda: 0)
    root_identity = (101, 10.0)
    cpu_time = [5.0]

    def process_tree(pid):
        return root_identity, {root_identity: cpu_time[0]}, 100, 0

    monkeypatch.setattr(governor, "_collect_process_tree", process_tree)

    first = governor.check_limits(101, now=10.0)
    cpu_time[0] = 6.25
    second = governor.check_limits(101, now=11.0)
    assert first.cpu_percent == 0.0
    assert first.cpu_sampled is False
    assert second.cpu_percent == 125.0
    assert second.cpu_sampled is True

    root_identity = (202, 20.0)
    cpu_time[0] = 50.0
    restarted = governor.check_limits(202, now=12.0)
    assert restarted.cpu_percent == 0.0
    assert restarted.cpu_sampled is False
    assert restarted.is_healthy is True


def test_cpu_limit_requires_sustained_periodic_samples(tmp_path: Path, monkeypatch):
    """CPU 週期取樣連續超限才觸發資源違規，首次基準不當作閒置證據。"""
    governor = ResourceGovernor(
        habitat_dir=tmp_path,
        cpu_limit_percent=10.0,
        sample_interval=1.0,
        cpu_violation_samples=2,
    )
    monkeypatch.setattr(governor, "get_habitat_disk_usage", lambda: 0)
    root_identity = (303, 30.0)
    cpu_time = [0.0]

    def process_tree(pid):
        return root_identity, {root_identity: cpu_time[0]}, 100, 0

    monkeypatch.setattr(governor, "_collect_process_tree", process_tree)

    baseline = governor.check_limits(303, now=30.0)
    cpu_time[0] = 0.5
    first_excess = governor.check_limits(303, now=31.0)
    cpu_time[0] = 1.0
    sustained_excess = governor.check_limits(303, now=32.0)

    assert baseline.is_healthy is True
    assert first_excess.cpu_percent == 50.0
    assert first_excess.is_healthy is True
    assert sustained_excess.is_healthy is False
    assert sustained_excess.warning_message.startswith("CPU 持續超過取樣限制")
