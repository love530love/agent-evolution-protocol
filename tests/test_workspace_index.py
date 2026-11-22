"""The shared workspace index is read-only, fail-closed and reuses standards."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from agent_evolution_protocol.coordination import knowledge_store, workspace_index as wi

FIXED = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


def _presence(root, agent, state="online", at="2026-11-22T11:00:00+00:00",
              expires="2026-11-22T11:30:00+00:00"):
    d = root / "presence" / "agents"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{agent}.json").write_text(json.dumps({
        "agent": agent, "state": state, "source": "self-report",
        "observed_at": at, "expires_at": expires,
    }), encoding="utf-8")


def test_seat_directory_lists_every_identity_source(tmp_path):
    _presence(tmp_path, "qoder", "online")
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "codex.json").write_text("{}", encoding="utf-8")
    (tmp_path / "mailbox").mkdir()
    (tmp_path / "mailbox" / "github_copilot").mkdir()
    view = wi.members(tmp_path, at=FIXED)
    names = {s["agent"] for s in view["seats"]}
    assert {"qoder", "codex", "github_copilot"} <= names
    assert view["schema"] == wi.SEAT_SCHEMA
    assert "NOT agent acceptance" in view["note"]


def test_seat_is_kept_after_expiry_until_reporting_window(tmp_path):
    _presence(tmp_path, "late_agent", "online",
              expires="2026-11-21T11:30:00+00:00")
    seat = {s["agent"]: s for s in wi.members(tmp_path, at=FIXED)["seats"]}["late_agent"]
    assert seat["seat_kept"] is True  # expired < 7 days: seat retained
    assert seat["state"] in {"stale", "offline", "unknown"}


def test_very_old_seat_is_not_kept(tmp_path):
    _presence(tmp_path, "ancient", "offline",
              expires="2026-11-01T00:00:00+00:00")
    seat = {s["agent"]: s for s in wi.members(tmp_path, at=FIXED)["seats"]}["ancient"]
    assert seat["seat_kept"] is False


def test_newcomers_finds_recent_first_evidence(tmp_path):
    _presence(tmp_path, "newbie", "online", at="2026-11-22T11:50:00+00:00")
    _presence(tmp_path, "veteran", "online", at="2026-11-20T08:00:00+00:00")
    view = wi.newcomers(tmp_path, within_hours=24, at=FIXED)
    assert [n["agent"] for n in view["joined"]] == ["newbie"]
    assert view["count"] == 1


def test_newcomers_unknown_time_is_not_claimed_as_joined(tmp_path):
    _presence(tmp_path, "mystery", "online", at="")
    view = wi.newcomers(tmp_path, at=FIXED)
    assert view["joined"] == []
    assert "unknown" in view["note"]


def test_chat_uses_existing_journal_and_reports_cursor(tmp_path):
    from agent_evolution_protocol.coordination.conversation_journal import append_event
    append_event(tmp_path, room="agent-legion", actor="qoder", source="test",
                 kind="status", text="hello team")
    view = wi.chat(tmp_path, limit=10)
    assert view["count"] == 1
    assert view["events"][0]["text"] == "hello team"
    assert view["next_cursor"] == view["events"][0]["id"]
    assert "not an ACK" in view["note"]


def test_knowledge_store_absent_is_empty_not_created(tmp_path):
    db = tmp_path / "missing.sqlite"
    view = wi.knowledge(db)
    assert view["available"] is False
    assert view["reason"] == "STORE_ABSENT"
    assert not db.exists()  # reading never creates the file


def test_knowledge_query_is_parameterised_and_read_only(tmp_path):
    db = tmp_path / "k.sqlite"
    knowledge_store.add(db, kind="lesson", actor="qoder", task_id="T1",
                        title="DPI", body="use UIA anchor", tags="uia")
    view = wi.knowledge(db, contains="DPI", kind="lesson")
    assert view["available"] is True and view["count"] == 1
    assert view["rows"][0]["task_id"] == "T1"
    # no caller SQL reaches sqlite: there is no sql parameter at all
    assert "sql" not in view


def test_query_rejects_out_of_range_limit(tmp_path):
    try:
        wi.knowledge(tmp_path / "k.sqlite", limit=501)
    except ValueError:
        pass
    else:
        raise AssertionError("limit above 500 must be rejected")


def test_overview_aggregates_without_writing(tmp_path):
    _presence(tmp_path, "qoder", "online")
    view = wi.overview(tmp_path, at=FIXED)
    assert view["view"] == "overview"
    assert view["members"]["total"] >= 1
    assert view["knowledge"]["available"] is False
    assert "Advisory only" in view["note"]


def test_roles_exposes_functional_names_not_vendors(tmp_path):
    view = wi.roles(tmp_path)
    role_ids = {r["id"] for r in view["roles"]}
    assert {"commander", "scout", "github", "builder"} <= role_ids
    assert "not vendor" in view["note"]


def test_health_view_reports_layers_and_never_claims_ack(tmp_path):
    _presence(tmp_path, "qoder", "online")
    view = wi.health(tmp_path, at=FIXED)
    assert view["view"] == "health"
    assert "verdict" in view and "online_layers" in view
    assert "not a delivery or ACK" in view["note"]
    assert "Stale is never offline" in view["note"]


def test_overview_includes_health_verdict(tmp_path):
    view = wi.overview(tmp_path, at=FIXED)
    assert "health_verdict" in view
