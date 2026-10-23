from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from agent_evolution_protocol.coordination import improvement as gate, improvement_work as work


class PromotionFenceTests(unittest.TestCase):
    def test_expiry_during_evidence_hashing_prevents_advance_and_finish(self):
        for entrypoint in ("advance", "finish"):
            for delayed_evidence in ("previous", "submitted"):
                with self.subTest(entrypoint=entrypoint, delayed_evidence=delayed_evidence):
                    with TemporaryDirectory() as temp:
                        root = Path(temp)
                        proof = root / "proof"
                        proof.write_text("baseline")
                        result = root / "result"
                        result.write_text("verified")
                        identity = gate.propose(root, dict(author="author", change="change",
                            acceptance="pass", rollback="restore", scope="fixture",
                            evidence=[str(proof)]))["id"]
                        lease = work.claim(root, identity, actor="tester", ttl=10)
                        original_lease = work._path(root, identity).read_bytes()
                        clock = [lease["expires"] - 1]
                        original_read = Path.read_bytes
                        delayed_path = proof if delayed_evidence == "previous" else result

                        def slow_read(path):
                            content = original_read(path)
                            if path == delayed_path:
                                clock[0] = lease["expires"]
                            return content

                        with patch.object(work, "_now", side_effect=lambda: clock[0]), \
                                patch.object(Path, "read_bytes", slow_read), \
                                patch.object(gate, "_save", wraps=gate._save) as publish:
                            with self.assertRaisesRegex(ValueError, "expired"):
                                if entrypoint == "advance":
                                    gate.advance(root, identity, state="VALIDATED", actor="tester",
                                        token=lease["token"], evidence=[str(result)], note="passed")
                                else:
                                    work.finish(root, identity, actor="tester", token=lease["token"],
                                        evidence=[str(result)], note="passed")
                            publish.assert_not_called()
                        self.assertEqual(gate.status(root, identity)["state"], "PROPOSED")
                        self.assertEqual(work._path(root, identity).read_bytes(), original_lease)

    def test_stage_change_during_hashing_is_rechecked_before_publication(self):
        for with_token in (False, True):
            with self.subTest(with_token=with_token):
                with TemporaryDirectory() as temp:
                    root = Path(temp)
                    proof = root / "proof"
                    proof.write_text("baseline")
                    identity = gate.propose(root, dict(author="author", change="change",
                        acceptance="pass", rollback="restore", scope="fixture",
                        evidence=[str(proof)]))["id"]
                    lease = work.claim(root, identity, actor="tester") if with_token else None
                    original_evidence = gate._evidence

                    def concurrent_decision(paths):
                        records = original_evidence(paths)
                        with patch.object(gate, "_evidence", original_evidence):
                            gate.advance(root, identity, state="REJECTED", actor="operator",
                                evidence=[str(proof)], note="stop during validation")
                        return records

                    with patch.object(gate, "_evidence", side_effect=concurrent_decision), \
                            patch.object(gate, "_save", wraps=gate._save) as publish:
                        with self.assertRaisesRegex(ValueError, "proposal changed"):
                            gate.advance(root, identity, state="VALIDATED", actor="tester",
                                evidence=[str(proof)], note="passed",
                                token=lease["token"] if lease else None)
                        self.assertEqual(publish.call_count, 1)
                        self.assertEqual(publish.call_args.args[2]["state"], "REJECTED")
                    self.assertEqual(gate.status(root, identity)["state"], "REJECTED")
