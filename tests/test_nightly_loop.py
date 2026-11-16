from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from agent_evolution_protocol.coordination import nightly_loop


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _config(root: Path, tasks: int = 2) -> Path:
    payload = {
        "schema": nightly_loop.CONFIG_SCHEMA,
        "run_id": "TESTRUN",
        "agent": "chatgpt",
        "bound_thread_id": "BOUND",
        "title_marker": "设计自进化架构",
        "cwd_marker": "AEGS",
        "peer_repo": str(root),
        "fingerprint": "PREVIOUS-EVENT",
        "result_grace_seconds": 1200,
        "delivery_confirm_seconds": 0,
        "max_sends": 5,
        "tasks": [
            {"title": f"task {index}", "prompt":
             "work {event_id}\nfinish with {marker}"}
            for index in range(1, tasks + 1)
        ],
    }
    path = root / "config.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class FakeAdapter:
    def __init__(self, *, newest: bool = True, receipt: dict | None = None):
        self.newest = newest
        self.receipt = receipt or {"report": {"status": "delivered", "task_delivered": True}}
        self.deliveries: list[tuple[str, str]] = []
        self.results: dict[str, dict] = {}
        self.submissions: dict[str, dict] = {}
        self.reconciliations: list[tuple[str, str, str]] = []
        self.transcript = "previous: PREVIOUS-EVENT"

    def threads(self):
        bound = {"thread_id": "BOUND", "title": "设计自进化架构",
                 "cwd": "F:\\PythonProjects1\\AEGS", "updated_at": 200}
        other = {"thread_id": "OTHER", "title": "other", "cwd": "F:\\x",
                 "updated_at": 300 if not self.newest else 100}
        return [bound, other] if self.newest else [other, bound]

    def observe(self):
        return {"activity_evidence": {"state": "ready"}, "input_found": True,
                "drivable": True, "transcript_text": self.transcript,
                "transcript_sha256": "x", "pid": 1}

    def deliver(self, event_id, text):
        self.deliveries.append((event_id, text))
        return self.receipt

    def result(self, thread_id, marker, since):
        return self.results.get(marker, {"status": "absent", "task_result_readback": False})

    def submission(self, thread_id, marker):
        return self.submissions.get(
            marker, {"status": "absent", "message_seen": False})

    def reconcile_session(self, event_id, evidence, session_id):
        self.reconciliations.append((event_id, evidence, session_id))
        return {"state": "RECONCILED", "reconciled": True}

    def git_snapshot(self, repo):
        return {"head": "abc", "branch": "main", "status": "", "log": "abc test"}


def _state(root: Path) -> dict:
    return json.loads((root / "nightly_loop" / "state.json").read_text(encoding="utf-8"))


def test_first_tick_sends_once_and_uses_event_id(tmp_path):
    adapter = FakeAdapter()
    result = nightly_loop.tick(tmp_path, _config(tmp_path), adapter=adapter)
    assert result["status"] == "SENT"
    assert result["send_count"] == 1
    assert len(adapter.deliveries) == 1
    event_id, text = adapter.deliveries[0]
    assert event_id == "AEP-NL-TESTRUN-01"
    assert event_id in text and f"AEP-RESULT:{event_id}" in text


def test_waiting_tick_does_not_resend(tmp_path):
    adapter = FakeAdapter()
    config = _config(tmp_path)
    start = _now()
    nightly_loop.tick(tmp_path, config, now=start, adapter=adapter)
    result = nightly_loop.tick(tmp_path, config, now=start + timedelta(minutes=5),
                               adapter=adapter)
    assert result["status"] == "AWAITING_RESULT"
    assert len(adapter.deliveries) == 1
    assert result["send_count"] == 1


def test_result_readback_advances_without_sending_again(tmp_path):
    adapter = FakeAdapter()
    config = _config(tmp_path)
    start = _now()
    nightly_loop.tick(tmp_path, config, now=start, adapter=adapter)
    event_id = adapter.deliveries[0][0]
    adapter.results[f"AEP-RESULT:{event_id}"] = {
        "status": "seen", "task_result_readback": True, "turn_id": "TURN",
        "completed_at": "2026-11-15T12:00:00Z",
        "message": "AEP-RESULT:" + event_id + "\ncommit: deadbeef\n"
                   "tests: 20 passed\ntest", "rollout_sha256": "sha",
    }
    result = nightly_loop.tick(tmp_path, config, now=start + timedelta(minutes=10),
                               adapter=adapter)
    assert result["status"] == "RESULT_RECORDED"
    assert result["cursor"] == 1
    assert result["fingerprint"] == event_id
    assert len(adapter.deliveries) == 1
    state = _state(tmp_path)
    assert state["items"][0]["status"] == "result"
    assert state["items"][0]["fields"]["commit"] == "deadbeef"
    assert adapter.reconciliations[0][0] == event_id
    assert "task_complete" in adapter.reconciliations[0][1]


def test_uncertain_delivery_holds_and_never_retries(tmp_path):
    adapter = FakeAdapter(receipt={"report": {"status": "SESSION_UNCERTAIN",
                                              "task_delivered": False}})
    config = _config(tmp_path)
    start = _now()
    first = nightly_loop.tick(tmp_path, config, now=start, adapter=adapter)
    second = nightly_loop.tick(tmp_path, config, now=start + timedelta(minutes=5),
                                adapter=adapter)
    assert first["status"] == "HOLD_UNCERTAIN_DELIVERY"
    assert second["status"] == "HOLD_UNCERTAIN_DELIVERY"
    assert len(adapter.deliveries) == 1
    assert adapter.reconciliations == []


def test_uncertain_delivery_confirmed_by_rollout_is_not_resent(tmp_path):
    adapter = FakeAdapter(receipt={"report": {"status": "submitted-unconfirmed",
                                              "task_delivered": False}})
    adapter.submissions["AEP-NL-TESTRUN-01"] = {
        "status": "seen", "message_seen": True, "message_id": "MSG",
        "turn_id": "TURN", "submitted_at": "2026-11-15T12:00:01Z",
        "rollout_sha256": "sha",
    }
    config = _config(tmp_path)
    start = _now()
    first = nightly_loop.tick(tmp_path, config, now=start, adapter=adapter)
    assert first["status"] == "SENT"
    assert first["send_count"] == 1
    state = _state(tmp_path)
    assert state["items"][0]["status"] == "sent"
    assert state["items"][0]["sent_at"] == "2026-11-15T12:00:01Z"
    assert any(row["action"] == "DELIVER_RECOVERED" for row in state["history"])
    assert adapter.reconciliations[0][0] == "AEP-NL-TESTRUN-01"
    adapter.results["AEP-RESULT:AEP-NL-TESTRUN-01"] = {
        "status": "seen", "task_result_readback": True, "turn_id": "TURN",
        "completed_at": "2026-11-15T12:01:00Z",
        "message": "AEP-RESULT:AEP-NL-TESTRUN-01\ncommit: cafe\ntests: 1 passed",
        "rollout_sha256": "sha",
    }
    second = nightly_loop.tick(tmp_path, config, now=start + timedelta(minutes=10),
                                adapter=adapter)
    assert second["status"] == "RESULT_RECORDED"
    assert second["cursor"] == 1
    assert len(adapter.deliveries) == 1


def test_uncertain_delivery_holds_when_rollout_has_no_submission(tmp_path):
    adapter = FakeAdapter(receipt={"report": {"status": "submitted-unconfirmed",
                                              "task_delivered": False}})
    config = _config(tmp_path)
    start = _now()
    first = nightly_loop.tick(tmp_path, config, now=start, adapter=adapter)
    assert first["status"] == "HOLD_UNCERTAIN_DELIVERY"
    assert first["send_count"] == 0
    assert len(adapter.deliveries) == 1
    state = _state(tmp_path)
    assert state["items"][0]["status"] == "uncertain"
    assert state["history"][-1]["payload"]["submission_status"] == "absent"
    assert adapter.reconciliations == []


def test_lease_blocked_by_settled_prior_event_is_requeued(tmp_path):
    adapter = FakeAdapter()
    config = _config(tmp_path)
    start = _now()
    nightly_loop.tick(tmp_path, config, now=start, adapter=adapter)
    adapter.results["AEP-RESULT:AEP-NL-TESTRUN-01"] = {
        "status": "seen", "task_result_readback": True, "turn_id": "TURN-1",
        "completed_at": "2026-11-15T12:00:00Z",
        "message": "AEP-RESULT:AEP-NL-TESTRUN-01\ncommit: dead\ntests: 1",
        "rollout_sha256": "sha",
    }
    nightly_loop.tick(tmp_path, config, now=start + timedelta(minutes=5),
                      adapter=adapter)
    adapter.transcript += "\nAEP-NL-TESTRUN-01"
    adapter.receipt = {"report": {
        "status": "SESSION_UNCERTAIN", "task_delivered": False,
        "previous_event_id": "AEP-NL-TESTRUN-01",
        "reason": "another route left this original session unresolved"}}
    blocked = nightly_loop.tick(tmp_path, config,
                                now=start + timedelta(minutes=10), adapter=adapter)
    assert blocked["status"] == "RETRYABLE_NOT_SENT"
    assert blocked["finished"] is False
    assert blocked["send_count"] == 1
    state = _state(tmp_path)
    assert state["items"][1]["status"] == "queued"
    assert state["items"][1]["requeued_from"] == "SESSION_UNCERTAIN"
    assert any(row["action"] == "DELIVER_NOT_SENT" for row in state["history"])
    adapter.receipt = {"report": {"status": "delivered", "task_delivered": True}}
    retried = nightly_loop.tick(tmp_path, config,
                                now=start + timedelta(minutes=15), adapter=adapter)
    assert retried["status"] == "SENT"
    assert retried["send_count"] == 2
    assert len(adapter.deliveries) == 3


def test_missing_result_after_grace_holds_without_retry(tmp_path):
    adapter = FakeAdapter()
    config = _config(tmp_path)
    start = _now()
    nightly_loop.tick(tmp_path, config, now=start, adapter=adapter)
    result = nightly_loop.tick(tmp_path, config, now=start + timedelta(seconds=1300),
                               adapter=adapter)
    assert result["status"] == "HOLD_RESULT_TIMEOUT"
    assert len(adapter.deliveries) == 1


def test_preflight_rejects_when_bound_thread_is_not_newest(tmp_path):
    adapter = FakeAdapter(newest=False)
    result = nightly_loop.tick(tmp_path, _config(tmp_path), adapter=adapter)
    assert result["status"] == "HOLD_PREFLIGHT"
    assert result["pending"]["status"] == "queued"
    assert not adapter.deliveries


def test_preflight_requires_active_view_fingerprint(tmp_path):
    adapter = FakeAdapter()
    adapter.transcript = "different chat"
    result = nightly_loop.tick(tmp_path, _config(tmp_path), adapter=adapter)
    assert result["status"] == "HOLD_PREFLIGHT"
    assert result["pending"]["status"] == "queued"
    assert not adapter.deliveries


def test_deadline_finishes_without_another_send(tmp_path):
    adapter = FakeAdapter()
    config = _config(tmp_path)
    start = _now()
    nightly_loop.tick(tmp_path, config, now=start - timedelta(hours=9), adapter=adapter)
    result = nightly_loop.tick(tmp_path, config, now=start, adapter=adapter)
    assert result["status"] == "FINISHED_DEADLINE"
    assert len(adapter.deliveries) == 1


def test_report_contains_evidence_and_rollback(tmp_path):
    adapter = FakeAdapter()
    nightly_loop.tick(tmp_path, _config(tmp_path), adapter=adapter)
    report = (tmp_path / "nightly_loop" / "report.md").read_text(encoding="utf-8")
    assert "AEP × AEGS" in report
    assert "task_delivered=true" in report
    assert "schtasks /Delete" in report
    status = json.loads((tmp_path / "nightly_loop" / "status.json").read_text(encoding="utf-8"))
    assert status["aep_head"] == "abc"


def test_proven_not_sent_requeues_without_stale_send_time(tmp_path):
    adapter = FakeAdapter(receipt={
        "status": "input-not-confirmed", "task_delivered": False})
    config = _config(tmp_path)
    start = _now()
    first = nightly_loop.tick(tmp_path, config, now=start, adapter=adapter)
    assert first["status"] == "RETRYABLE_NOT_SENT"
    assert first["finished"] is False
    state = _state(tmp_path)
    item = state["items"][0]
    assert item["status"] == "queued"
    assert not item.get("sent_at")
    assert state["send_count"] == 0
    report = (tmp_path / "nightly_loop" / "report.md").read_text(encoding="utf-8")
    assert "## 恢复记录" in report
    assert "DELIVER_NOT_SENT" in report
    adapter.receipt = {"report": {"status": "delivered", "task_delivered": True}}
    second = nightly_loop.tick(tmp_path, config, now=start + timedelta(minutes=30),
                               adapter=adapter)
    assert second["status"] == "SENT"
    assert second["send_count"] == 1


def test_proven_not_sent_retries_are_bounded(tmp_path):
    adapter = FakeAdapter(receipt={
        "status": "input-not-confirmed", "task_delivered": False})
    config_path = _config(tmp_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["not_sent_retry_budget"] = 2
    config_path.write_text(json.dumps(config), encoding="utf-8")
    start = _now()
    first = nightly_loop.tick(tmp_path, config_path, now=start, adapter=adapter)
    second = nightly_loop.tick(tmp_path, config_path,
                               now=start + timedelta(minutes=30), adapter=adapter)
    assert first["status"] == "RETRYABLE_NOT_SENT"
    assert second["status"] == "HOLD_DELIVERY_BUDGET"
    assert len(adapter.deliveries) == 2
    state = _state(tmp_path)
    assert state["items"][0]["status"] == "held"
    assert any(row["action"] == "DELIVER_BUDGET_EXHAUSTED"
               for row in state["history"])


def test_foreign_draft_is_a_terminal_hold_not_a_retry(tmp_path):
    adapter = FakeAdapter(receipt={"report": {
        "status": "composer-not-empty", "task_delivered": False,
        "existing_len": 523, "requested_len": 480,
        "existing_sha256": "aaa", "requested_sha256": "bbb"}})
    config = _config(tmp_path)
    start = _now()
    first = nightly_loop.tick(tmp_path, config, now=start, adapter=adapter)
    second = nightly_loop.tick(tmp_path, config,
                               now=start + timedelta(minutes=30), adapter=adapter)
    assert first["status"] == "HOLD_DRAFT_CONFLICT"
    assert first["finished"] is True
    assert second["status"] == "HOLD_DRAFT_CONFLICT"
    assert len(adapter.deliveries) == 1
    state = _state(tmp_path)
    assert state["items"][0]["status"] == "held"
    assert not state["items"][0].get("sent_at")
    assert any(row["action"] == "DELIVER_DRAFT_CONFLICT"
               for row in state["history"])


def test_repeated_preflight_failure_stops_on_budget(tmp_path):
    adapter = FakeAdapter()
    adapter.observe = lambda: {
        "activity_evidence": {"state": "ready"}, "input_found": False,
        "input_candidate_count": 1, "input_viable_count": 0,
        "input_rejected": [{"reason": "OUTSIDE_WINDOW: bottom=54"}],
        "drivable": True, "transcript_text": adapter.transcript,
        "transcript_sha256": "x", "pid": 1}
    config_path = _config(tmp_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["preflight_hold_budget"] = 2
    config_path.write_text(json.dumps(config), encoding="utf-8")
    start = _now()
    first = nightly_loop.tick(tmp_path, config_path, now=start, adapter=adapter)
    second = nightly_loop.tick(tmp_path, config_path,
                               now=start + timedelta(minutes=30), adapter=adapter)
    assert first["status"] == "HOLD_PREFLIGHT"
    assert second["status"] == "HOLD_PREFLIGHT_BUDGET"
    assert second["finished"] is True
    assert not adapter.deliveries
    state = _state(tmp_path)
    assert state["preflight_hold_count"] == 2
    hold = next(row for row in state["history"] if row["action"] == "PREFLIGHT_HOLD")
    assert hold["payload"]["input_rejected"][0]["reason"].startswith("OUTSIDE_WINDOW")
    assert any(row["action"] == "PREFLIGHT_BUDGET_EXHAUSTED"
               for row in state["history"])


def test_report_includes_original_deadline_after_resume(tmp_path):
    adapter = FakeAdapter()
    config = _config(tmp_path)
    start = _now()
    nightly_loop.tick(tmp_path, config, now=start, adapter=adapter)
    state = _state(tmp_path)
    state["original_deadline"] = "2026-11-15T20:00:00Z"
    state["resume_count"] = 1
    (tmp_path / "nightly_loop" / "state.json").write_text(
        json.dumps(state), encoding="utf-8")
    nightly_loop.tick(tmp_path, config, now=start + timedelta(minutes=5),
                      adapter=adapter)
    report = (tmp_path / "nightly_loop" / "report.md").read_text(encoding="utf-8")
    assert "原始截止" in report
    assert "2026-11-15T20:00:00Z" in report
    assert "恢复次数：1" in report


def test_stderr_pre_write_refusal_is_classified():
    assert nightly_loop._status_from_stderr(
        "Exception: COMPOSER_NOT_EMPTY: the selected control already holds 523 characters"
    ) == "composer-not-empty"
    assert nightly_loop._status_from_stderr("unrelated failure") == ""
