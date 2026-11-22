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


def _notice(root, agent, transition, at, name):
    d = root / "presence" / "notices"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps({
        "agent": agent, "transition": transition, "at": at,
        "source": "agent-self-report",
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
    _presence(tmp_path, "veteran", "online", at="2026-11-22T11:59:00+00:00")
    _notice(tmp_path, "newbie", "joined", "2026-11-22T11:50:00+00:00", "new.json")
    _notice(tmp_path, "veteran", "joined", "2026-11-20T08:00:00+00:00", "old.json")
    _notice(tmp_path, "veteran", "rejoined", "2026-11-22T11:59:00+00:00", "rejoin.json")
    view = wi.newcomers(tmp_path, within_hours=24, at=FIXED)
    assert [n["agent"] for n in view["joined"]] == ["newbie"]
    assert view["count"] == 1
    assert view["joined"][0]["evidence"].endswith("new.json")


def test_newcomers_unknown_time_is_not_claimed_as_joined(tmp_path):
    _presence(tmp_path, "mystery", "online", at="2026-11-22T11:59:00+00:00")
    view = wi.newcomers(tmp_path, at=FIXED)
    assert view["joined"] == []
    assert "unknown" in view["note"]


def test_newcomers_ignores_future_and_corrupt_notices(tmp_path):
    _notice(tmp_path, "future", "joined", "2026-11-22T12:01:00+00:00", "future.json")
    _notice(tmp_path, "bad/name", "joined", "2026-11-22T11:00:00+00:00", "bad.json")
    (tmp_path / "presence" / "notices" / "broken.json").write_text("{", encoding="utf-8")
    assert wi.newcomers(tmp_path, at=FIXED)["joined"] == []


def test_newcomers_rejects_invalid_window(tmp_path):
    import pytest
    for value in (0, -1, float("inf"), float("nan"), True):
        with pytest.raises(ValueError):
            wi.newcomers(tmp_path, within_hours=value, at=FIXED)


def test_chat_uses_existing_journal_and_reports_cursor(tmp_path):
    from agent_evolution_protocol.coordination.conversation_journal import append_event
    append_event(tmp_path, room="agent-legion", actor="qoder", source="test",
                 kind="status", text="hello team")
    view = wi.chat(tmp_path, limit=10)
    assert view["count"] == 1
    assert view["events"][0]["text"] == "hello team"
    assert view["next_cursor"] == view["events"][0]["id"]
    assert "not an ACK" in view["note"]


def test_chat_scopes_explicit_workspace_and_excludes_unbound(tmp_path):
    from agent_evolution_protocol.coordination.conversation_journal import append_event
    ws_a = "W-" + "a" * 16
    ws_b = "W-" + "b" * 16
    append_event(tmp_path, room="team", actor="a", source="test", kind="message", text="结果: A-1", workspace_id=ws_a)
    append_event(tmp_path, room="team", actor="b", source="test", kind="message", text="结果: A-1", workspace_id=ws_b)
    append_event(tmp_path, room="team", actor="c", source="test", kind="message", text="结果: A-1")
    append_event(tmp_path, room="other", actor="a", source="test", kind="message", text="结果: A-1", workspace_id=ws_a)
    view = wi.chat(tmp_path, workspace_id=ws_a, room="team", contains="a-1")
    assert view["count"] == 1
    assert view["events"][0]["actor"] == "a"
    assert view["scope"] == "workspace"
    assert wi.chat(tmp_path)["count"] == 4


def test_chat_filtered_cursor_does_not_skip_matching_results(tmp_path):
    from agent_evolution_protocol.coordination.conversation_journal import append_event
    ws_a = "W-" + "a" * 16
    first = append_event(tmp_path, room="team", actor="a", source="test", kind="message", text="first", workspace_id=ws_a)
    append_event(tmp_path, room="team", actor="b", source="test", kind="message", text="unbound")
    second = append_event(tmp_path, room="team", actor="a", source="test", kind="message", text="second", workspace_id=ws_a)
    page = wi.chat(tmp_path, workspace_id=ws_a, limit=1)
    assert page["events"][0]["id"] == first["id"]
    page2 = wi.chat(tmp_path, workspace_id=ws_a, after=page["next_cursor"], limit=1)
    assert page2["events"][0]["id"] == second["id"]


def test_chat_rejects_invalid_workspace_id(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        wi.chat(tmp_path, workspace_id="agent-evolution-protocol")


def test_chat_reports_corrupt_event_and_keeps_reading(tmp_path):
    folder = tmp_path / "private_conversations" / "events"
    folder.mkdir(parents=True)
    first = "20261122T110000000000Z-aaaaaaaaaaaa"
    broken = "20261122T110001000000Z-bbbbbbbbbbbb"
    last = "20261122T110002000000Z-cccccccccccc"
    ws_a = "W-" + "a" * 16
    for event_id, text in ((first, "one"), (last, "two")):
        (folder / (event_id + ".json")).write_text(json.dumps({
            "id": event_id, "workspace_id": ws_a, "room": "team", "text": text,
        }), encoding="utf-8")
    (folder / (broken + ".json")).write_text("{", encoding="utf-8")
    view = wi.chat(tmp_path, workspace_id=ws_a, limit=5)
    assert [item["id"] for item in view["events"]] == [first, last]
    assert view["malformed_count"] == 1
    assert view["malformed_sources"][0]["path"].endswith(broken + ".json")
    assert view["next_cursor"] == last


def test_main_cli_chat_filters_workspace_and_room(tmp_path, capsys):
    from agent_evolution_protocol import cli
    from agent_evolution_protocol.coordination.conversation_journal import append_event
    ws_a = "W-" + "a" * 16
    append_event(tmp_path, room="team", actor="a", source="test", kind="message", text="进展 A-1", workspace_id=ws_a)
    append_event(tmp_path, room="team", actor="b", source="test", kind="message", text="进展 A-1")
    args = cli.build_parser().parse_args([
        "coord-workspace-index", "chat", "--coord-root", str(tmp_path),
        "--workspace-id", ws_a, "--room", "team", "--contains", "A-1",
    ])
    cli.coord_workspace_index(args)
    output = json.loads(capsys.readouterr().out)
    assert output["count"] == 1
    assert output["events"][0]["actor"] == "a"


def test_main_cli_rejects_workspace_filter_for_unscoped_views(tmp_path):
    import pytest
    from agent_evolution_protocol import cli
    for view in ("knowledge", "members", "newcomers", "overview", "health", "roles"):
        args = cli.build_parser().parse_args([
            "coord-workspace-index", view, "--coord-root", str(tmp_path),
            "--workspace-id", "W-" + "a" * 16,
        ])
        with pytest.raises(cli.ProtocolError, match="only supported for the chat view"):
            cli.coord_workspace_index(args)


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


def test_knowledge_read_only_uri_handles_special_path_characters(tmp_path):
    import sqlite3
    import pytest
    folder = tmp_path / "p#room 50%"
    folder.mkdir()
    db = folder / "knowledge.sqlite"
    knowledge_store.add(db, kind="lesson", actor="qoder", body="actual record")
    view = wi.knowledge(db, contains="actual")
    assert view["count"] == 1
    assert view["rows"][0]["body"] == "actual record"
    connection = knowledge_store.connect_read_only(db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE should_not_exist (x INTEGER)")
    finally:
        connection.close()
    assert not (tmp_path / "p").exists()


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
