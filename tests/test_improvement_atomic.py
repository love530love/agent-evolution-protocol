from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from agent_evolution_protocol.coordination import improvement as gate


class AtomicJournalTests(unittest.TestCase):
    def test_interrupted_publication_leaves_no_partial_final_and_retry_recovers(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            proof = root / "proof"
            proof.write_text("ok")
            request = dict(author="a", change="b", acceptance="c", rollback="d", scope="e", evidence=[str(proof)])
            with patch.object(gate.os, "link", side_effect=OSError("simulated crash before publish")):
                with self.assertRaises(OSError):
                    gate.propose(root, request)
            self.assertFalse(list((root / "improvements").glob("*/*.json")))
            self.assertEqual(gate.propose(root, request)["state"], "PROPOSED")

    def test_duplicate_concurrent_proposals_have_one_complete_first_event(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            proof = root / "proof"
            proof.write_text("ok")
            request = dict(author="a", change="b", acceptance="c", rollback="d", scope="e", evidence=[str(proof)])
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: gate.propose(root, request), range(12)))
            self.assertEqual(len({result["id"] for result in results}), 1)
            self.assertTrue(all(len(result["events"]) == 1 for result in results))
            self.assertFalse(list((root / "improvements").glob("*/*.tmp")))

    def test_failed_next_event_does_not_damage_current_state(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            proof = root / "proof"
            proof.write_text("ok")
            identity = gate.propose(root, dict(author="a", change="b", acceptance="c", rollback="d",
                                   scope="e", evidence=[str(proof)]))["id"]
            with patch.object(gate.os, "fsync", side_effect=OSError("simulated flush failure")):
                with self.assertRaises(OSError):
                    gate.advance(root, identity, state="VALIDATED", actor="tester", evidence=[str(proof)], note="pass")
            self.assertEqual(gate.status(root, identity)["state"], "PROPOSED")
            self.assertEqual(gate.advance(root, identity, state="VALIDATED", actor="tester",
                                        evidence=[str(proof)], note="retry after inspection")["state"], "VALIDATED")
