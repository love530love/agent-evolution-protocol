"""Every document the federation engine writes must validate against the
schemas the workspace already publishes.

The point of this file is to stop a second, private standard from growing next
to the canonical one. If the engine needs a field the schema does not have, the
schema is extended deliberately; the engine does not get its own dialect.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

try:
    import jsonschema
except ModuleNotFoundError:  # pragma: no cover - declared in the ``test`` extra
    # A missing validator must not read as "conformance passed".  Importing
    # cleanly and skipping with a reason keeps ``unittest discover`` usable on a
    # bare interpreter, while CI installs the extra so the check really runs.
    jsonschema = None

from agent_evolution_protocol.federation import adapter, engine, handoff, snapshot, store


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


def setUpModule():
    if jsonschema is None:
        raise unittest.SkipTest(
            "jsonschema is not installed, so no document was actually "
            "validated; install the test extra (pip install -e .[test])")


def schema(name):
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def check(document, name):
    jsonschema.validate(instance=document, schema=schema(name))
    return document


class SnapshotConformanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = engine.create(self.root, goal="ship the engine",
                                   acceptance=["tests pass"], moment=NOW)

    def tearDown(self):
        self.tmp.cleanup()

    def test_written_snapshot_validates_against_the_published_schema(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="code", moment=NOW)
        built = snapshot.build(self.state["workspace_id"], anchor=self.state["anchor"],
                               dag=self.state["tasks"], moment=NOW, version=1)
        check(built, "workspace-snapshot-v1.schema.json")

    def test_engine_checkpoint_file_on_disk_validates(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="code", moment=NOW)
        engine.checkpoint(self.root, self.state, moment=NOW, reason="test")
        folder = store.workspace_dir(self.root, self.state["workspace_id"]) / "snapshots"
        for path in sorted(folder.glob("v*.json")):
            check(json.loads(path.read_text(encoding="utf-8")),
                  "workspace-snapshot-v1.schema.json")


class EventConformanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = engine.create(self.root, goal="ship the engine",
                                   acceptance=["tests pass"], moment=NOW)
        self.log = store.EventLog(self.root, self.state["workspace_id"])

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_event_line_validates_against_the_published_schema(self):
        for index in range(3):
            self.log.append("tick", actor="kernel", payload={"i": index}, moment=NOW)
        for line in self.log.path.read_text(encoding="utf-8").splitlines():
            check(json.loads(line), "event-envelope-v1.schema.json")


class HandoffConformanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = engine.create(self.root, goal="ship the engine",
                                   acceptance=["tests pass"], moment=NOW)
        engine.add_task(self.root, self.state, task_id="t1", requirement="code", moment=NOW)
        engine.checkpoint(self.root, self.state, moment=NOW, reason="test")
        self.built = snapshot.latest(self.root, self.state["workspace_id"])

    def tearDown(self):
        self.tmp.cleanup()

    def test_packet_validates_against_the_published_schema(self):
        row = handoff.packet(self.built, from_agent="alpha", to_agent="beta",
                             reason="token_exhausted", next_action="finish t1", moment=NOW)
        check(row, "handoff-packet-v1.schema.json")

    def test_packet_pins_the_snapshot_it_points_at(self):
        row = handoff.packet(self.built, from_agent="alpha", to_agent="beta",
                             reason="stalled", next_action="finish t1", moment=NOW)
        self.assertTrue(row["snapshot_ref"].endswith(self.built["digest"]))
        self.assertEqual(row["anchor_sha256"], self.built["anchored_request"]["sha256"])


class AdapterBundleConformanceTests(unittest.TestCase):
    def test_adapter_renders_a_valid_capability_bundle(self):
        fake = adapter.FakeAdapter("alpha", {"code", "review"})
        check(adapter.bundle(fake, workspace_id="W-" + "d" * 24),
              "agent-capability-bundle-v1.schema.json")

    def test_unknown_kind_is_refused_before_emitting_an_invalid_document(self):
        fake = adapter.FakeAdapter("alpha", {"code"})
        with self.assertRaises(ValueError):
            adapter.bundle(fake, kind="telepathy")
        with self.assertRaises(ValueError):
            adapter.bundle(fake, transport="smoke-signal")

    def test_bundle_reports_the_real_capabilities(self):
        fake = adapter.FakeAdapter("alpha", {"code", "review"})
        document = adapter.bundle(fake)
        self.assertEqual(document["capabilities"], ["code", "review"])
        self.assertEqual(document["connectors"][0]["capabilities"], ["code", "review"])


class NoPrivateSchemaTests(unittest.TestCase):
    def test_every_schema_file_is_versioned(self):
        """A schema without ``-vN`` is how a private dialect starts."""
        offenders = [path.name for path in SCHEMAS.glob("*.schema.json")
                     if not path.name.replace(".schema.json", "").rsplit("-v", 1)[-1].isdigit()]
        self.assertEqual(offenders, [])

    def test_engine_does_not_ship_its_own_schema_copy(self):
        names = {path.name for path in SCHEMAS.glob("*.schema.json")}
        self.assertEqual(sorted(name for name in names if name.startswith("federation-")), [])


class EndToEndConformanceTests(unittest.TestCase):
    """One real run, then validate every artifact it produced."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_full_handoff_run_leaves_only_conformant_documents(self):
        state = engine.create(self.root, goal="ship the engine",
                              acceptance=["tests pass"], moment=NOW)
        engine.add_task(self.root, state, task_id="t1", requirement="code", moment=NOW)
        alpha = adapter.FakeAdapter("alpha", {"code"}, outcomes=("token_exhausted",),
                                    heartbeat_at=NOW.isoformat())
        beta = adapter.FakeAdapter("beta", {"code"}, heartbeat_at=NOW.isoformat())
        engine.tick(self.root, state, [alpha, beta], now=NOW, moment=NOW)
        self.assertEqual(state["tasks"]["t1"]["owner"], "beta")

        workspace = store.workspace_dir(self.root, state["workspace_id"])
        snapshots = sorted((workspace / "snapshots").glob("v*.json"))
        self.assertTrue(snapshots)
        for path in snapshots:
            check(json.loads(path.read_text(encoding="utf-8")),
                  "workspace-snapshot-v1.schema.json")

        events = [json.loads(line) for line
                  in (workspace / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertTrue(events)
        for event in events:
            check(event, "event-envelope-v1.schema.json")

        self.assertTrue(state["handoffs"])
        for row in state["handoffs"]:
            check(row, "handoff-packet-v1.schema.json")


if __name__ == "__main__":
    unittest.main()
