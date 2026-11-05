"""Acceptance gate for the evidence-backed multi-agent relay loop.

The operator's completion criterion is explicit: online agents must dispatch to
each other in turn, return a summary, and only then is a run complete.  Two
things must therefore be true and must stay true:

* a loop has a *recomputed* verdict (receipt + artifact digest + sender reply),
  because the repository previously had no function that could answer "is this
  VERIFIED?" at all;
* the drill never reports an agent online on its behalf and never fabricates an
  ACK.  Presence is self-reported by each agent; a coordinator that writes
  `online` for its peers would be rehearsing with itself.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from agent_evolution_protocol.coordination import loop_verdict as lv
from agent_evolution_protocol.coordination import relay_drill as rd

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "agent_evolution_protocol" / "coordination"
EVENT = "D20261105T080000000000Z-abcdef123456"
SENDER, TARGET = "agent_a", "agent_b"
ANALYSIS = "Independent analysis: the relay evidence binds one event id."


def _root(*, with_loop=True, receipt_state="AGENT_ACK", digest=None,
          reply_hash=None, target=TARGET, sender=SENDER):
    holder = tempfile.TemporaryDirectory()
    root, bridge = Path(holder.name) / "coord", Path(holder.name) / "bridge"
    for folder in (root / "delegation" / "inbox", root / "delegation" / "receipts",
                   root / "delegation" / "artifacts" / EVENT,
                   bridge / "messages" / sender, root / "presence" / "agents"):
        folder.mkdir(parents=True, exist_ok=True)
    if with_loop:
        request = {"id": EVENT, "from": sender, "to": target, "task": "T-RELAY-1",
                   "instruction": "analyse and reply with the same event id",
                   "evidence": ["docs/DELIVERY_TRUTH_MATRIX.zh-CN.md"],
                   "reply_due_at": "2026-11-05T09:00:00+00:00",
                   "allow_new_session": False}
        (root / "delegation" / "inbox" / f"{EVENT}.json").write_text(
            json.dumps(request), encoding="utf-8")
        sha = digest or lv.sha256_text(ANALYSIS)
        receipt = {"id": EVENT, "state": receipt_state, "from": sender, "to": target,
                   "agent_accepted": receipt_state == "AGENT_ACK",
                   "reply": f"ACK {EVENT}; analysis_sha256={sha}"}
        (root / "delegation" / "receipts" / f"{EVENT}.json").write_text(
            json.dumps(receipt), encoding="utf-8")
        artifact = {"schema": "aep-handoff-analysis-v1", "event_id": EVENT,
                    "agent": target, "sha256": sha, "analysis": ANALYSIS}
        (root / "delegation" / "artifacts" / EVENT / f"{target}.json").write_text(
            json.dumps(artifact), encoding="utf-8")
        reply = {"id": EVENT + "-reply", "sender": target, "to": sender,
                 "text": f"collaboration reply; analysis_sha256={reply_hash if reply_hash is not None else sha}",
                 "reply_to": EVENT, "accepted": True}
        (bridge / "messages" / sender / f"{EVENT}-reply.json").write_text(
            json.dumps(reply), encoding="utf-8")
    return root, bridge, holder


class LoopVerdictTests(unittest.TestCase):
    def test_complete_three_way_loop_is_verified(self):
        root, bridge, holder = _root()
        try:
            result = lv.check(root, bridge, EVENT, sender=SENDER, target=TARGET)
            self.assertEqual(result["verdict"], "VERIFIED")
            self.assertEqual(result["reasons"], [])
            self.assertTrue(all(result["checks"].values()))
            self.assertEqual(result["evidence_sha256"], lv.sha256_text(ANALYSIS))
        finally:
            holder.cleanup()

    def test_empty_root_is_not_verified_and_names_the_reason(self):
        root, bridge, holder = _root(with_loop=False)
        try:
            result = lv.check(root, bridge, EVENT, sender=SENDER, target=TARGET)
            self.assertEqual(result["verdict"], "NOT_VERIFIED")
            self.assertIn("REQUEST_MISSING", result["reasons"])
        finally:
            holder.cleanup()

    def test_each_broken_link_is_refused_for_its_own_reason(self):
        cases = (
            ({"receipt_state": "MAILBOX_ONLY"}, "RECEIPT_NOT_ACK"),
            ({"digest": "0" * 64}, "ARTIFACT_DIGEST_MISMATCH"),
            ({"reply_hash": "f" * 64}, "REPLY_HASH_MISMATCH"),
            ({"target": "agent_c"}, "TARGET_MISMATCH"),
        )
        for kwargs, expected in cases:
            with self.subTest(expected=expected):
                root, bridge, holder = _root(**kwargs)
                try:
                    result = lv.check(root, bridge, EVENT, sender=SENDER, target=TARGET)
                    self.assertEqual(result["verdict"], "NOT_VERIFIED")
                    self.assertIn(expected, result["reasons"])
                finally:
                    holder.cleanup()

    def test_hash_mentioned_in_prose_without_the_field_is_refused(self):
        """A bare digest substring must not count as a declared evidence hash."""
        root, bridge, holder = _root()
        try:
            sha = lv.sha256_text(ANALYSIS)
            reply_path = bridge / "messages" / SENDER / f"{EVENT}-reply.json"
            reply_path.write_text(json.dumps({
                "id": EVENT + "-reply", "to": SENDER,
                "text": f"prose that merely mentions {sha} in passing",
                "reply_to": EVENT}), encoding="utf-8")
            result = lv.check(root, bridge, EVENT, sender=SENDER, target=TARGET)
            self.assertEqual(result["verdict"], "NOT_VERIFIED")
            self.assertIn("REPLY_HASH_MISMATCH", result["reasons"])
            receipt_path = root / "delegation" / "receipts" / f"{EVENT}.json"
            receipt_path.write_text(json.dumps({
                "state": "AGENT_ACK", "to": TARGET, "agent_accepted": True,
                "reply": f"the number {sha} appears but is not a declared field"}),
                encoding="utf-8")
            result = lv.check(root, bridge, EVENT, sender=SENDER, target=TARGET)
            self.assertIn("RECEIPT_HASH_MISMATCH", result["reasons"])
        finally:
            holder.cleanup()

    def test_sender_and_target_can_be_inferred_when_both_are_omitted(self):
        root, bridge, holder = _root()
        try:
            result = lv.check(root, bridge, EVENT)
            self.assertEqual(result["verdict"], "VERIFIED")
            self.assertEqual(result["evidence_sha256"], lv.sha256_text(ANALYSIS))
        finally:
            holder.cleanup()

    def test_missing_reply_is_refused(self):
        root, bridge, holder = _root()
        try:
            (bridge / "messages" / SENDER / f"{EVENT}-reply.json").unlink()
            result = lv.check(root, bridge, EVENT, sender=SENDER, target=TARGET)
            self.assertEqual(result["verdict"], "NOT_VERIFIED")
            self.assertIn("REPLY_MISSING", result["reasons"])
        finally:
            holder.cleanup()

    def test_check_is_read_only(self):
        root, bridge, holder = _root()
        try:
            before = sorted(str(p.relative_to(root.parent)) for p in root.parent.rglob("*"))
            lv.check(root, bridge, EVENT, sender=SENDER, target=TARGET)
            after = sorted(str(p.relative_to(root.parent)) for p in root.parent.rglob("*"))
            self.assertEqual(before, after)
        finally:
            holder.cleanup()


class RelayDrillTests(unittest.TestCase):
    def _online(self, root, names):
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        for name in names:
            (root / "presence" / "agents" / f"{name}.json").write_text(json.dumps({
                "agent": name, "state": "online", "source": "self-report",
                "session_id": "", "observed_at": now.isoformat(),
                "expires_at": (now + timedelta(minutes=30)).isoformat()}), encoding="utf-8")

    def test_no_fresh_online_agents_is_an_explicit_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, bridge = Path(tmp) / "coord", Path(tmp) / "bridge"
            (root / "presence" / "agents").mkdir(parents=True)
            (root / "delegation" / "inbox").mkdir(parents=True)
            report = rd.run(root, bridge)
            self.assertFalse(report["run_complete"])
            self.assertEqual(report["blocked"], "NEED_AT_LEAST_TWO_ONLINE_AGENTS")
            self.assertEqual(report["fresh_online"], [])
            self.assertIn("join-agent.ps1", report["how_to_signal"])
            self.assertEqual(report["legs"], [])

    def test_stale_presence_does_not_count_as_online(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, bridge = Path(tmp) / "coord", Path(tmp) / "bridge"
            (root / "presence" / "agents").mkdir(parents=True)
            (root / "delegation" / "inbox").mkdir(parents=True)
            (root / "presence" / "agents" / "agent_a.json").write_text(json.dumps({
                "agent": "agent_a", "state": "online", "observed_at": "2026-10-29T13:22:26+00:00",
                "expires_at": "2026-10-29T13:52:26+00:00"}), encoding="utf-8")
            report = rd.run(root, bridge)
            self.assertEqual(report["fresh_online"], [])
            self.assertEqual(report["blocked"], "NEED_AT_LEAST_TWO_ONLINE_AGENTS")

    def test_three_online_agents_form_a_cycle_where_each_sends_and_receives_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, bridge = Path(tmp) / "coord", Path(tmp) / "bridge"
            (root / "presence" / "agents").mkdir(parents=True)
            (root / "delegation" / "inbox").mkdir(parents=True)
            self._online(root, ["agent_a", "agent_b", "agent_c"])
            report = rd.run(root, bridge)
            legs = report["legs"]
            self.assertEqual(len(legs), 3)
            self.assertEqual([x["sender"] for x in legs], ["agent_a", "agent_b", "agent_c"])
            self.assertEqual([x["target"] for x in legs], ["agent_b", "agent_c", "agent_a"])
            self.assertEqual(report["blocked"], "")
            self.assertFalse(report["run_complete"], "a plan alone is never a passed run")
            self.assertFalse(report["applied"])

    def test_dry_run_writes_nothing_and_never_signals_presence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, bridge = Path(tmp) / "coord", Path(tmp) / "bridge"
            (root / "presence" / "agents").mkdir(parents=True)
            (root / "delegation" / "inbox").mkdir(parents=True)
            self._online(root, ["agent_a", "agent_b"])
            with mock.patch.object(rd.team_dispatch, "create") as create, \
                    mock.patch.object(rd.presence, "signal") as signal:
                report = rd.run(root, bridge)
                create.assert_not_called()
                signal.assert_not_called()
            self.assertEqual(report["writes_performed"], 0)
            self.assertEqual(list((root / "delegation" / "inbox").glob("*.json")), [])

    def test_apply_creates_one_dispatch_per_leg_without_signalling_for_peers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, bridge = Path(tmp) / "coord", Path(tmp) / "bridge"
            (root / "presence" / "agents").mkdir(parents=True)
            (root / "delegation" / "inbox").mkdir(parents=True)
            self._online(root, ["agent_a", "agent_b"])
            with mock.patch.object(rd.presence, "signal") as signal:
                report = rd.run(root, bridge, apply=True)
                signal.assert_not_called()
            self.assertTrue(report["applied"])
            self.assertEqual(report["writes_performed"], 2)
            self.assertEqual(len(list((root / "delegation" / "inbox").glob("*.json"))), 2)
            self.assertFalse(report["run_complete"], "dispatch is not acceptance")

    def test_repeated_apply_does_not_duplicate_dispatches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, bridge = Path(tmp) / "coord", Path(tmp) / "bridge"
            (root / "presence" / "agents").mkdir(parents=True)
            (root / "delegation" / "inbox").mkdir(parents=True)
            self._online(root, ["agent_a", "agent_b"])
            first = rd.run(root, bridge, apply=True)
            second = rd.run(root, bridge, apply=True)
            self.assertEqual(len(list((root / "delegation" / "inbox").glob("*.json"))), 2)
            self.assertEqual(first["writes_performed"], 2)
            self.assertEqual(second["writes_performed"], 0)
            self.assertTrue(second["replayed"])
            self.assertEqual(second["legs"], first["legs"])
            self.assertFalse(second["run_complete"], "replay is not a passed run")

    def test_run_complete_only_when_every_leg_is_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, bridge = Path(tmp) / "coord", Path(tmp) / "bridge"
            for folder in (root / "presence" / "agents", root / "delegation" / "inbox",
                           bridge / "messages" / "agent_a"):
                folder.mkdir(parents=True, exist_ok=True)
            self._online(root, ["agent_a", "agent_b"])
            plan = rd.run(root, bridge)
            legs = plan["legs"]
            # Build real three-way evidence for every leg, with each leg's own event id.
            for leg in legs:
                eid = leg["event_id"]
                sender, target = leg["sender"], leg["target"]
                sha = lv.sha256_text(ANALYSIS)
                (root / "delegation" / "receipts").mkdir(parents=True, exist_ok=True)
                (root / "delegation" / "artifacts" / eid).mkdir(parents=True, exist_ok=True)
                (bridge / "messages" / sender).mkdir(parents=True, exist_ok=True)
                (root / "delegation" / "inbox" / f"{eid}.json").write_text(json.dumps(
                    {"id": eid, "from": sender, "to": target}), encoding="utf-8")
                (root / "delegation" / "receipts" / f"{eid}.json").write_text(json.dumps(
                    {"id": eid, "state": "AGENT_ACK", "from": sender, "to": target,
                     "agent_accepted": True, "reply": f"analysis_sha256={sha}"}), encoding="utf-8")
                (root / "delegation" / "artifacts" / eid / f"{target}.json").write_text(
                    json.dumps({"schema": "aep-handoff-analysis-v1", "event_id": eid,
                                "agent": target, "sha256": sha, "analysis": ANALYSIS}),
                    encoding="utf-8")
                (bridge / "messages" / sender / f"{eid}-reply.json").write_text(
                    json.dumps({"text": f"analysis_sha256={sha}", "reply_to": eid}),
                    encoding="utf-8")
            complete = rd.run(root, bridge, legs=legs)
            self.assertTrue(complete["run_complete"])
            self.assertEqual(complete["verified_legs"], len(legs))
            (root / "delegation" / "receipts" / f"{legs[0]['event_id']}.json").write_text(
                json.dumps({"state": "MAILBOX_ONLY"}), encoding="utf-8")
            broken = rd.run(root, bridge, legs=legs)
            self.assertFalse(broken["run_complete"])
            self.assertLess(broken["verified_legs"], len(legs))


class BoundaryTests(unittest.TestCase):
    def test_modules_are_socket_free_model_free_and_loop_free(self):
        banned = {"socket", "urllib", "requests", "httpx", "subprocess", "threading",
                  "asyncio", "multiprocessing", "insight_proposer", "local_model_session"}
        scanned = 0
        for name in ("loop_verdict.py", "relay_drill.py"):
            tree = ast.parse((SRC / name).read_text(encoding="utf-8"))
            self.assertFalse(any(isinstance(n, ast.While) for n in ast.walk(tree)), name)
            for node in ast.walk(tree):
                names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                         else [node.module or ""] + [a.name for a in node.names]
                         if isinstance(node, ast.ImportFrom) else [])
                for imported in names:
                    scanned += 1
                    with self.subTest(module=name, imported=imported):
                        self.assertFalse(any(imported == bad or imported.startswith(bad + ".")
                                             for bad in banned))
        self.assertGreater(scanned, 0)

    def test_capability_card_and_boundary_doc_exist(self):
        card = REPO / "skills" / "aep-relay-drill" / "SKILL.md"
        doc = REPO / "docs" / "OPERATIONAL_FIDELITY_AND_RELAY_DRILL.zh-CN.md"
        self.assertTrue(card.is_file())
        self.assertTrue(doc.is_file())
        text = card.read_text(encoding="utf-8") + doc.read_text(encoding="utf-8")
        for token in ("省积分", "操作拟真度", "依次", "VERIFIED", "join-agent.ps1",
                      "不代报在线", "SENDER_NOT_ONLINE"):
            with self.subTest(token=token):
                self.assertIn(token, text)


if __name__ == "__main__":
    unittest.main()
