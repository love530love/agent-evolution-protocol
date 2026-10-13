from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile

from agent_evolution_protocol.coordination import conversation_journal, presence


def test_join_refresh_leave_rejoin_and_unread_digest():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        first = presence.signal(root, agent="workbuddy", state="online", source="test", ttl_minutes=2)
        assert first["notice"]["transition"] == "joined"
        assert presence.snapshot(root)["strength"]["online"] == 1
        repeat = presence.signal(root, agent="workbuddy", state="online", source="test", ttl_minutes=2)
        assert repeat["notice"] is None
        event = conversation_journal.append_event(root, room="test", actor="user", source="test",
                                                  kind="message", text="new instruction")
        left = presence.signal(root, agent="workbuddy", state="offline", source="session_hook:end")
        assert left["notice"]["transition"] == "left"
        joined = presence.signal(root, agent="workbuddy", state="online", source="session_hook:start")
        assert joined["notice"]["transition"] == "rejoined"
        digest = root / "presence" / "digests" / (joined["notice"]["id"] + ".json")
        assert event["id"] in digest.read_text(encoding="utf-8")
        assert presence.snapshot(root)["strength"]["online"] == 1


def test_deadline_marks_stale_once_and_rejoin():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        presence.signal(root, agent="qoder", state="online", source="test", ttl_minutes=1)
        row_path = root / "presence" / "agents" / "qoder.json"
        row = json.loads(row_path.read_text(encoding="utf-8"))
        due = datetime.fromisoformat(row["expires_at"])
        assert presence.next_due(root) == row["expires_at"]
        result = presence.reconcile(root, at=due + timedelta(seconds=1))
        assert result["notices"][0]["transition"] == "lost_contact"
        assert presence.reconcile(root, at=due + timedelta(seconds=2))["notices"] == []
        assert presence.next_due(root) == ""
        assert presence.snapshot(root)["strength"]["stale"] == 1
        assert presence.signal(root, agent="qoder", state="online", source="test")["notice"]["transition"] == "rejoined"


def test_unread_cursor_is_not_advanced_by_presence_notice():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        message = conversation_journal.append_event(root, room="test", actor="user", source="test",
                                                    kind="message", text="read this")
        presence.signal(root, agent="codex", state="online", source="test")
        assert presence.unread(root, "codex")["count"] >= 1
        assert conversation_journal.cursor_for(root, "codex") == ""
        conversation_journal.acknowledge(root, agent="codex", through=message["id"])
        assert conversation_journal.cursor_for(root, "codex") == message["id"]


def test_absence_does_not_equal_offline_without_evidence():
    with tempfile.TemporaryDirectory() as folder:
        assert presence.snapshot(Path(folder))["agents"] == []
        assert presence.reconcile(Path(folder))["notices"] == []
