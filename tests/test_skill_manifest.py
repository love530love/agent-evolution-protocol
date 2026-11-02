"""The committed skill manifest must describe the CLI that exists right now.

The failure this guards against already happened once in prose: a research doc
promised ``schema=aep-next-action-v1`` while the code emitted
``agent-evolution-next-action-v1``, and nobody noticed for two days. One
hand-written test fixed that one paragraph; generating the whole surface fixes
the class.

Two asymmetries are deliberate and are what most of these tests are about:

* Marking a command that writes as ``read-only`` is the mistake that costs
  something, so only that direction is policed. A command left unlabelled
  defaults to ``mutating``, which is merely unhelpful.
* The count of undocumented commands is a ratchet, not a target. Sixty-four of
  eighty-eight have no description anywhere near their definition; the number
  may fall freely and may only rise with a deliberate edit here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import tempfile
import unittest

from agent_evolution_protocol.cli import build_parser
from agent_evolution_protocol.coordination import skill_manifest


DOCS = Path(__file__).resolve().parents[1] / "docs" / "coordination"

#: Measured when the manifest was first generated. Lower it whenever you
#: document a command; raise it only after deciding *not* to.
UNDOCUMENTED_CEILING = 64


def live_names() -> set[str]:
    parser = build_parser()
    return set(skill_manifest._subparsers(parser).choices)


class SurfaceTests(unittest.TestCase):
    def setUp(self):
        self.manifest = skill_manifest.build()
        self.rows = {row["name"]: row for row in self.manifest["commands"]}

    def test_every_command_appears_exactly_once(self):
        self.assertEqual(self.manifest["count"], len(live_names()))
        self.assertEqual(len(self.rows), len(self.manifest["commands"]))

    def test_every_command_carries_a_declared_risk(self):
        for name, row in self.rows.items():
            self.assertIn(row["risk"], skill_manifest.RISK_ORDER, name)

    def test_the_dangerous_direction_is_closed(self):
        # A read-only label on a command that reaches another process would let
        # a caller believe a dry run happened. The opposite mistake is harmless.
        leaked = skill_manifest.NEVER_READ_ONLY & skill_manifest.READ_ONLY_COMMANDS
        self.assertEqual(leaked, set())
        for name in skill_manifest.NEVER_READ_ONLY:
            self.assertNotEqual(self.rows[name]["risk"], skill_manifest.READ_ONLY, name)

    def test_no_classification_names_a_command_that_does_not_exist(self):
        declared = (skill_manifest.READ_ONLY_COMMANDS
                    | skill_manifest.IRREVERSIBLE_COMMANDS)
        self.assertEqual(declared - live_names(), set())

    def test_an_unknown_command_is_assumed_to_mutate(self):
        self.assertEqual(skill_manifest.risk_of("coord-invented"), skill_manifest.MUTATING)

    def test_arguments_carry_what_a_caller_needs(self):
        row = self.rows["coord-route"]
        by_dest = {argument["dest"]: argument for argument in row["arguments"]}
        self.assertTrue(by_dest["observation"]["required"])
        self.assertEqual(by_dest["observation"]["kind"], "option")
        self.assertFalse(by_dest["target"]["required"])
        self.assertEqual(by_dest["target"]["default"], "composer")

    def test_positional_choices_are_recorded_sorted(self):
        row = self.rows["coord-uia"]
        action = next(argument for argument in row["arguments"]
                      if argument["kind"] == "positional")
        self.assertEqual(action["choices"], sorted(action["choices"]))

    def test_a_path_default_is_serialisable(self):
        # Path(".agent-evolution") is not JSON, and str() on a bool default would
        # read as truthy to a careless consumer, so only Paths are converted.
        by_dest = {row["dest"]: row for row in self.manifest["global_arguments"]}
        self.assertEqual(by_dest["root"]["default"], ".agent-evolution")
        json.dumps(self.manifest)

    def test_the_digest_ignores_itself(self):
        self.assertTrue(self.manifest["digest"].startswith("sha256:"))
        copy = dict(self.manifest)
        copy["digest"] = "sha256:0000"
        self.assertEqual(skill_manifest.digest(copy), self.manifest["digest"])

    def test_undocumented_count_only_falls(self):
        self.assertLessEqual(
            self.manifest["undocumented"], UNDOCUMENTED_CEILING,
            "a command was added without a docstring or help text; document it, "
            "or raise UNDOCUMENTED_CEILING after deciding not to")

    def test_documented_flag_agrees_with_the_purpose_text(self):
        for name, row in self.rows.items():
            self.assertEqual(row["documented"], bool(row.get("purpose")), name)


class CommittedCopyTests(unittest.TestCase):
    """The files in docs/ are generated; these fail when they stop matching."""

    def test_the_committed_copy_matches_the_live_cli(self):
        report = skill_manifest.check(DOCS)
        self.assertTrue(report["in_sync"], report)
        self.assertEqual(report["reasons"], [])

    def test_the_committed_skill_doc_is_byte_identical_to_the_render(self):
        recorded = (DOCS / skill_manifest.SKILL_NAME).read_text(encoding="utf-8")
        self.assertEqual(recorded, skill_manifest.render_markdown(skill_manifest.build()))

    def test_a_hand_edit_to_the_doc_is_reported_as_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            skill_manifest.write(directory)
            target = Path(directory) / skill_manifest.SKILL_NAME
            target.write_text(target.read_text(encoding="utf-8") + "\nhand edit\n",
                              encoding="utf-8")
            report = skill_manifest.check(directory)
        self.assertFalse(report["in_sync"])
        self.assertIn("SKILL_STALE", report["reasons"])


class DriftReportingTests(unittest.TestCase):
    def test_a_missing_manifest_names_every_command(self):
        with tempfile.TemporaryDirectory() as directory:
            report = skill_manifest.check(directory)
        self.assertFalse(report["in_sync"])
        self.assertIn("MANIFEST_MISSING", report["reasons"])
        self.assertEqual(set(report["missing"]), live_names())

    def test_a_stale_manifest_names_what_changed(self):
        manifest = skill_manifest.build()
        with tempfile.TemporaryDirectory() as directory:
            skill_manifest.write(directory, manifest)
            target = Path(directory) / skill_manifest.MANIFEST_NAME
            recorded = json.loads(target.read_text(encoding="utf-8"))
            recorded["commands"] = [row for row in recorded["commands"]
                                    if row["name"] != "coord-send"]
            recorded["commands"].append({"name": "coord-invented", "risk": "read-only",
                                         "arguments": [], "documented": False})
            target.write_text(json.dumps(recorded, ensure_ascii=False), encoding="utf-8")
            report = skill_manifest.check(directory, manifest)
        self.assertFalse(report["in_sync"])
        self.assertIn("DIGEST_MISMATCH", report["reasons"])
        self.assertIn("coord-send", report["missing"])
        self.assertIn("coord-invented", report["extra"])

    def test_a_manifest_edited_without_regenerating_is_caught(self):
        # Trusting the stored digest was a real hole: an edit that leaves the
        # digest alone used to compare equal to the live surface.
        manifest = skill_manifest.build()
        with tempfile.TemporaryDirectory() as directory:
            skill_manifest.write(directory, manifest)
            target = Path(directory) / skill_manifest.MANIFEST_NAME
            recorded = json.loads(target.read_text(encoding="utf-8"))
            recorded["commands"] = [row for row in recorded["commands"]
                                    if row["name"] != "coord-send"]
            target.write_text(json.dumps(recorded, ensure_ascii=False), encoding="utf-8")
            report = skill_manifest.check(directory, manifest)
        self.assertFalse(report["in_sync"])
        self.assertIn("DIGEST_NOT_SELF_CONSISTENT", report["reasons"])

    def test_an_unreadable_manifest_is_not_a_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / skill_manifest.MANIFEST_NAME
            target.write_text("{not json", encoding="utf-8")
            report = skill_manifest.check(directory)
        self.assertFalse(report["in_sync"])
        self.assertIn("MANIFEST_UNREADABLE", report["reasons"])


class DescribeArgumentTests(unittest.TestCase):
    def _describe(self, add):
        parser = argparse.ArgumentParser()
        add(parser)
        return {action.dest: skill_manifest.describe_argument(action)
                for action in parser._actions if action.dest != "help"}

    def test_a_store_true_flag_is_a_flag(self):
        rows = self._describe(lambda parser: parser.add_argument("--force", action="store_true"))
        self.assertEqual(rows["force"]["kind"], "flag")
        self.assertFalse(rows["force"]["required"])

    def test_a_required_option_is_required(self):
        rows = self._describe(lambda parser: parser.add_argument("--agent", required=True))
        self.assertEqual(rows["agent"]["kind"], "option")
        self.assertTrue(rows["agent"]["required"])

    def test_an_optional_positional_is_not_required(self):
        rows = self._describe(lambda parser: parser.add_argument("action", nargs="?"))
        self.assertEqual(rows["action"]["kind"], "positional")
        self.assertFalse(rows["action"]["required"])

    def test_a_nargs_list_is_recorded(self):
        rows = self._describe(lambda parser: parser.add_argument("--paths", nargs="*", default=[]))
        self.assertEqual(rows["paths"]["nargs"], "*")
        self.assertEqual(rows["paths"]["default"], [])


class ResearchDocTests(unittest.TestCase):
    """The absorption doc quotes counts, so the counts have to be true.

    This is the module's own thesis applied to the module's own write-up: a
    number in prose is a claim, and an unchecked claim is the thing that already
    went stale once in this repository.
    """

    DOC = DOCS / "THIRD_PARTY_CAPABILITY_ABSORPTION_20261102.zh-CN.md"

    def setUp(self):
        self.text = self.DOC.read_text(encoding="utf-8")
        self.manifest = skill_manifest.build()

    def _number(self, pattern: str, label: str) -> int:
        match = re.search(pattern, self.text)
        if match is None:
            raise AssertionError(f"the absorption doc no longer states {label}")
        return int(match.group(1))

    def test_the_documented_command_count_is_the_real_one(self):
        self.assertEqual(self._number(r"(\d+) 个子命令", "the command count"),
                         self.manifest["count"])

    def test_the_documented_docstring_count_is_the_real_one(self):
        self.assertEqual(
            self._number(r"其中 (\d+) 个在处理函数上有 docstring", "the docstring count"),
            self.manifest["documented"])

    def test_the_documented_undocumented_count_is_the_real_one(self):
        self.assertEqual(
            self._number(r"\*\*(\d+) / \d+ 个命令在定义处没有任何说明", "the undocumented count"),
            self.manifest["undocumented"])

    def test_every_claimed_new_module_exists(self):
        for name in ("skill_manifest", "action_verification", "trajectory"):
            self.assertIn(f"coordination/{name}.py", self.text)
            self.assertTrue((DOCS.parents[1] / "src" / "agent_evolution_protocol"
                             / "coordination" / f"{name}.py").is_file(), name)

    def test_every_claimed_test_file_exists(self):
        for name in ("test_skill_manifest", "test_session_continuity",
                     "test_action_verification", "test_trajectory"):
            self.assertTrue((DOCS.parents[1] / "tests" / f"{name}.py").is_file(), name)


if __name__ == "__main__":
    unittest.main()
