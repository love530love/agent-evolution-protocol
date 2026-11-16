from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from agent_evolution_protocol.coordination import core_host


def test_cycle_never_dispatches_and_records_a_skipped_federation():
    with TemporaryDirectory() as folder, \
            patch.object(core_host.campaign_tick, "tick", return_value={"ok": True}), \
            patch.object(core_host.mission_pulse, "pulse", return_value={"queued": []}) as pulse:
        result = core_host.cycle(Path(folder), bridge_root=Path(folder))
    assert result["ui_touched"] is False
    assert result["dispatch_performed"] is False
    assert result["federation"] == {"status": "SKIPPED", "reason": "FEDERATION_CONTEXT_MISSING"}
    assert pulse.call_args.kwargs["apply"] is True


def test_cycle_rejects_partial_federation_configuration_before_desktop_actions(tmp_path):
    with patch.object(core_host.campaign_tick, "tick", return_value={"ok": True}), \
            patch.object(core_host.mission_pulse, "pulse", return_value={"queued": []}):
        try:
            core_host.cycle(tmp_path, bridge_root=tmp_path, workspace_id="ws")
        except ValueError as exc:
            assert "supplied together" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("partial federation configuration was accepted")


def test_watch_writes_a_core_heartbeat_and_has_no_commander_import_path():
    with TemporaryDirectory() as folder:
        output = StringIO()
        with patch.object(core_host, "cycle", return_value={
            "missions": {"queued": []}, "federation": {"status": "SKIPPED"},
        }) as cycle:
            assert core_host.watch(Path(folder), bridge_root=Path(folder), max_ticks=2,
                                   interval_seconds=5, stream=output, sleep=lambda _: None) == 0
        heartbeat = Path(folder) / "runtime" / "aep-core-host.heartbeat.json"
        assert '"status": "CYCLE_COMPLETED"' in heartbeat.read_text(encoding="utf-8")
        assert '"ui_touched": false' in heartbeat.read_text(encoding="utf-8")
        assert cycle.call_count == 2
