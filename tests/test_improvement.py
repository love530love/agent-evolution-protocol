from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from agent_evolution_protocol.coordination import improvement as gate


class ImprovementTests(unittest.TestCase):
    def test_lifecycle_and_guards(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            evidence = root / "test.txt"
            evidence.write_text("baseline and test", encoding="utf-8")
            request = dict(author="author", change="fix", acceptance="tests pass",
                           rollback="restore old version", scope="sandbox", evidence=[str(evidence)])
            proposal = gate.propose(root, request)
            identity = proposal["id"]
            self.assertEqual(identity, gate.propose(root, request)["id"])
            def move(state, actor):
                return gate.advance(root, identity, state=state, actor=actor,
                                    evidence=[str(evidence)], note="verified in sandbox")
            with self.assertRaises(ValueError):
                move("ADOPTED", "author")
            move("VALIDATED", "validator")
            with self.assertRaises(ValueError):
                move("REVIEWED", "validator")
            move("REVIEWED", "reviewer")
            move("TRIAL", "operator")
            move("ADOPTED", "operator")
            evidence.write_text("changed", encoding="utf-8")
            final = move("ROLLED_BACK", "operator")
            self.assertFalse(final["deployment_performed"])
            with self.assertRaises(ValueError):
                move("TRIAL", "operator")

    def test_tampering_and_invalid_identity(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(ValueError):
                gate.status(root, "../outside")
            evidence = root / "proof"
            evidence.write_text("ok")
            row = gate.propose(root, dict(author="a", change="b", acceptance="c",
                               rollback="d", scope="e", evidence=[str(evidence)]))
            evidence.write_text("edited")
            with self.assertRaises(ValueError):
                gate.advance(root, row["id"], state="VALIDATED", actor="v",
                             evidence=[str(evidence)], note="test")
            path = root / "improvements" / row["id"] / "000000.json"
            path.write_text(path.read_text().replace('"PROPOSED"', '"ADOPTED"'))
            with self.assertRaises(ValueError):
                gate.status(root, row["id"])
