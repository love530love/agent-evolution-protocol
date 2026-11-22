from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from agent_evolution_protocol.coordination import supervisor


def test_watch_owns_one_python_tick_loop_and_writes_a_heartbeat():
    with TemporaryDirectory() as folder:
        output = StringIO()
        with patch.object(supervisor.commander, "tick", return_value={
            "at": "2026-11-14T12:00:00+00:00", "dispatched": [{"order": "x"}],
            "perceived": {"skipped": True},
        }) as tick, patch.object(supervisor.time, "sleep"):
            result = supervisor.watch(Path(folder), interval_seconds=15, max_ticks=2,
                                      perceive_first=False, stream=output)
        assert result == 0
        assert tick.call_count == 2
        assert all(call.kwargs == {"perceive_first": False} for call in tick.call_args_list)
        assert '"status": "STARTED"' in output.getvalue()
        heartbeat = Path(folder) / "runtime" / "aep-supervisor.heartbeat.json"
        assert '"status": "TICK_COMPLETED"' in heartbeat.read_text(encoding="utf-8")


def test_main_reports_an_existing_owner_without_starting_another_loop(tmp_path):
    with patch.object(supervisor, "watch", side_effect=supervisor.AlreadyRunning("held")), \
         patch("sys.argv", ["supervisor", "--coord-root", str(tmp_path)]):
        assert supervisor.main() == 0


def test_watch_rejects_invalid_intervals_before_starting_a_loop(tmp_path):
    for interval, ticks in ((0, 0), (1, -1)):
        try:
            supervisor.watch(tmp_path, interval_seconds=interval, max_ticks=ticks)
        except ValueError:
            pass
        else:  # pragma: no cover - assertion clarity
            raise AssertionError("invalid supervisor settings were accepted")


def test_observe_only_reads_ack_evidence_without_calling_desktop_dispatch(tmp_path):
    output = StringIO()
    with patch.object(supervisor.commander, "tick") as tick, \
            patch.object(supervisor.commander, "check_acks", return_value={"acked": []}) as check:
        assert supervisor.watch(tmp_path, interval_seconds=1, max_ticks=1,
                                dispatch=False, stream=output) == 0
    assert tick.call_count == 0
    assert check.call_count == 1
    heartbeat = (tmp_path / "runtime" / "aep-supervisor.heartbeat.json").read_text(encoding="utf-8")
    assert '"mode": "observe-only"' in heartbeat
    assert '"dispatch_enabled": false' in heartbeat
    assert '"delivery_claim": "NONE"' in heartbeat
    tick = json.loads(output.getvalue().splitlines()[-1])
    assert tick["dispatch_enabled"] is False
    assert tick["delivery_claim"] == "NONE"


def test_failed_dispatch_tick_keeps_mode_and_non_delivery_claim(tmp_path):
    output = StringIO()
    with patch.object(supervisor.commander, "tick", side_effect=RuntimeError("boom")):
        assert supervisor.watch(tmp_path, interval_seconds=1, max_ticks=1,
                                dispatch=True, stream=output) == 0
    tick = json.loads(output.getvalue().splitlines()[-1])
    assert tick["status"] == "TICK_FAILED"
    assert tick["mode"] == "dispatch"
    assert tick["dispatch_enabled"] is True
    assert tick["delivery_claim"] == "NONE"
