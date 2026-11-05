"""Acceptance gate for the one-shot legion-wide announcement.

An operator asked for an announcement about a newly joined agent.  Doing it by
hand exposed three traps that a weak agent would reliably fall into:

* `coord-chat-publish` defaults to `room=workspace`; only an explicit
  `--room agent-legion` reaches the shared all-agent timeline;
* `coord-chat-read --limit N` walks FORWARD from the agent's cursor, so it
  shows the oldest unread events, not the newest -- a fresh announcement looks
  like it was never published;
* `command_memory.announce()` has a hardcoded body and a hardcoded 7-agent
  recipient list, so it cannot carry a custom notice or reach a new agent.

This capability fixes the room and the verdict, and states the two read traps
in its receipt.  It must never become a second message format, a desktop
sender, or a resident process.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import tempfile
import unittest

from agent_evolution_protocol.coordination import conversation_journal as cj
from agent_evolution_protocol.coordination import legion_announce as la

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "agent_evolution_protocol" / "coordination" / "legion_announce.py"
TEXT = "【协作区通报】新 Agent 加入：claude；本条为只读演练文本。"
SOURCE = "workbuddyai"


def _root(with_mailboxes=True):
    holder = tempfile.TemporaryDirectory()
    root = Path(holder.name) / "coord"
    (root / "private_conversations" / "events").mkdir(parents=True)
    if with_mailboxes:
        for name in ("codex", "qoder", "claude"):
            (root / "messages" / name).mkdir(parents=True)
    return root, holder


class DryRunTests(unittest.TestCase):
    def test_default_preview_writes_nothing(self):
        root, holder = _root()
        try:
            before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
            report = la.announce(root, text=TEXT, source=SOURCE)
            after = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
            self.assertEqual(before, after)
            self.assertFalse(report["applied"])
            self.assertEqual(report["writes_performed"], 0)
            self.assertEqual(report["room"], la.LEGION_ROOM)
        finally:
            holder.cleanup()

    def test_room_is_always_the_legion_room(self):
        root, holder = _root()
        try:
            report = la.announce(root, text=TEXT, source=SOURCE, apply=True)
            self.assertEqual(report["room"], "agent-legion")
            self.assertNotEqual(report["room"], "workspace")
        finally:
            holder.cleanup()

    def test_plan_lists_mailbox_targets_from_the_workspace(self):
        root, holder = _root()
        try:
            plan = la.plan(root, text=TEXT, source=SOURCE)
            self.assertEqual(plan["mailbox_targets"], ["claude", "codex", "qoder"])
            for command in plan["mailbox_commands"]:
                self.assertIn("--mailbox-only", command)
                self.assertNotIn("--dispatch", command)
                self.assertIn("coord-send", command)
        finally:
            holder.cleanup()

    def test_plan_excludes_the_announcer_itself(self):
        """coord-send rejects sender == receiver, so the plan must not list it."""
        root, holder = _root()
        try:
            plan = la.plan(root, text=TEXT, source="qoder")
            self.assertNotIn("qoder", plan["mailbox_targets"])
            self.assertEqual(plan["mailbox_targets"], ["claude", "codex"])
            explicit = la.plan(root, text=TEXT, source="hal",
                               mailbox_targets=["hal", "claude", "codex"])
            self.assertEqual(explicit["mailbox_targets"], ["claude", "codex"])
            self.assertIn("自动排除", plan["mailbox_command_note"])
        finally:
            holder.cleanup()

    def test_empty_text_or_invalid_source_is_refused(self):
        root, holder = _root()
        try:
            for kwargs in ({"text": "   ", "source": SOURCE},
                           {"text": TEXT, "source": "Not Valid"}):
                with self.subTest(kwargs=kwargs):
                    with self.assertRaises(ValueError):
                        la.announce(root, apply=True, **kwargs)
        finally:
            holder.cleanup()


class ApplyTests(unittest.TestCase):
    def test_apply_writes_exactly_one_journal_event_with_the_right_fields(self):
        root, holder = _root()
        try:
            report = la.announce(root, text=TEXT, source=SOURCE, apply=True)
            self.assertTrue(report["applied"])
            self.assertEqual(report["writes_performed"], 1)
            events = cj.read_events(root, after="", limit=500)
            self.assertEqual(len(events), 1)
            event = events[0]
            self.assertEqual(event["id"], report["event_id"])
            self.assertEqual(event["room"], "agent-legion")
            self.assertEqual(event["actor"], SOURCE)
            self.assertEqual(event["source"], SOURCE)
            self.assertEqual(event["text"], TEXT)
            self.assertEqual(event["visibility"], "workspace")
        finally:
            holder.cleanup()

    def test_receipt_states_both_read_traps_and_the_verdict(self):
        root, holder = _root()
        try:
            report = la.announce(root, text=TEXT, source=SOURCE, apply=True)
            self.assertIn("coord-chat-read", report["read_guidance"])
            self.assertIn("游标", report["read_guidance"])
            self.assertIn("command_memory.announce", report["announce_trap"])
            self.assertEqual(report["verdict"], "DEPOSITED")
            self.assertFalse(report["delivered"])
            self.assertFalse(report["acknowledged"])
            self.assertEqual(report["mailbox_sync"], "not_performed")
        finally:
            holder.cleanup()

    def test_second_apply_is_a_distinct_event_but_never_a_silent_daemon(self):
        root, holder = _root()
        try:
            first = la.announce(root, text=TEXT, source=SOURCE, apply=True)
            second = la.announce(root, text=TEXT, source=SOURCE, apply=True)
            self.assertNotEqual(first["event_id"], second["event_id"])
            self.assertEqual(len(cj.read_events(root, after="", limit=500)), 2)
            for report in (first, second):
                self.assertEqual(report["models_loaded"], 0)
                self.assertEqual(report["processes_started"], 0)
                self.assertIs(report["ui_touched"], False)
                self.assertIs(report["deployment_performed"], False)
                self.assertIs(report["system_tasks_registered"], False)
        finally:
            holder.cleanup()


class BoundaryTests(unittest.TestCase):
    def test_module_is_socket_free_model_free_and_loop_free(self):
        tree = ast.parse(SRC.read_text(encoding="utf-8"))
        banned = {"socket", "urllib", "requests", "httpx", "subprocess", "threading",
                  "asyncio", "multiprocessing", "insight_proposer", "local_model_session"}
        scanned = 0
        for node in ast.walk(tree):
            self.assertFalse(isinstance(node, ast.While), "no resident loop")
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] + [a.name for a in node.names]
                     if isinstance(node, ast.ImportFrom) else [])
            for name in names:
                scanned += 1
                with self.subTest(name=name):
                    self.assertFalse(any(name == bad or name.startswith(bad + ".")
                                         for bad in banned))
        self.assertGreater(scanned, 0)

    def test_capability_card_and_boundary_doc_exist(self):
        card = REPO / "skills" / "aep-legion-announce" / "SKILL.md"
        doc = REPO / "docs" / "LEGION_ANNOUNCE.zh-CN.md"
        self.assertTrue(card.is_file())
        self.assertTrue(doc.is_file())
        text = card.read_text(encoding="utf-8") + doc.read_text(encoding="utf-8")
        for token in ("agent-legion", "coord-chat-read", "游标", "command_memory.announce",
                      "--mailbox-only", "DEPOSITED", "只有 `VERIFIED` 能关单"):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_trigger_script_is_ascii_and_defaults_to_dry_run(self):
        script = REPO / "scripts" / "announce-to-legion.ps1"
        self.assertTrue(script.is_file())
        raw = script.read_bytes().decode("ascii")
        self.assertIn("if ($Apply)", raw)
        self.assertNotIn("$Apply = $true", raw)
        self.assertIn("legion_announce", raw)

    def test_no_new_coord_command_surface(self):
        """The module entry point must not add another coord-* CLI command."""
        tree = ast.parse(SRC.read_text(encoding="utf-8"))
        text = SRC.read_text(encoding="utf-8")
        self.assertNotIn("build_parser", text)
        self.assertNotIn("add_parser", text)
        self.assertFalse(any(isinstance(n, ast.While) for n in ast.walk(tree)))


if __name__ == "__main__":
    unittest.main()
