from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from agent_evolution_protocol.federation import snapshot


WS = "W-" + "c" * 24


def anchor(**overrides):
    data = {"goal": "ship the engine", "acceptance": ["tests pass"], "constraints": ["no secrets"]}
    data.update(overrides)
    return snapshot.make_anchor(**data)


class AnchorTests(unittest.TestCase):
    def test_anchor_digest_is_stable(self):
        first = anchor()
        second = anchor()
        self.assertEqual(first["sha256"], second["sha256"])

    def test_anchor_requires_goal_and_acceptance(self):
        with self.assertRaises(ValueError):
            snapshot.make_anchor(goal="", acceptance=["x"])
        with self.assertRaises(ValueError):
            snapshot.make_anchor(goal="g", acceptance=[])
        with self.assertRaises(ValueError):
            snapshot.make_anchor(goal="g", acceptance=["  "])

    def test_mutated_anchor_is_refused(self):
        block = anchor()
        block["goal"] = "something easier"
        with self.assertRaises(ValueError):
            snapshot.verify_anchor(block)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.anchor = anchor()

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_and_verify(self):
        built = snapshot.build(WS, anchor=self.anchor, dag={"t1": {"state": "pending"}})
        report = snapshot.verify(built)
        self.assertTrue(report["valid"])
        self.assertEqual(report["tasks"], 1)

    def test_digest_is_reproducible(self):
        moment = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
        one = snapshot.build(WS, anchor=self.anchor, dag={"t1": {"state": "pending"}},
                             version=1, moment=moment)
        two = snapshot.build(WS, anchor=self.anchor, dag={"t1": {"state": "pending"}},
                             version=1, moment=moment)
        self.assertEqual(one["digest"], two["digest"])

    def test_digest_changes_when_state_changes(self):
        moment = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
        one = snapshot.build(WS, anchor=self.anchor, dag={"t1": {"state": "pending"}},
                             version=1, moment=moment)
        two = snapshot.build(WS, anchor=self.anchor, dag={"t1": {"state": "done"}},
                             version=1, moment=moment)
        self.assertNotEqual(one["digest"], two["digest"])

    def test_tampered_dag_breaks_the_digest(self):
        built = snapshot.build(WS, anchor=self.anchor, dag={"t1": {"id": "t1", "state": "pending"}})
        built["dag"]["nodes"][0]["state"] = "done"
        with self.assertRaises(ValueError):
            snapshot.verify(built)

    def test_expected_anchor_mismatch_is_detected(self):
        built = snapshot.build(WS, anchor=self.anchor, dag={})
        with self.assertRaises(ValueError):
            snapshot.verify(built, expected_anchor_sha256="0" * 64)

    def test_snapshot_splits_anchor_dag_and_artifacts(self):
        built = snapshot.build(WS, anchor=self.anchor, dag={"t1": {"id": "t1", "state": "pending"}},
                               artifacts=[{"name": "a.txt", "sha256": "0" * 64, "path": "a.txt"}])
        self.assertEqual(built["schema"], "aep-workspace-snapshot-v1")
        self.assertEqual(built["anchored_request"]["text"], "ship the engine")
        self.assertEqual(built["anchored_request"]["acceptance"], ["tests pass"])
        self.assertEqual([node["id"] for node in built["dag"]["nodes"]], ["t1"])
        self.assertEqual(built["artifacts"][0]["name"], "a.txt")

    def test_edited_anchor_inside_the_snapshot_is_refused(self):
        built = snapshot.build(WS, anchor=self.anchor, dag={})
        built["anchored_request"]["text"] = "something easier"
        with self.assertRaises(ValueError):
            snapshot.verify(built)

    def test_parent_snapshot_is_recorded(self):
        first = snapshot.build(WS, anchor=self.anchor, dag={}, version=1)
        second = snapshot.build(WS, anchor=self.anchor, dag={}, version=2,
                                parent_snapshot=first["digest"])
        self.assertIsNone(first["parent_snapshot"])
        self.assertEqual(second["parent_snapshot"], first["digest"])

    def test_save_latest_and_versions(self):
        first = snapshot.build(WS, anchor=self.anchor, dag={}, version=snapshot.next_version(self.root, WS))
        snapshot.save(self.root, first)
        second = snapshot.build(WS, anchor=self.anchor, dag={"t1": {"state": "done"}},
                                version=snapshot.next_version(self.root, WS))
        snapshot.save(self.root, second)
        self.assertEqual(snapshot.latest(self.root, WS)["version"], 2)
        self.assertEqual(len(snapshot.history(self.root, WS)), 2)

    def test_rollback_returns_verified_old_version(self):
        first = snapshot.build(WS, anchor=self.anchor, dag={}, version=1)
        snapshot.save(self.root, first)
        second = snapshot.build(WS, anchor=self.anchor, dag={"t1": {"state": "done"}}, version=2)
        snapshot.save(self.root, second)
        restored = snapshot.rollback(self.root, WS, 1)
        self.assertEqual(restored["digest"], first["digest"])
        with self.assertRaises(ValueError):
            snapshot.rollback(self.root, WS, 99)

    def test_artifact_is_pinned_by_digest(self):
        path = self.root / "out.txt"
        path.write_text("hello", encoding="utf-8")
        row = snapshot.artifact(path)
        self.assertEqual(row["name"], "out.txt")
        self.assertEqual(row["size"], 5)
        self.assertEqual(len(row["sha256"]), 64)

    def test_missing_artifact_is_refused(self):
        with self.assertRaises(ValueError):
            snapshot.artifact(self.root / "missing.txt")


if __name__ == "__main__":
    unittest.main()
