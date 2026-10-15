import json
from datetime import datetime, timedelta, timezone
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

    def test_incentives_require_independent_review_and_real_cited_bytes(self):
        proof = self.root / "logs" / "a.txt"
        proof.parent.mkdir()
        proof.write_text("official return", encoding="utf-8")
        event = self.record()
        self.assertEqual(legion_ledger.incentive_board(self.root)["rows"][0]["rp"], 0)
        with self.assertRaises(ValueError):
            legion_ledger.adjudicate(self.root, event_id=event["id"], reviewer="codex",
                                     verdict="accepted", evidence_files=["logs/a.txt"], note="same agent")
        with self.assertRaises(ValueError):
            legion_ledger.adjudicate(self.root, event_id=event["id"], reviewer="workbuddy",
                                     verdict="accepted", evidence_files=["logs/a.txt"], note="same reporter")
        with self.assertRaises(ValueError):
            legion_ledger.adjudicate(self.root, event_id=event["id"], reviewer="qoder",
                                     verdict="accepted", evidence_files=["missing.txt"], note="not present")
        review = legion_ledger.adjudicate(self.root, event_id=event["id"], reviewer="qoder",
                                           verdict="accepted", evidence_files=["logs/a.txt"], note="independent check")
        self.assertEqual(len(review["evidence"][0]["sha256"]), 64)
        row = legion_ledger.incentive_board(self.root)["rows"][0]
        self.assertEqual((row["rp"], row["reputation"], row["accepted"]), (3, 51, 1))
        with self.assertRaises(FileExistsError):
            legion_ledger.adjudicate(self.root, event_id=event["id"], reviewer="hermes",
                                     verdict="accepted", evidence_files=["logs/a.txt"], note="cannot double settle")
        proof.write_text("changed", encoding="utf-8")
        row = legion_ledger.incentive_board(self.root)["rows"][0]
        self.assertEqual((row["rp"], row["invalidated"]), (0, 1))

    def test_rejected_and_duplicate_evidence_do_not_mint_credit(self):
        proof = self.root / "logs" / "a.txt"
        proof.parent.mkdir()
        proof.write_text("same official return", encoding="utf-8")
        first = self.record()
        second = self.record(kind="falsification", note="same data different claim")
        third_proof = self.root / "logs" / "b.txt"
        third_proof.write_text("new official return", encoding="utf-8")
        third = self.record(kind="breakthrough", evidence=["logs/b.txt"], note="unproven breakthrough")
        for event in (first, second):
            legion_ledger.adjudicate(self.root, event_id=event["id"], reviewer="qoder",
                                      verdict="accepted", evidence_files=["logs/a.txt"], note="checked")
        legion_ledger.adjudicate(self.root, event_id=third["id"], reviewer="qoder",
                                  verdict="rejected", evidence_files=["logs/b.txt"], note="did not reproduce")
        row = legion_ledger.incentive_board(self.root)["rows"][0]
        self.assertEqual((row["rp"], row["ap"], row["accepted"], row["duplicate"], row["rejected"]),
                         (3, 0, 1, 1, 1))

    def test_adjudication_refuses_unclaimed_or_outside_evidence(self):
        event = self.record()
        uncited = self.root / "logs" / "uncited.txt"
        uncited.parent.mkdir()
        uncited.write_text("proof", encoding="utf-8")
        with self.assertRaises(ValueError):
            legion_ledger.adjudicate(self.root, event_id=event["id"], reviewer="qoder",
                                     verdict="accepted", evidence_files=["logs/uncited.txt"], note="not claimed")
        with self.assertRaises(ValueError):
            legion_ledger.adjudicate(self.root, event_id=event["id"], reviewer="qoder",
                                     verdict="accepted", evidence_files=["../escape.txt"], note="outside")

    def test_reputation_uses_lifetime_history_while_credits_use_window(self):
        logs = self.root / "logs"
        logs.mkdir()
        (logs / "a.txt").write_text("older evidence", encoding="utf-8")
        (logs / "b.txt").write_text("new evidence", encoding="utf-8")
        old = self.record()
        event_file = self.root / "legion" / "events" / f"{old['id']}.json"
        historical = json.loads(event_file.read_text(encoding="utf-8"))
        historical["at"] = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        event_file.write_text(json.dumps(historical), encoding="utf-8")
        legion_ledger.adjudicate(self.root, event_id=old["id"], reviewer="qoder",
                                  verdict="accepted", evidence_files=["logs/a.txt"], note="older independent check")
        recent = self.record(kind="breakthrough", evidence=["logs/b.txt"], note="new result")
        legion_ledger.adjudicate(self.root, event_id=recent["id"], reviewer="qoder",
                                  verdict="accepted", evidence_files=["logs/b.txt"], note="new independent check")
        lifetime = legion_ledger.incentive_board(self.root)["rows"][0]
        window = legion_ledger.incentive_board(self.root, since_hours=24)["rows"][0]
        self.assertEqual((lifetime["rp"], lifetime["ap"], lifetime["reputation"]), (3, 6, 52))
        self.assertEqual((window["rp"], window["ap"], window["reputation"]), (0, 6, 52))
        self.assertEqual((window["accepted"], window["pending"]), (1, 0))


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
