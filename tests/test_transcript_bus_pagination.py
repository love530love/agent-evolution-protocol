"""P0 tests for transcript_bus read_events pagination fix (F-02).

Verifies that:
1. read_events() returns the *newest* N events when after="" (not the oldest).
2. read_newest() returns the newest N events in chronological order.
3. When after is provided, read_events keeps the forward-reading semantics.
"""

import json
import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_evolution_protocol.coordination import transcript_bus


# Event IDs must match SAFE_EVENT: ^[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}$
def _event_id(index):
    """Generate a valid event ID like 20261102T100000000001Z-000000000001."""
    return f"20261102T1000000000{index:02d}Z-{'0' * 12}"


def _write_event(folder, event_id, agent="", role="user", session_id=""):
    """Helper to write a test event to disk.
    
    Events must be written under bus_root(coord_root) / "events" = 
    coord_root / "transcript_bus" / "events".
    """
    payload = {
        "id": event_id,
        "agent": agent,
        "role": role,
        "session_id": session_id,
        "recorded_at": f"2026-11-02T10:00:0{int(event_id[-1])}Z",
        "added_lines": [f"Message from {event_id}"],
    }
    (folder / f"{event_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


class TestReadEventsDirection(unittest.TestCase):
    """read_events should return the newest limit events, not the oldest."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        # bus_root(coord_root) = coord_root / "transcript_bus"
        self.bus_dir = self.tmpdir / "transcript_bus"
        self.events_dir = self.bus_dir / "events"
        self.events_dir.mkdir(parents=True)
        # Write 20 events.
        for i in range(1, 21):
            event_id = _event_id(i)
            _write_event(self.events_dir, event_id, agent="test",
                        session_id="4242")

    def test_no_after_returns_newest(self):
        """When after="" (or not provided), read_events should return the
        *newest* 5 events, not the oldest 5."""
        events = transcript_bus.read_events(self.tmpdir, agent="test", limit=5)
        self.assertEqual(len(events), 5)
        # The newest events should be the last 5 written.
        event_ids = [e["id"] for e in events]
        self.assertIn(_event_id(20), event_ids)
        self.assertIn(_event_id(16), event_ids)
        self.assertNotIn(_event_id(1), event_ids)

    def test_after_preserves_forward_semic(self):
        """When after is provided, read_events should return events *after*
        the given cursor (forward reading semantics)."""
        # Read events after evt-0010.
        events = transcript_bus.read_events(self.tmpdir, agent="test",
                                            after=_event_id(10), limit=100)
        self.assertEqual(len(events), 10)
        event_ids = [e["id"] for e in events]
        self.assertNotIn(_event_id(10), event_ids)
        self.assertIn(_event_id(20), event_ids)
        # Should be in chronological order (oldest first).
        self.assertEqual(event_ids, sorted(event_ids))


class TestReadNewest(unittest.TestCase):
    """read_newest() returns the newest N events in chronological order."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.bus_dir = self.tmpdir / "transcript_bus"
        self.events_dir = self.bus_dir / "events"
        self.events_dir.mkdir(parents=True)
        for i in range(1, 11):
            event_id = _event_id(i)
            _write_event(self.events_dir, event_id, agent="test",
                        session_id="4242")

    def test_read_newest_returns_correct_count(self):
        """read_newest should return exactly the requested count."""
        events = transcript_bus.read_newest(self.tmpdir, agent="test", limit=3)
        self.assertEqual(len(events), 3)

    def test_read_newest_is_chronological(self):
        """read_newest should return events in chronological order (oldest first)."""
        events = transcript_bus.read_newest(self.tmpdir, agent="test", limit=5)
        event_ids = [e["id"] for e in events]
        # The newest 5 are evt-0006 through evt-0010.
        self.assertEqual(event_ids, [_event_id(i) for i in range(6, 11)])


class TestReadEventsEmpty(unittest.TestCase):
    """Edge cases for empty event folders."""

    def test_empty_folder_returns_empty_list(self):
        """When the events folder does not exist, read_events should return []."""
        with tempfile.TemporaryDirectory() as tmp:
            coord_root = Path(tmp)
            events = transcript_bus.read_events(coord_root, agent="test")
            self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
