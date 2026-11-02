"""A trajectory binds a command to its result, and replaying it does not re-fire.

Two properties are worth testing and nothing else here matters much: the binding
must be re-derivable (a stored digest nobody recomputes is decoration), and the
replay must refuse to reproduce a write. The second one is the difference
between an audit artefact and a macro that sends messages.
"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent_evolution_protocol.coordination import skill_manifest
from agent_evolution_protocol.coordination import trajectory


KNOWN = {row["name"] for row in skill_manifest.build()["commands"]}


class TrajectoryCase(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def write(self, name: str, payload) -> str:
        target = self.root / name
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return str(target)

    def record(self, **kwargs):
        kwargs.setdefault("trajectory_id", "run")
        kwargs.setdefault("known", KNOWN)
        return trajectory.record(self.root, **kwargs)


class DigestTests(unittest.TestCase):
    def test_the_digest_ignores_key_order(self):
        self.assertEqual(trajectory.digest_of({"a": 1, "b": 2}),
                         trajectory.digest_of({"b": 2, "a": 1}))

    def test_a_different_value_is_a_different_digest(self):
        self.assertNotEqual(trajectory.digest_of({"a": 1}), trajectory.digest_of({"a": 2}))


class IdentifierTests(unittest.TestCase):
    def test_a_plain_id_is_accepted(self):
        self.assertEqual(trajectory.valid_id("20261102T1453-c0272241"),
                         "20261102T1453-c0272241")

    def test_a_path_separator_is_refused(self):
        for bad in ("a/b", "a\\b", "..", ""):
            with self.assertRaises(ValueError, msg=bad):
                trajectory.valid_id(bad)

    def test_a_windows_device_name_is_refused(self):
        # CON.json is still the console device on Windows, and the failure then
        # looks like an empty trajectory rather than a bad identifier.
        with self.assertRaises(ValueError):
            trajectory.valid_id("CON")


class CommandResolutionTests(unittest.TestCase):
    def test_the_command_is_found_after_a_global_flag_and_its_value(self):
        argv = ["--coord-root", r"K:\some\path", "coord-status"]
        self.assertEqual(trajectory.command_of(argv, known=KNOWN), "coord-status")

    def test_a_value_that_looks_like_a_command_is_not_mistaken_for_one(self):
        argv = ["--coord-root", "coord-status", "coord-claim"]
        self.assertEqual(trajectory.command_of(argv, known=KNOWN), "coord-status")

    def test_an_unknown_argv_resolves_to_nothing(self):
        self.assertEqual(trajectory.command_of(["aep", "--help"], known=KNOWN), "")


class RecordingTests(TrajectoryCase):
    def test_reopening_does_not_wipe_the_steps(self):
        trajectory.start(self.root, trajectory_id="run", agent="wb")
        self.record(argv=["coord-status"])
        again = trajectory.start(self.root, trajectory_id="run", agent="ignored")
        self.assertEqual(len(again["steps"]), 1)
        self.assertEqual(again["agent"], "wb")

    def test_steps_are_numbered_and_carry_the_declared_risk(self):
        first = self.record(argv=["coord-status"])
        second = self.record(argv=["coord-commander", "--target", "x"])
        self.assertEqual((first["index"], second["index"]), (0, 1))
        self.assertEqual(first["risk"], skill_manifest.READ_ONLY)
        self.assertEqual(second["risk"], skill_manifest.IRREVERSIBLE)

    def test_an_unrecognised_command_is_recorded_as_mutating(self):
        step = self.record(argv=["not-a-command"])
        self.assertEqual(step["command"], "")
        self.assertEqual(step["risk"], skill_manifest.MUTATING)

    def test_an_inlined_result_is_digested_and_excerpted(self):
        payload = {"schema": "x", "blob": "y" * 1000}
        step = self.record(argv=["coord-status"], result=payload)
        self.assertEqual(step["result_digest"], trajectory.digest_of(payload))
        self.assertLessEqual(len(step["result_excerpt"]), trajectory.EXCERPT_CHARS)

    def test_a_referenced_result_is_digested_from_the_file(self):
        reference = self.write("receipt.json", {"state": "DELIVERED"})
        step = self.record(argv=["coord-status"], result_ref=reference)
        self.assertEqual(step["result_digest"],
                         trajectory.digest_of({"state": "DELIVERED"}))

    def test_an_unreadable_reference_still_records_the_step(self):
        # A trajectory written here may be read where the receipt does not
        # exist; that is a reportable state, not a crash.
        step = self.record(argv=["coord-status"], result_ref=str(self.root / "absent.json"))
        self.assertEqual(step["result_digest"], "")

    def test_whitespace_does_not_change_a_referenced_result(self):
        # An inlined result and a referenced one must agree, or the binding
        # depends on how the file happened to be written.
        tight = self.root / "tight.json"
        loose = self.root / "loose.json"
        tight.write_text('{"state":"DELIVERED"}', encoding="utf-8")
        loose.write_text('{\n  "state": "DELIVERED"\n}\n', encoding="utf-8")
        first = self.record(argv=["coord-status"], result_ref=str(tight))
        second = self.record(argv=["coord-status"], result_ref=str(loose))
        self.assertEqual(first["result_digest"], second["result_digest"])
        self.assertEqual(first["result_digest"], trajectory.digest_of({"state": "DELIVERED"}))

    def test_a_reference_that_is_not_json_is_still_bound(self):
        plain = self.root / "log.txt"
        plain.write_text("DELIVERED 42", encoding="utf-8")
        step = self.record(argv=["coord-status"], result_ref=str(plain))
        self.assertEqual(step["result_digest"], trajectory.digest_of("DELIVERED 42"))


class VerifyTests(TrajectoryCase):
    def test_a_clean_trajectory_verifies(self):
        reference = self.write("receipt.json", {"state": "DELIVERED"})
        self.record(argv=["coord-status"], result={"ok": True})
        self.record(argv=["coord-status"], result_ref=reference)
        report = trajectory.verify(trajectory.load(self.root, "run"))
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["steps"], 2)

    def test_an_edited_argv_is_caught(self):
        self.record(argv=["coord-status"])
        loaded = trajectory.load(self.root, "run")
        loaded["steps"][0]["argv"] = ["coord-commander", "--target", "someone"]
        report = trajectory.verify(loaded)
        self.assertFalse(report["ok"])
        self.assertEqual(report["tampered"], [0])

    def test_a_changed_result_file_is_caught(self):
        reference = self.write("receipt.json", {"state": "DELIVERED"})
        self.record(argv=["coord-status"], result_ref=reference)
        Path(reference).write_text(json.dumps({"state": "DEFERRED"}), encoding="utf-8")
        report = trajectory.verify(trajectory.load(self.root, "run"))
        self.assertFalse(report["ok"])
        self.assertEqual(report["changed"], [0])

    def test_a_deleted_result_file_is_reported_as_missing_not_changed(self):
        reference = self.write("receipt.json", {"state": "DELIVERED"})
        self.record(argv=["coord-status"], result_ref=reference)
        Path(reference).unlink()
        report = trajectory.verify(trajectory.load(self.root, "run"))
        self.assertEqual(report["missing"], [0])
        self.assertEqual(report["changed"], [])

    def test_junk_is_reported_rather_than_raising(self):
        self.assertEqual(trajectory.verify({"steps": "nope"})["reason"], "NOT_A_TRAJECTORY")


class ReplayTests(TrajectoryCase):
    def test_only_reads_are_replayed(self):
        self.record(argv=["coord-status"])
        self.record(argv=["coord-commander", "--target", "wb"])
        self.record(argv=["coord-send", "--agent", "wb"])
        plan = trajectory.replay_plan(trajectory.load(self.root, "run"))
        self.assertEqual([step["command"] for step in plan["replayable"]], ["coord-status"])
        self.assertEqual([step["command"] for step in plan["refused"]],
                         ["coord-commander", "coord-send"])
        for refused in plan["refused"]:
            self.assertEqual(refused["reason"], "NOT_READ_ONLY")

    def test_a_refusal_names_the_risk_so_the_gap_is_accounted_for(self):
        self.record(argv=["federate", "serve"])
        plan = trajectory.replay_plan(trajectory.load(self.root, "run"))
        self.assertEqual(plan["refused"][0]["risk"], skill_manifest.IRREVERSIBLE)

    def test_an_empty_argv_is_refused_even_when_the_command_is_a_read(self):
        self.record(argv=[])
        plan = trajectory.replay_plan(trajectory.load(self.root, "run"))
        self.assertEqual(plan["replayable"], [])
        self.assertEqual(plan["refused"][0]["reason"], "NO_ARGV")

    def test_junk_is_reported_rather_than_raising(self):
        self.assertEqual(trajectory.replay_plan({"steps": 1})["reason"], "NOT_A_TRAJECTORY")


class LoadingTests(TrajectoryCase):
    def test_an_absent_trajectory_is_none(self):
        self.assertIsNone(trajectory.load(self.root, "absent"))

    def test_a_corrupt_file_is_none_rather_than_a_crash(self):
        target = trajectory.path(self.root, "broken")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{not json", encoding="utf-8")
        self.assertIsNone(trajectory.load(self.root, "broken"))

    def test_a_file_without_steps_is_not_a_trajectory(self):
        target = trajectory.path(self.root, "shallow")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"trajectory_id": "shallow"}), encoding="utf-8")
        self.assertIsNone(trajectory.load(self.root, "shallow"))


if __name__ == "__main__":
    unittest.main()
