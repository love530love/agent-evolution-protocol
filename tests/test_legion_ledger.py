import json
from pathlib import Path
import subprocess
import shutil
import sys
import unittest

from agent_evolution_protocol.coordination import legion_ledger


class LegionLedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def record(self, **kwargs):
        defaults = dict(agent="codex", kind="cross_review", reporter="workbuddy",
                        task="T132", evidence=["logs/a.txt"], note="reviewed the failure signature")
        defaults.update(kwargs)
        return legion_ledger.record(self.root, **defaults)

    def test_scoreboard_sums_weights(self):
        self.record()
        self.record(kind="falsification", evidence=["logs/b.txt"], note="killed the hypothesis")
        self.record(agent="qoder", kind="silent_round", reporter="codex", note="no output this round")
        board = legion_ledger.scoreboard(self.root)
        scores = {row["agent"]: row["score"] for row in board["rows"]}
        self.assertEqual(scores["codex"], 3 + 4)
        self.assertEqual(scores["qoder"], -2)
        self.assertEqual(board["leader"], "codex")

    def test_evidence_required_for_heavy_kinds(self):
        with self.assertRaises(ValueError):
            self.record(kind="falsification", evidence=[])

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            self.record(kind="being_nice")

    def test_note_required(self):
        with self.assertRaises(ValueError):
            self.record(note="   ")

    def test_slacking_flags_silent_and_repeat_trap(self):
        self.record(agent="qoder", kind="repeat_mistake", reporter="codex",
                    signature="compile-timeout", evidence=["logs/c.txt"], note="same trap again")
        self.record(agent="qoder", kind="repeat_mistake", reporter="codex",
                    signature="compile-timeout", evidence=["logs/d.txt"], note="same trap third time")
        report = legion_ledger.slacking(self.root, expected=["qoder", "hermes"], window_hours=24)
        agents = {row["agent"]: row["reasons"] for row in report["findings"]}
        self.assertIn("qoder", agents)
        self.assertIn("repeat_trap:compile-timeout", agents["qoder"])
        self.assertIn("negative_net_score", agents["qoder"])
        self.assertIn("no_local_evidence_in_window", agents["hermes"])
        self.assertNotIn("codex", agents)

    def test_challenge_requires_falsifiable_fields(self):
        with self.assertRaises(ValueError):
            legion_ledger.challenge(self.root, agent="qoder", reason="silent",
                                    alternative="", predicted_signature="x",
                                    cheapest_test="y", stop_condition="z")
        card = legion_ledger.challenge(
            self.root, agent="qoder", reason="no productive output",
            alternative="take over the failing chip with a smaller tile",
            predicted_signature="tile-too-large", cheapest_test="run one tile size on one chip",
            stop_condition="one failed prediction", evidence=["logs/e.txt"], task="T132")
        self.assertEqual(card["target"], "qoder")
        self.assertTrue(Path(self.root, "legion", "challenges", card["id"] + ".json").is_file())

    def test_chase_reports_gap_to_leader(self):
        self.record(agent="codex", kind="breakthrough", evidence=["logs/f.txt"], note="new split")
        self.record(agent="qoder", kind="proposal", reporter="qoder", note="idea only")
        chase = legion_ledger.chase(self.root)
        self.assertEqual(chase["leader"], "codex")
        self.assertEqual(chase["chases"][0]["agent"], "qoder")
        self.assertEqual(chase["chases"][0]["gap"], 5)

    def test_events_are_append_only_files(self):
        first = self.record()
        second = self.record(kind="proposal", reporter="codex", note="another idea")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(list(Path(self.root, "legion", "events").glob("*.json"))), 2)


class UiaAdapterContractTest(unittest.TestCase):
    """The desktop transport must fail loudly instead of pretending to deliver."""

    def run_adapter(self, *extra):
        pwsh = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        if not pwsh:
            self.skipTest("powershell not available")
        adapter = (Path(__file__).resolve().parents[1] / "src" / "agent_evolution_protocol"
                   / "coordination" / "wake_adapters" / "uia_session.ps1")
        if not adapter.is_file():
            self.skipTest("adapter missing")
        return subprocess.run(
            [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(adapter),
             "-Agent", "ghost", "-ProcessName", "AepNoSuchClientProcess", *extra],
            text=True, capture_output=True, timeout=180, check=False)

    def test_absent_client_is_refused_not_delivered(self):
        result = self.run_adapter("-Mode", "observe")
        self.assertNotEqual(result.returncode, 0)
        combined = result.stderr + result.stdout
        self.assertTrue("LAUNCH_FORBIDDEN" in combined or "WINDOW_NOT_UNIQUE" in combined,
                        combined[:400])
        self.assertNotIn('"task_delivered":true', combined.replace(" ", ""))

    def test_deliver_refuses_to_launch_and_never_claims_delivery(self):
        result = self.run_adapter("-Mode", "deliver", "-EventId", "EVT-1",
                                  "-Text", "hello EVT-1", "-AllowClipboardPaste")
        self.assertNotEqual(result.returncode, 0)
        combined = result.stderr + result.stdout
        self.assertTrue("LAUNCH_FORBIDDEN" in combined or "WINDOW_NOT_UNIQUE" in combined,
                        combined[:400])
        self.assertNotIn('"task_delivered":true', combined.replace(" ", ""))

    def test_deliver_requires_event_id_inside_text(self):
        # Even before any window check, a message that cannot be verified is refused.
        result = self.run_adapter("-Mode", "deliver", "-EventId", "EVT-1",
                                  "-Text", "hello without the id", "-AllowLaunch")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('"task_delivered":true',
                         (result.stderr + result.stdout).replace(" ", ""))


if __name__ == "__main__":
    unittest.main()
