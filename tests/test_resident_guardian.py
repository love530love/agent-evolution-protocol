from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from agent_evolution_protocol.coordination import resident_guardian


class Clock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


class Process:
    def __init__(self, pid: int, polls: list[int | None] | None = None):
        self.pid = pid
        self.polls = list(polls or [None])
        self.terminated = 0
        self.killed = 0

    def poll(self):
        if len(self.polls) > 1:
            return self.polls.pop(0)
        return self.polls[0]

    def terminate(self):
        self.terminated += 1
        self.polls = [15]

    def kill(self):
        self.killed += 1
        self.polls = [9]

    def wait(self, timeout=None):
        result = self.poll()
        return 0 if result is None else result


class Job:
    """In-memory ownership witness for deterministic Guardian tests."""

    def __init__(self, name: str):
        self.name = name
        self.assigned: list[int] = []
        self.closed = 0

    def assign_process(self, pid: int):
        self.assigned.append(pid)

    def close(self):
        self.closed += 1


class Job:
    """In-memory ownership witness for deterministic Guardian tests."""

    def __init__(self, name: str):
        self.name = name
        self.assigned: list[int] = []
        self.closed = 0

    def assign_process(self, pid: int):
        self.assigned.append(pid)

    def close(self):
        self.closed += 1


def _job_factory(name: str) -> Job:
    return Job(name)


def test_child_command_preserves_observe_only_and_never_uses_a_shell(tmp_path):
    command = resident_guardian._child_command(
        tmp_path, interval_seconds=15, observe_only=True, no_perceive=False,
    )
    assert command[:3] == [resident_guardian._child_interpreter(), "-m",
                           "agent_evolution_protocol.coordination.supervisor"]
    assert "--observe-only" in command
    assert "--guardian-managed" in command
    assert "--guardian-run-id" not in command


def test_child_command_passes_local_queue_only_in_dispatch_mode(tmp_path):
    command = resident_guardian._child_command(
        tmp_path, interval_seconds=15, observe_only=False, no_perceive=False,
        local_queue=True,
    )
    assert "--local-queue" in command


def test_guardian_rejects_local_queue_in_observe_only_mode(tmp_path):
    with pytest.raises(ValueError, match="local_queue requires dispatch mode"):
        resident_guardian.watch(tmp_path, observe_only=True, local_queue=True, max_ticks=1,
                                spawn=lambda command: Process(501), job_factory=_job_factory)


def test_restart_history_discards_corrupt_stale_and_future_rows(tmp_path):
    now = datetime(2026, 11, 27, tzinfo=timezone.utc)
    path = tmp_path / "runtime" / "aep-desktop-guardian.state.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"restart_times": [
        (now - timedelta(seconds=20)).isoformat(),
        (now - timedelta(seconds=100)).isoformat(),
        (now + timedelta(seconds=3)).isoformat(),
        "bad-value",
    ]}), encoding="utf-8")
    result = resident_guardian._restart_history(tmp_path, now=now, window_seconds=60)
    assert result == [now - timedelta(seconds=20)]


def test_guardian_stops_only_its_owned_child_when_a_smoke_run_ends(tmp_path):
    clock = Clock(datetime(2026, 11, 27, tzinfo=timezone.utc))
    child = Process(501)
    commands = []

    def spawn(command):
        commands.append(command)
        return child

    jobs: list[Job] = []

    def factory(name: str) -> Job:
        job = Job(name)
        jobs.append(job)
        return job

    assert resident_guardian.watch(
        tmp_path, observe_only=True, max_ticks=1, spawn=spawn,
        sleep=clock.sleep, clock=clock, job_factory=factory,
    ) == 0
    assert len(commands) == 1
    assert child.terminated == 1
    row = json.loads((tmp_path / "runtime" / "aep-desktop-guardian.heartbeat.json").read_text())
    assert row["status"] == "STOPPED"
    assert row["delivery_claim"] == "NONE"
    assert row["ui_touched"] is False
    if resident_guardian.os.name == "nt":
        assert row["child_containment"] == "WINDOWS_JOB"
        assert len(jobs) == 1
        assert jobs[0].assigned == [child.pid]
        assert jobs[0].closed == 1
    else:
        assert row["child_containment"] == "NOT_APPLICABLE"
        assert jobs == []


@pytest.mark.skipif(resident_guardian.os.name != "nt", reason="Windows containment policy")
def test_guardian_refuses_to_start_a_windows_worker_without_a_job(tmp_path):
    clock = Clock(datetime(2026, 11, 27, tzinfo=timezone.utc))
    launched: list[list[str]] = []

    def no_job(name: str):
        raise OSError("test containment unavailable")

    assert resident_guardian.watch(
        tmp_path, observe_only=True, max_ticks=1,
        spawn=lambda command: launched.append(command) or Process(501),
        sleep=clock.sleep, clock=clock, job_factory=no_job,
    ) == 0
    row = json.loads((tmp_path / "runtime" / "aep-desktop-guardian.state.json").read_text())
    assert launched == []
    assert row["status"] == "CONTAINMENT_UNAVAILABLE"
    assert row["child_containment"] == "UNAVAILABLE"


def test_exit_is_restarted_and_the_restart_is_persisted(tmp_path):
    clock = Clock(datetime(2026, 11, 27, tzinfo=timezone.utc))
    first = Process(501, [7])
    second = Process(502)
    created = iter((first, second))

    assert resident_guardian.watch(
        tmp_path, interval_seconds=1, max_ticks=3,
        spawn=lambda command: next(created), sleep=clock.sleep, clock=clock,
        job_factory=_job_factory,
    ) == 0
    row = json.loads((tmp_path / "runtime" / "aep-desktop-guardian.state.json").read_text())
    assert row["restart_count_window"] == 1
    assert second.terminated == 1
    assert first.terminated == 0  # it had already exited; no broad kill


def test_lock_held_exit_is_not_misreported_as_a_normal_worker_exit(tmp_path):
    clock = Clock(datetime(2026, 11, 27, tzinfo=timezone.utc))
    blocked = Process(501, [75])

    assert resident_guardian.watch(
        tmp_path, interval_seconds=1, max_ticks=2,
        spawn=lambda command: blocked, sleep=clock.sleep, clock=clock,
        job_factory=_job_factory,
    ) == 0
    row = json.loads((tmp_path / "runtime" / "aep-desktop-guardian.state.json").read_text())
    assert row["status"] == "WORKER_LOCK_HELD"
    assert row["reason"] == "SUPERVISOR_ALREADY_RUNNING"


def test_stale_or_missing_child_heartbeat_stops_only_direct_child(tmp_path):
    clock = Clock(datetime(2026, 11, 27, tzinfo=timezone.utc))
    child = Process(501)

    assert resident_guardian.watch(
        tmp_path, interval_seconds=2, startup_grace_seconds=1, max_ticks=2,
        spawn=lambda command: child, sleep=clock.sleep, clock=clock,
        job_factory=_job_factory,
    ) == 0
    assert child.terminated == 1
    row = json.loads((tmp_path / "runtime" / "aep-desktop-guardian.state.json").read_text())
    assert row["status"] == "UNHEALTHY"
    assert row["reason"].startswith("HEARTBEAT_MISSING:")


def test_restart_budget_enters_backoff_instead_of_creating_an_infinite_launch_loop(tmp_path):
    clock = Clock(datetime(2026, 11, 27, tzinfo=timezone.utc))
    first = Process(501, [7])
    second = Process(502, [8])
    launches = []

    def spawn(command):
        launches.append(command)
        return (first, second)[len(launches) - 1]

    assert resident_guardian.watch(
        tmp_path, interval_seconds=1, max_ticks=5, max_restarts=1,
        restart_window_seconds=60, spawn=spawn, sleep=clock.sleep, clock=clock,
        job_factory=_job_factory,
    ) == 0
    assert len(launches) == 2  # initial launch + the one allowed recovery
    row = json.loads((tmp_path / "runtime" / "aep-desktop-guardian.state.json").read_text())
    assert row["status"] == "RESTART_BACKOFF"
    assert row["reason"] == "RESTART_BUDGET_EXHAUSTED"
