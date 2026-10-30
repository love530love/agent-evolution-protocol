from pathlib import Path
import tempfile
import unittest

from agent_evolution_protocol.federation import evolution


WS = "W-" + "f" * 24


class ProposeTests(unittest.TestCase):
    def setUp(self):
        self.library = evolution.new_library()

    def test_proposal_is_recorded_once(self):
        first = evolution.propose(self.library, name="segment context", tags=["token_exhausted"],
                                  params={"segments": 4})
        second = evolution.propose(self.library, name="segment context", tags=["token_exhausted"],
                                   params={"segments": 4})
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.library["strategies"]), 1)

    def test_same_name_different_params_is_a_different_strategy(self):
        evolution.propose(self.library, name="segment context", tags=["token_exhausted"],
                          params={"segments": 4})
        evolution.propose(self.library, name="segment context", tags=["token_exhausted"],
                          params={"segments": 8})
        self.assertEqual(len(self.library["strategies"]), 2)

    def test_tags_are_required(self):
        with self.assertRaises(ValueError):
            evolution.propose(self.library, name="x", tags=[])

    def test_params_must_be_an_object(self):
        with self.assertRaises(ValueError):
            evolution.propose(self.library, name="x", tags=["t"], params=["nope"])


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.library = evolution.new_library()
        self.strategy = evolution.propose(self.library, name="cool down browser",
                                          tags=["browser_blocked"], params={"seconds": 300})

    def test_promotion_requires_evidence(self):
        evolution.record(self.library, self.strategy["id"], outcome="success", workspace_id=WS)
        with self.assertRaises(ValueError) as caught:
            evolution.promote(self.library, self.strategy["id"])
        self.assertIn("NOT_ENOUGH_EVIDENCE", str(caught.exception))

    def test_promotion_after_threshold(self):
        for _ in range(2):
            evolution.record(self.library, self.strategy["id"], outcome="success", workspace_id=WS)
        row = evolution.promote(self.library, self.strategy["id"])
        self.assertEqual(row["status"], "promoted")
        self.assertTrue(row["promoted_at"])

    def test_failures_block_promotion(self):
        for _ in range(3):
            evolution.record(self.library, self.strategy["id"], outcome="success", workspace_id=WS)
        evolution.record(self.library, self.strategy["id"], outcome="failure", workspace_id=WS)
        with self.assertRaises(ValueError) as caught:
            evolution.promote(self.library, self.strategy["id"])
        self.assertIn("TOO_MANY_FAILURES", str(caught.exception))

    def test_history_is_append_only(self):
        evolution.record(self.library, self.strategy["id"], outcome="success", workspace_id=WS,
                         note="first recovery")
        evolution.record(self.library, self.strategy["id"], outcome="failure", workspace_id=WS,
                         note="regression")
        history = self.strategy["history"]
        self.assertEqual([row["outcome"] for row in history], ["success", "failure"])
        self.assertEqual(history[0]["note"], "first recovery")

    def test_unknown_strategy_is_refused(self):
        with self.assertRaises(ValueError):
            evolution.record(self.library, "S-deadbeef", outcome="success", workspace_id=WS)
        with self.assertRaises(ValueError):
            evolution.promote(self.library, "S-deadbeef")


class InheritanceTests(unittest.TestCase):
    def setUp(self):
        self.library = evolution.new_library()

    def test_only_promoted_strategies_are_inherited(self):
        promoted = evolution.propose(self.library, name="rotate summary", tags=["token_exhausted"])
        evolution.propose(self.library, name="unverified idea", tags=["loop"])
        for _ in range(2):
            evolution.record(self.library, promoted["id"], outcome="success", workspace_id=WS)
        evolution.promote(self.library, promoted["id"])
        local = evolution.inherit(self.library, WS)
        names = [row["name"] for row in local["strategies"].values()]
        self.assertEqual(names, ["rotate summary"])
        self.assertEqual(local["strategies"][promoted["id"]]["inherited_from"], promoted["id"])

    def test_inherited_library_is_independent(self):
        promoted = evolution.propose(self.library, name="rotate summary", tags=["token_exhausted"])
        for _ in range(2):
            evolution.record(self.library, promoted["id"], outcome="success", workspace_id=WS)
        evolution.promote(self.library, promoted["id"])
        local = evolution.inherit(self.library, WS)
        local["strategies"][promoted["id"]]["successes"] = 99
        self.assertEqual(self.library["strategies"][promoted["id"]]["successes"], 2)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "library.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_and_load_roundtrip(self):
        library = evolution.new_library()
        evolution.propose(library, name="x", tags=["loop"])
        evolution.save(self.path, library)
        self.assertEqual(evolution.load(self.path), library)

    def test_missing_file_yields_empty_library(self):
        self.assertEqual(evolution.load(self.path), evolution.new_library())

    def test_retire_keeps_the_record(self):
        library = evolution.new_library()
        row = evolution.propose(library, name="x", tags=["loop"])
        evolution.retire(library, row["id"], reason="no longer effective")
        self.assertEqual(library["strategies"][row["id"]]["status"], "retired")
        self.assertEqual(library["strategies"][row["id"]]["history"][-1]["outcome"], "retired")

    def test_retire_requires_reason(self):
        library = evolution.new_library()
        row = evolution.propose(library, name="x", tags=["loop"])
        with self.assertRaises(ValueError):
            evolution.retire(library, row["id"], reason="")


if __name__ == "__main__":
    unittest.main()
