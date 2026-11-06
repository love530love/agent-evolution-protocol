"""Acceptance gate: expose compose on the CLI and add an INDEPENDENT UIA
transcript verdict beside (not instead of) the four OCR send-report checks.

Measured 2026-11-06 on ChatGPT: a compose receipt said task_delivered=false
with every OCR band check failing, while a read-only UIA capture contained the
event id and the agent had already begun a read-only audit. An OCR band split
on a multi-monitor window produced a false negative. The fix is a second,
recomputable source whose disagreements are reported, not a relaxed contract.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from agent_evolution_protocol import cli
from agent_evolution_protocol.coordination import action_verification as av
from agent_evolution_protocol.coordination import delivery_preflight as dp
from agent_evolution_protocol.coordination import uia_transcript_verdict as utv

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "agent_evolution_protocol" / "coordination"

EVENT = "20261106T065238959490Z-9fd49a50"
HINT = "和 Codex 一起工作"


def _transcript(*, event_in_conversation=True, event_in_composer=False,
                probe_in_conversation=True, hint_lines=2):
    """Shape of the real 2026-11-06 UIA capture (clean uia-tree text)."""
    rows = ["历史会话", "请查看队友通报并讨论"]
    if event_in_conversation:
        rows.append(f"【协作区讨论邀请】事件ID：{EVENT}")
    if probe_in_conversation:
        rows.append("这条消息是真实桌面投递，不是信箱落盘。请回表讨论。")
    rows.append("ChatGPT 说：我会按事件 ID 做一次只读审计")
    rows.append("最新一条回复")
    for _ in range(hint_lines):
        rows.append(f"{HINT} | {HINT}")
    if event_in_composer:
        rows.append(f"草稿里还留着 {EVENT}")
    rows.extend(["完全访问 | 完全访问", "暂无聊天 | 暂无聊天"])
    return "\n".join(rows)


class TranscriptVerdictTests(unittest.TestCase):
    def test_event_id_in_conversation_and_not_in_composer_verifies(self):
        result = utv.evaluate(transcript=_transcript(), event_id=EVENT,
                              probe="请回表讨论", composer_hint=HINT)
        self.assertIs(result["verified"], True)
        self.assertEqual(result["reason"], "")
        self.assertEqual(result["boundary"]["method"], "last-placeholder-line")
        self.assertGreater(result["boundary"]["line_index"], 0)
        self.assertTrue(result["evidence"]["in_conversation"])
        self.assertFalse(result["evidence"]["in_composer"])

    def test_event_id_only_in_composer_is_refused(self):
        result = utv.evaluate(
            transcript=_transcript(event_in_conversation=False, event_in_composer=True),
            event_id=EVENT, composer_hint=HINT)
        self.assertIs(result["verified"], False)
        self.assertEqual(result["reason"], "STILL_IN_COMPOSER")

    def test_missing_event_id_is_refused(self):
        result = utv.evaluate(
            transcript=_transcript(event_in_conversation=False),
            event_id=EVENT, composer_hint=HINT)
        self.assertIs(result["verified"], False)
        self.assertEqual(result["reason"], "EVENT_ID_NOT_IN_CONVERSATION")

    def test_probe_missing_from_conversation_is_refused(self):
        result = utv.evaluate(
            transcript=_transcript(probe_in_conversation=False),
            event_id=EVENT, probe="请回表讨论", composer_hint=HINT)
        self.assertIs(result["verified"], False)
        self.assertEqual(result["reason"], "PROBE_NOT_IN_CONVERSATION")

    def test_without_a_composer_hint_the_boundary_is_unknown_not_guessed(self):
        result = utv.evaluate(transcript=_transcript(), event_id=EVENT,
                              composer_hint="")
        self.assertIsNone(result["verified"])
        self.assertEqual(result["reason"], "NO_COMPOSER_BOUNDARY")
        self.assertEqual(result["boundary"]["method"], "absent")

    def test_empty_transcript_is_unknown(self):
        result = utv.evaluate(transcript="", event_id=EVENT, composer_hint=HINT)
        self.assertIsNone(result["verified"])
        self.assertEqual(result["reason"], "NO_TRANSCRIPT")

    def test_empty_event_id_is_unknown(self):
        result = utv.evaluate(transcript=_transcript(), event_id="",
                              composer_hint=HINT)
        self.assertIsNone(result["verified"])
        self.assertEqual(result["reason"], "NO_EVENT_ID")

    def test_last_placeholder_line_is_the_boundary(self):
        text = _transcript(hint_lines=3)
        result = utv.evaluate(transcript=text, event_id=EVENT, composer_hint=HINT)
        lines = text.splitlines()
        k = result["boundary"]["line_index"]
        self.assertIn(HINT, lines[k])
        self.assertTrue(all(HINT not in line for line in lines[k + 1:]))

    def test_checks_use_the_existing_action_verification_shape(self):
        result = utv.evaluate(transcript=_transcript(), event_id=EVENT,
                              probe="请回表讨论", composer_hint=HINT)
        self.assertTrue(result["checks"])
        for check in result["checks"]:
            self.assertIn(check["outcome"], (av.PASS, av.FAIL, av.UNKNOWN))
            self.assertIn("evidence", check)
        self.assertTrue(all(c["outcome"] == av.PASS for c in result["checks"]))

    def test_substituted_ocr_characters_tolerate_by_giving_up_chars(self):
        noisy = _transcript().replace("请回表讨论", "请回表讨谕")
        result = utv.evaluate(transcript=noisy, event_id=EVENT,
                              probe="请回表讨论", composer_hint=HINT)
        self.assertIs(result["verified"], True)
        self.assertNotEqual(result["evidence"]["probe_fragment"], "请回表讨论")

    def test_no_network_process_or_model_imports(self):
        tree = ast.parse((SRC / "uia_transcript_verdict.py").read_text(encoding="utf-8"))
        banned = {"socket", "urllib", "requests", "httpx", "subprocess",
                  "threading", "asyncio", "insight_proposer", "local_model_session"}
        seen: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                seen.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                seen.update(alias.name for alias in node.names)
                if node.module:
                    seen.add(node.module.split(".")[0])
        self.assertFalse(seen & banned, f"forbidden imports: {seen & banned}")


class PostflightIntegrationTests(unittest.TestCase):
    """The UIA source is BESIDE the four OCR checks, never a replacement."""

    RECEIPT = {
        "task_delivered": False,
        "probe_in_composer_before_send": False,
        "probe_in_transcript": False,
        "probe_in_composer_after": False,
        "placeholder_gone": False,
        "composer_cleared": True,
    }

    def test_primary_verdict_stays_false_when_ocr_checks_fail(self):
        result = dp.postflight(dict(self.RECEIPT),
                               transcript=_transcript(), event_id=EVENT,
                               probe="请回表讨论", composer_hint=HINT)
        self.assertIs(result["verified"], False,
                      "a second source must never relax the existing contract")
        self.assertTrue(result["advisory_only"])
        self.assertIs(result["declared_task_delivered"], False)
        self.assertEqual(result["reason"], "PROBE_IN_COMPOSER_BEFORE_SEND_DISAGREES")
        self.assertIs(result["transcript_verdict"]["verified"], True)

    def test_conflict_is_named_when_the_two_sources_disagree(self):
        result = dp.postflight(dict(self.RECEIPT),
                               transcript=_transcript(), event_id=EVENT,
                               composer_hint=HINT)
        self.assertIs(result["sources_in_conflict"], True)
        self.assertEqual(result["conflict_note"], "OCR_BAND_FALSE_NEGATIVE_SUSPECTED")

    def test_no_conflict_when_both_sources_agree(self):
        clean = {k: (not v if isinstance(v, bool) else v)
                 for k, v in self.RECEIPT.items()}
        clean["task_delivered"] = True
        clean["probe_in_composer_after"] = False
        result = dp.postflight(clean, transcript=_transcript(), event_id=EVENT,
                               composer_hint=HINT)
        self.assertIs(result["sources_in_conflict"], False)
        self.assertIs(result["verified"], True)

    def test_without_transcript_the_second_source_is_absent(self):
        result = dp.postflight(dict(self.RECEIPT))
        self.assertIsNone(result["transcript_verdict"])
        self.assertIs(result["sources_in_conflict"], False)

    def test_run_threads_the_transcript_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ui_profiles").mkdir()
            (root / "ui_profiles" / "chatgpt.json").write_text(
                json.dumps({"agent": "chatgpt", "transport": "uia"}),
                encoding="utf-8")
            report = dp.run(root, agents=["chatgpt"], send_receipt=dict(self.RECEIPT),
                            transcript=_transcript(), event_id=EVENT,
                            composer_hint=HINT)
        self.assertIs(report["postflight"]["transcript_verdict"]["verified"], True)
        self.assertIs(report["ui_input_performed"], False)
        self.assertIs(report["deployment_performed"], False)

    def test_cli_accepts_transcript_file_and_prints_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt = root / "receipt.json"
            transcript = root / "transcript.txt"
            receipt.write_text(json.dumps(self.RECEIPT), encoding="utf-8")
            transcript.write_text(_transcript(), encoding="utf-8")
            self.assertEqual(
                dp.main(["--coord-root", str(root), "--agent", "nonexistent",
                         "--send-receipt", str(receipt),
                         "--transcript-file", str(transcript),
                         "--event-id", EVENT, "--composer-hint", HINT]), 0)


class ComposeCommandSurfaceTests(unittest.TestCase):
    def test_coord_uia_action_choices_include_compose(self):
        parser = cli.build_parser()
        parsed = parser.parse_args(["--coord-root", "coordination", "coord-uia",
                                   "compose", "--agent", "chatgpt"])
        self.assertEqual(parsed.action, "compose")

    def test_compose_is_a_writing_action_requiring_a_carried_event_id(self):
        self.assertIn("compose", cli.WRITING_UIA_ACTIONS)
        self.assertIn("deliver", cli.WRITING_UIA_ACTIONS)
        self.assertNotIn("observe", cli.WRITING_UIA_ACTIONS)
        self.assertNotIn("capture", cli.WRITING_UIA_ACTIONS)

    def test_compose_without_event_id_is_refused_before_any_adapter_runs(self):
        args = argparse_stub(action="compose", event_id=None, text="hello")
        with mock.patch.object(cli, "uia_argv") as runner:
            with self.assertRaises(cli.ProtocolError):
                cli.coord_uia(args)
        runner.run.assert_not_called()

    def test_compose_rejects_text_that_does_not_carry_its_event_id(self):
        args = argparse_stub(action="compose", event_id=EVENT,
                             text="this text lacks the id")
        with mock.patch.object(cli, "uia_argv") as runner:
            with self.assertRaises(cli.ProtocolError):
                cli.coord_uia(args)
        runner.run.assert_not_called()


def argparse_stub(**overrides):
    import argparse
    base = dict(coord_root=Path("coordination"), agent="chatgpt",
                process=None, exe=None, title=None, text="", text_file=None,
                out_file=str(Path(tempfile.gettempdir()) / "aep-test-capture.txt"),
                event_id=None, allow_unknown_path=False,
                allow_clipboard_paste=False, ocr=False, timeout=20,
                urgent_interject=False, no_send=False)
    base.update(overrides)
    return argparse.Namespace(**base)


class BoundaryDocTests(unittest.TestCase):
    def test_capability_card_and_doc_state_the_second_source(self):
        card = (ROOT / "skills" / "aep-delivery-preflight" / "SKILL.md").read_text(
            encoding="utf-8")
        doc = (ROOT / "docs" / "DESKTOP_DELIVERY_PREFLIGHT.zh-CN.md").read_text(
            encoding="utf-8")
        for token in ("coord-uia compose", "transcript_verdict",
                      "sources_in_conflict", "并列", "不放宽"):
            with self.subTest(token=token):
                self.assertIn(token, card)
        for token in ("2026-11-06", "假阴性", "第二判定源", "并列"):
            with self.subTest(token=token):
                self.assertIn(token, doc)

    def test_no_new_coord_command_was_added(self):
        manifest = json.loads(
            (ROOT / "docs" / "coordination" / "skill-manifest.json").read_text(
                encoding="utf-8"))
        names = {row["name"] for row in manifest["commands"]} \
            if isinstance(manifest.get("commands"), list) else set()
        self.assertNotIn("coord-uia-transcript", names)


if __name__ == "__main__":
    unittest.main()
