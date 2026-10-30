import unittest

from agent_evolution_protocol.federation import handoff, snapshot


WS = "W-" + "d" * 24


def built():
    anchor = snapshot.make_anchor(goal="migrate", acceptance=["no data loss"])
    return snapshot.build(WS, anchor=anchor, dag={"t1": {"state": "running"}})


class PacketTests(unittest.TestCase):
    def test_packet_points_at_the_snapshot(self):
        snap = built()
        row = handoff.packet(snap, from_agent="alpha", to_agent="beta",
                             reason="token_exhausted", next_action="finish t1")
        self.assertEqual(row["snapshot_digest"], snap["digest"])
        self.assertEqual(row["anchor_sha256"], snap["anchored_request"]["sha256"])
        self.assertTrue(row["snapshot_ref"].endswith(snap["digest"]))
        self.assertTrue(row["handoff_id"].startswith("H-"))

    def test_requires_two_distinct_agents(self):
        snap = built()
        with self.assertRaises(ValueError):
            handoff.packet(snap, from_agent="alpha", to_agent="alpha",
                           reason="stalled", next_action="go")

    def test_unknown_reason_is_refused(self):
        snap = built()
        with self.assertRaises(ValueError):
            handoff.packet(snap, from_agent="alpha", to_agent="beta",
                           reason="because", next_action="go")

    def test_empty_next_action_is_refused(self):
        snap = built()
        with self.assertRaises(ValueError):
            handoff.packet(snap, from_agent="alpha", to_agent="beta",
                           reason="stalled", next_action=" ")


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.snap = built()
        self.row = handoff.packet(self.snap, from_agent="alpha", to_agent="beta",
                                  reason="token_exhausted", next_action="finish t1")

    def test_roundtrip(self):
        report = handoff.verify(self.row)
        self.assertTrue(report["valid"])
        self.assertEqual(report["to_agent"], "beta")

    def test_retargeted_packet_is_detected(self):
        self.row["to_agent"] = "gamma"
        with self.assertRaises(ValueError):
            handoff.verify(self.row)

    def test_other_workspace_is_rejected(self):
        with self.assertRaises(ValueError):
            handoff.verify(self.row, workspace_id="W-" + "e" * 24)

    def test_bad_snapshot_digest_is_rejected(self):
        self.row["snapshot_digest"] = "not-a-digest"
        with self.assertRaises(ValueError):
            handoff.verify(self.row)


class AcceptTests(unittest.TestCase):
    def setUp(self):
        self.snap = built()
        self.row = handoff.packet(self.snap, from_agent="alpha", to_agent="beta",
                                  reason="stalled", next_action="finish t1")

    def test_named_agent_accepts(self):
        result = handoff.accept(self.row, agent="beta", capabilities={"code"},
                                snapshot=self.snap)
        self.assertTrue(result["accepted"])

    def test_wrong_agent_is_refused(self):
        with self.assertRaises(ValueError):
            handoff.accept(self.row, agent="gamma", capabilities={"code"})

    def test_missing_capability_is_refused(self):
        self.row["required_capability"] = "browser"
        self.row["digest"] = handoff.digest({k: v for k, v in self.row.items() if k != "digest"})
        with self.assertRaises(ValueError) as caught:
            handoff.accept(self.row, agent="beta", capabilities={"code"})
        self.assertIn("MISSING_CAPABILITY", str(caught.exception))

    def test_snapshot_mismatch_is_refused(self):
        other = snapshot.build(WS, anchor=snapshot.anchor_of(self.snap),
                               dag={"t1": {"state": "done"}}, version=2)
        with self.assertRaises(ValueError):
            handoff.accept(self.row, agent="beta", capabilities={"code"}, snapshot=other)


if __name__ == "__main__":
    unittest.main()
