import json
from pathlib import Path
import tempfile
import unittest

from agent_evolution_protocol.federation import store


WS = "W-" + "a" * 24


class AtomicWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip(self):
        path = store.atomic_write(self.root / "deep" / "state.json", {"a": 1})
        self.assertEqual(store.read_json(path), {"a": 1})

    def test_missing_file_returns_default(self):
        self.assertIsNone(store.read_json(self.root / "nope.json"))
        self.assertEqual(store.read_json(self.root / "nope.json", default={}), {})

    def test_no_temporary_file_is_left_behind(self):
        store.atomic_write(self.root / "state.json", {"a": 1})
        self.assertEqual([p.name for p in self.root.iterdir()], ["state.json"])

    def test_invalid_workspace_id_is_rejected(self):
        with self.assertRaises(ValueError):
            store.workspace_dir(self.root, "not-a-workspace")

    def test_digest_is_key_order_independent(self):
        self.assertEqual(store.digest({"a": 1, "b": 2}), store.digest({"b": 2, "a": 1}))


class EventLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.log = store.EventLog(self.root, WS)

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_log_is_valid(self):
        self.assertEqual(self.log.verify(), {"valid": True, "events": 0, "head": ""})

    def test_chain_is_valid_after_appends(self):
        for index in range(3):
            self.log.append("tick", actor="kernel", payload={"i": index})
        report = self.log.verify()
        self.assertTrue(report["valid"])
        self.assertEqual(report["events"], 3)

    def test_edited_event_is_detected(self):
        self.log.append("tick", actor="kernel")
        self.log.append("tick", actor="kernel")
        rows = self.log.path.read_text(encoding="utf-8").splitlines()
        rows[0] = rows[0].replace('"kernel"', '"intruder"')
        self.log.path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        report = self.log.verify()
        self.assertFalse(report["valid"])
        self.assertEqual(report["seq"], 1)

    def test_deleted_event_is_detected(self):
        for _ in range(3):
            self.log.append("tick", actor="kernel")
        rows = self.log.path.read_text(encoding="utf-8").splitlines()
        self.log.path.write_text("\n".join([rows[0], rows[2]]) + "\n", encoding="utf-8")
        report = self.log.verify()
        self.assertFalse(report["valid"])
        self.assertIn("sequence", report["reason"])

    def test_foreign_workspace_event_is_detected(self):
        self.log.append("tick", actor="kernel")
        rows = self.log.path.read_text(encoding="utf-8").splitlines()
        rows[0] = rows[0].replace(WS, "W-" + "b" * 24)
        self.log.path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        report = self.log.verify()
        self.assertFalse(report["valid"])
        self.assertIn("watermark", report["reason"])

    def test_event_requires_kind_and_actor(self):
        with self.assertRaises(ValueError):
            self.log.append("", actor="kernel")
        with self.assertRaises(ValueError):
            self.log.append("tick", actor="  ")

    def test_append_returns_the_written_event(self):
        event = self.log.append("tick", actor="kernel", payload={"x": 1})
        self.assertEqual(event["seq"], 1)
        self.assertEqual(event["workspace_id"], WS)
        self.assertEqual(len(event["sha256"]), 64)

    def test_events_carry_a_stable_id_and_parent_link(self):
        first = self.log.append("tick", actor="kernel")
        second = self.log.append("tick", actor="kernel")
        self.assertEqual(first["schema"], "aep-event-envelope-v1")
        self.assertGreaterEqual(len(first["event_id"]), 8)
        self.assertIsNone(first["parent_event_id"])
        self.assertEqual(second["parent_event_id"], first["event_id"])

    def test_reparented_event_is_detected_even_with_a_recomputed_hash(self):
        self.log.append("tick", actor="kernel")
        self.log.append("tick", actor="kernel")
        rows = [json.loads(line) for line in self.log.path.read_text(encoding="utf-8").splitlines()]
        rows[1]["parent_event_id"] = "Edeadbeefdeadbeef"
        base = {key: rows[1][key] for key in store.ID_FIELDS}
        rows[1]["event_id"] = "E" + store.digest(base)[:15]
        rows[1]["sha256"] = store.digest({key: value for key, value in rows[1].items()
                                          if key != "sha256"})
        self.log.path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n",
            encoding="utf-8")
        report = self.log.verify()
        self.assertFalse(report["valid"])
        self.assertEqual(report["seq"], 2)
        self.assertIn("parent", report["reason"])


if __name__ == "__main__":
    unittest.main()
