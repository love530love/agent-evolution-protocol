"""A completed Codex turn is read from the client's rollout, read-only."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from agent_evolution_protocol.coordination import codex_rollout


MARKER = "AEP-RESULT:AEP-OPS-20261115-02"
NOW = datetime(2026, 11, 15, 11, 45, 30, tzinfo=timezone.utc)


class RolloutTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.db = self.root / "state.db"
        self.rollout = self.root / "rollout.jsonl"
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, cwd TEXT, "
                    "updated_at INTEGER, rollout_path TEXT)")
        con.execute("INSERT INTO threads VALUES (?,?,?,?,?)",
                    ("thread-1", "设计自进化架构", "F:/AEGS", 1, str(self.rollout)))
        con.commit()
        con.close()

    def _rollout(self, *events):
        self.rollout.write_text("\n".join(json.dumps(e, ensure_ascii=False)
                                          for e in events) + "\n", encoding="utf-8")

    def _complete(self, message, timestamp=NOW.isoformat(), turn="turn-1"):
        return {"timestamp": timestamp, "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": turn,
                            "last_agent_message": message}}

    def test_a_completed_turn_is_visible_with_its_thread_and_turn(self):
        self._rollout(self._complete(f"done\n{MARKER}\ncommit: f72abb2"))
        result = codex_rollout.find_result(self.db, MARKER, title_marker="设计自进化架构")
        self.assertEqual(result["status"], codex_rollout.SEEN)
        self.assertTrue(result["task_result_readback"])
        self.assertEqual(result["thread_id"], "thread-1")
        self.assertEqual(result["turn_id"], "turn-1")
        self.assertIn(MARKER, result["message"])
        self.assertTrue(result["rollout_sha256"])

    def test_an_old_marker_is_not_this_runs_result(self):
        self._rollout(self._complete(MARKER, timestamp=(NOW - timedelta(hours=2)).isoformat()))
        result = codex_rollout.find_result(self.db, MARKER, title_marker="设计自进化架构",
                                           since=NOW - timedelta(minutes=5))
        self.assertEqual(result["status"], codex_rollout.ABSENT)
        self.assertFalse(result["task_result_readback"])

    def test_a_marker_in_a_non_completed_item_is_not_readback(self):
        self._rollout({"timestamp": NOW.isoformat(), "type": "response_item",
                       "payload": {"type": "message", "role": "assistant",
                                   "content": [{"type": "output_text", "text": MARKER}]}})
        result = codex_rollout.find_result(self.db, MARKER, title_marker="设计自进化架构")
        self.assertEqual(result["status"], codex_rollout.ABSENT)

    def test_a_missing_rollout_is_unreadable_not_absent(self):
        self.assertFalse(self.rollout.exists())
        result = codex_rollout.find_result(self.db, MARKER, title_marker="设计自进化架构")
        self.assertEqual(result["status"], codex_rollout.UNREADABLE)
        self.assertFalse(result["task_result_readback"])

    def test_a_thread_can_be_read_by_id(self):
        self._rollout(self._complete(MARKER))
        result = codex_rollout.result_for_thread(self.db, "thread-1", MARKER)
        self.assertEqual(result["status"], codex_rollout.SEEN)
        missing = codex_rollout.result_for_thread(self.db, "thread-x", MARKER)
        self.assertEqual(missing["status"], codex_rollout.NO_THREAD)

    def test_a_user_message_confirms_submission_without_completion(self):
        event = "AEP-NL-20261115-2005-03"
        self._rollout(
            {"timestamp": NOW.isoformat(), "type": "event_msg",
             "payload": {"type": "item_completed", "turn_id": "turn-2",
                         "item": {"type": "UserMessage", "id": "msg-2",
                                  "content": [{"type": "text",
                                               "text": event + "\nwork"}]}}},
            {"timestamp": NOW.isoformat(), "type": "response_item",
             "payload": {"type": "message", "role": "assistant",
                         "content": [{"type": "output_text", "text": event}]}},
        )
        result = codex_rollout.submission_for_thread(self.db, "thread-1", event)
        self.assertEqual(result["status"], codex_rollout.SEEN)
        self.assertTrue(result["message_seen"])
        self.assertEqual(result["message_id"], "msg-2")
        self.assertEqual(result["turn_id"], "turn-2")
        self.assertNotIn("task_result_readback", result)

    def test_an_assistant_echo_is_not_a_submission(self):
        event = "AEP-NL-20261115-2005-03"
        self._rollout(
            {"timestamp": NOW.isoformat(), "type": "response_item",
             "payload": {"type": "message", "role": "assistant",
                         "content": [{"type": "output_text", "text": event}]}},
            {"timestamp": NOW.isoformat(), "type": "event_msg",
             "payload": {"type": "task_complete", "turn_id": "turn-2",
                         "last_agent_message": event}},
        )
        result = codex_rollout.read_submission(
            {"thread_id": "thread-1", "rollout_path": str(self.rollout)}, event)
        self.assertEqual(result["status"], codex_rollout.ABSENT)
        self.assertFalse(result["message_seen"])

    def test_submission_for_missing_thread_is_no_thread(self):
        result = codex_rollout.submission_for_thread(self.db, "thread-x", "AEP-NL-x")
        self.assertEqual(result["status"], codex_rollout.NO_THREAD)
        self.assertFalse(result["message_seen"])

    def test_marker_is_required(self):
        with self.assertRaises(ValueError):
            codex_rollout.read_result({"rollout_path": str(self.rollout)}, " ")


if __name__ == "__main__":
    unittest.main()
