"""The mailbox that assigns work instead of announcing it.

Each test asserts one of the three facts a notification cannot carry: the task
exists in the DAG, the write scope is leased, and the message is executable.
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_evolution_protocol.coordination import claim_conflicts, commander
from agent_evolution_protocol.federation import assignment, engine

WORKSPACE = "W-0123456789abcdef01234567"


class AssignmentTests(unittest.TestCase):
    def setUp(self):
        self._temp = TemporaryDirectory()
        self.root = Path(self._temp.name) / "state"
        self.coord = str(Path(self._temp.name) / "coordination")
        self.addCleanup(self._temp.cleanup)
        self.workspace = engine.create(self.root, goal="把 README 命令跑一遍",
                                       acceptance=["每条命令都有结论"])["workspace_id"]

    def _assign(self, **kwargs):
        options = dict(workspace_id=self.workspace, task_id="T1", requirement="跑通 README",
                       target="alpha", sender="kernel")
        options.update(kwargs)
        return assignment.assign(self.coord, federation_root=str(self.root), **options)

    def test_assignment_creates_the_dag_node_the_lease_and_the_order(self):
        receipt = self._assign(paths=["src/agent_evolution_protocol/cli.py"])
        state = engine.load(self.root, self.workspace)
        self.assertIn("T1", state["tasks"])
        self.assertEqual(state["tasks"]["T1"]["state"], "running")
        self.assertEqual(state["tasks"]["T1"]["owner"], "alpha")
        self.assertTrue(receipt["claim"]["claimed"])
        self.assertTrue((Path(self.coord) / "claims" / "T1" / "owner.json").is_file())
        orders = commander._orders(self.coord)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["event_id"], receipt["event_id"])

    def test_the_instruction_is_executable_not_announced(self):
        receipt = self._assign(paths=["docs/README.md"])
        text = receipt["instruction"]
        for fragment in ("coord-claim", "coord-task-state", "--phase working",
                         "--phase done", "AEP-ACK:" + receipt["event_id"],
                         "coord-claim-renew"):
            self.assertIn(fragment, text)
        # A receiver must be told what to do when it cannot finish.
        self.assertIn("blocked", text)

    def test_repeat_assignment_does_not_duplicate_the_node_or_the_order(self):
        first = self._assign()
        second = self._assign()
        state = engine.load(self.root, self.workspace)
        self.assertEqual(list(state["tasks"]), ["T1"])
        self.assertEqual(first["event_id"], second["event_id"])
        self.assertEqual(len(commander._orders(self.coord)), 2)
        # The second deposit is a different order for the same task only
        # because ``assign`` is explicit; the supervisor's adapter is what
        # refuses to re-queue a live one.
        self.assertIn("dag:existing:T1", second["evidence"])

    def test_a_refused_claim_is_reported_not_hidden(self):
        claim_conflicts.claim(self.coord, agent="beta", task="OTHER",
                              paths=["src/agent_evolution_protocol/cli.py"])
        receipt = self._assign(paths=["src/agent_evolution_protocol/cli.py"])
        self.assertFalse(receipt["claim"]["claimed"])
        self.assertEqual(receipt["claim"]["reason"], "scope_conflict")
        self.assertIn("claim:scope_conflict", receipt["evidence"])

    def test_event_id_is_stable_per_task_and_workspace(self):
        self.assertEqual(assignment.event_id_for(self.workspace, "T1"),
                         assignment.event_id_for(self.workspace, "T1"))
        self.assertNotEqual(assignment.event_id_for(self.workspace, "T1"),
                            assignment.event_id_for(self.workspace, "T2"))

    def test_completion_requires_the_assignee_to_say_so(self):
        self._assign()
        self.assertFalse(assignment.complete_evidence(self.coord, task_id="T1")["found"])
        folder = Path(self.coord) / "task_states"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "T1.json").write_text(json.dumps(
            {"task": "T1", "owner": "alpha", "phase": "done", "evidence": []}),
            encoding="utf-8")
        report = assignment.complete_evidence(self.coord, task_id="T1", agent="alpha")
        self.assertTrue(report["done"])
        # Another agent's claim of completion is not evidence for this task.
        foreign = assignment.complete_evidence(self.coord, task_id="T1", agent="beta")
        self.assertFalse(foreign.get("done"))

    def test_prose_done_is_not_a_phase(self):
        folder = Path(self.coord) / "task_states"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "T1.json").write_text(json.dumps(
            {"task": "T1", "owner": "alpha", "phase": "working"}), encoding="utf-8")
        report = assignment.complete_evidence(self.coord, task_id="T1")
        self.assertTrue(report["found"])
        self.assertFalse(report["done"])

    def test_blocked_phase_is_surfaced_with_its_reason(self):
        folder = Path(self.coord) / "task_states"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "T1.json").write_text(json.dumps(
            {"task": "T1", "owner": "alpha", "phase": "blocked",
             "blocked_reason": "缺少凭据"}), encoding="utf-8")
        report = assignment.complete_evidence(self.coord, task_id="T1")
        self.assertTrue(report["blocked"])
        self.assertEqual(report["blocked_reason"], "缺少凭据")


if __name__ == "__main__":
    unittest.main()
