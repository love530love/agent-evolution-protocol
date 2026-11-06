"""Drift guard for the weak-agent capability cards.

A card is only useful if a weak agent can follow it literally: every command
must really exist, every referenced document must really be there, and the
Qoder interject sequence must not silently regress to the outdated conclusion.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

REPO = Path(__file__).resolve().parents[1]
SKILLS = REPO / "skills"
SECTIONS = ("## 何时用", "## 前置条件", "## 步骤", "## 回执判定表",
            "## 失败处置", "## 验收门")
CAPABILITIES = {"file-only", "cli", "desktop-ui"}


def _cards() -> list[Path]:
    return sorted(SKILLS.glob("*/SKILL.md"))


def _frontmatter(text: str) -> dict:
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not match:
        return {}
    result = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip()
    return result


def _manifest_names() -> set[str]:
    path = REPO / "docs" / "coordination" / "skill-manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {row["name"] for row in data["commands"]}


def _manifest_global_flags() -> set[str]:
    path = REPO / "docs" / "coordination" / "skill-manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {flag for row in data["global_arguments"] for flag in row["flags"]}


class CapabilityCardTests(unittest.TestCase):
    def test_scanner_sees_cards_and_the_spec(self):
        # A broken scanner and a directory with no cards must not look identical.
        self.assertGreaterEqual(len(_cards()), 3)
        self.assertTrue((SKILLS / "README.md").is_file())
        self.assertIn("不新增标准", (SKILLS / "README.md").read_text(encoding="utf-8"))

    def test_every_card_has_identity_frontmatter(self):
        for card in _cards():
            with self.subTest(card=card.parent.name):
                meta = _frontmatter(card.read_text(encoding="utf-8"))
                self.assertTrue(meta.get("name"), "name required")
                self.assertEqual(meta["name"], card.parent.name)
                self.assertTrue(meta.get("description"), "description required")

    def test_strict_cards_follow_the_six_section_contract(self):
        strict = [c for c in _cards()
                  if "min_agent_capability" in _frontmatter(c.read_text(encoding="utf-8"))]
        self.assertGreaterEqual(len(strict), 2, "strict cards must be auto-discovered")
        for card in strict:
            with self.subTest(card=card.parent.name):
                text = card.read_text(encoding="utf-8")
                meta = _frontmatter(text)
                self.assertIn(meta["min_agent_capability"], CAPABILITIES)
                self.assertEqual(meta.get("model_required"), "false",
                                 "cards must not require model capability")
                positions = [text.find(section) for section in SECTIONS]
                self.assertTrue(all(p >= 0 for p in positions),
                                f"missing sections: {[s for s, p in zip(SECTIONS, positions) if p < 0]}")
                self.assertEqual(positions, sorted(positions), "sections must be in fixed order")
                self.assertIn("|", text, "a receipt->verdict table is required")
                self.assertNotIn("综合判断", text)
                self.assertNotIn("酌情处理", text)

    def test_all_coord_commands_exist_in_the_generated_manifest(self):
        known = _manifest_names()
        self.assertGreater(len(known), 50, "manifest must have actually loaded")
        scanned = 0
        for card in _cards():
            text = card.read_text(encoding="utf-8")
            # (?<!-) excludes the global flag --coord-root: it is an option,
            # not a command. It is checked separately below instead of being
            # silently ignored.
            for name in set(re.findall(r"(?<!-)\b(coord-[a-z0-9-]+)\b", text)):
                scanned += 1
                with self.subTest(card=card.parent.name, command=name):
                    self.assertIn(name, known)
        self.assertGreater(scanned, 0, "no commands found: guard would be vacuous")

    def test_global_flags_used_in_cards_are_declared(self):
        known = _manifest_global_flags()
        self.assertEqual({"--root", "--coord-root", "--kernel-root"}, known,
                         "the generated global argument table changed")
        scanned = 0
        for card in _cards():
            text = card.read_text(encoding="utf-8")
            for flag in set(re.findall(r"(?<!\w)(--(?:coord|kernel|root)[a-z0-9-]*)", text)):
                scanned += 1
                with self.subTest(card=card.parent.name, flag=flag):
                    self.assertIn(flag, known)
        self.assertGreater(scanned, 0, "no global flags found: guard would be vacuous")

    def test_all_referenced_repository_documents_exist(self):
        pattern = re.compile(r"`((?:docs|scripts|schemas)/[^`\s]+?)`")
        scanned = 0
        for card in list(_cards()) + [SKILLS / "README.md"]:
            for relative in set(pattern.findall(card.read_text(encoding="utf-8"))):
                if any(ch in relative for ch in "<>*"):
                    continue
                scanned += 1
                with self.subTest(card=card.name, path=relative):
                    self.assertTrue((REPO / relative).is_file(), relative)
        self.assertGreater(scanned, 3, "no references found: guard would be vacuous")

    def test_qoder_interject_sequence_did_not_regress(self):
        card = (SKILLS / "aep-desktop-wake" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("interject_after_send", card)
        self.assertIn("强制回车", card)
        self.assertIn("interject_promoted", card)
        self.assertIn("interruption", card)
        spec = (REPO / "docs" / "DESKTOP_SEND_AND_URGENT_INTERJECT.zh-CN.md").read_text(
            encoding="utf-8")
        self.assertIn("interject_after_send", spec,
                      "the send/interject spec regressed to the pre-0.6.16 conclusion")
        self.assertIn("看不到插话控件不等于没有插话能力", spec)

    def test_insight_card_states_the_deployment_boundary(self):
        card = (SKILLS / "aep-insight-loop" / "SKILL.md").read_text(encoding="utf-8")
        for token in ("deployment_performed", "scanned", "PROPOSED", "unavailable:", "--apply"):
            with self.subTest(token=token):
                self.assertIn(token, card)
        self.assertIn("不要去启动", card)

    def test_file_only_cards_really_offer_a_file_path(self):
        checked = 0
        for card in _cards():
            meta = _frontmatter(card.read_text(encoding="utf-8"))
            if meta.get("min_agent_capability") != "file-only":
                continue
            checked += 1
            with self.subTest(card=card.parent.name):
                self.assertIn("coordination/delegation/inbox/<id>.json",
                              card.read_text(encoding="utf-8"),
                              "a file-only card must be followable without a CLI")
        self.assertGreaterEqual(checked, 2, "both file-only cards must be discovered")

    def test_start_dispatch_card_covers_join_and_the_verdict_gate(self):
        card = SKILLS / "aep-start-dispatch" / "SKILL.md"
        self.assertTrue(card.is_file(), "new agents need an entry card")
        text = card.read_text(encoding="utf-8")
        self.assertEqual(_frontmatter(text)["min_agent_capability"], "file-only")
        for token in ("join --agent", "coord-claim", "coord-delivery-status",
                      "WAKE_QUEUED_UNVERIFIED", "BLOCKED_HANDOFF_REQUIRED",
                      "只有 `VERIFIED` 能关单", "MESSAGE_VISIBLE",
                      "不得重放写操作", "coordination/delegation/inbox/<id>.json"):
            with self.subTest(token=token):
                self.assertIn(token, text)
        self.assertIn("docs/NOVICE_LONGRUN_WORKFLOW.zh-CN.md", text,
                      "do not let a weak agent promise unattended multi-day runs")
        self.assertIn("不校验成员", text, "the AGENTS-name trap must be stated")

    def test_boundary_document_states_the_hard_limits(self):
        path = REPO / "docs" / "AUTONOMOUS_INSIGHT_LOOP.zh-CN.md"
        self.assertTrue(path.is_file(), "the boundary document must ship with the capability")
        text = path.read_text(encoding="utf-8")
        for token in ("deployment_performed", "不会自动改源码", "随用随走",
                      "没有 LM Studio 端到端真实推理实测", "没有常驻 watcher"):
            with self.subTest(token=token):
                self.assertIn(token, text, "a stated limit must not silently disappear")

    def test_acceptance_gate_points_at_real_test_files(self):
        pattern = re.compile(r"\b(tests/test_[a-z0-9_]+\.py)\b")
        scanned = 0
        for card in _cards():
            for relative in set(pattern.findall(card.read_text(encoding="utf-8"))):
                scanned += 1
                with self.subTest(card=card.parent.name, path=relative):
                    self.assertTrue((REPO / relative).is_file(), relative)
        self.assertGreater(scanned, 0, "no acceptance gate found: guard would be vacuous")

    def test_mission_pulse_card_states_the_no_daemon_boundary(self):
        card = SKILLS / "aep-mission-pulse" / "SKILL.md"
        self.assertTrue(card.is_file(), "long-running missions need a pulse card")
        text = card.read_text(encoding="utf-8")
        self.assertEqual(_frontmatter(text)["min_agent_capability"], "cli")
        for token in ("零模型", "--apply", "DEPOSITED", "只有 `VERIFIED` 能关单",
                      "定时器不是判断", "scripts/register-mission-pulse-task.ps1",
                      "docs/NOVICE_LONGRUN_WORKFLOW.zh-CN.md", "ALREADY_QUEUED"):
            with self.subTest(token=token):
                self.assertIn(token, text)
        self.assertIn("禁止启动常驻 watcher", text,
                      "a pulse must never silently become a daemon")

    def test_coordinator_tick_card_states_the_single_dispatch_boundary(self):
        card = SKILLS / "aep-coordinator-tick" / "SKILL.md"
        self.assertTrue(card.is_file(), "the coordinator tick needs a capability card")
        text = card.read_text(encoding="utf-8")
        self.assertEqual(_frontmatter(text)["min_agent_capability"], "cli")
        for token in ("零模型", "不是第二套调度器", "不回退", "--apply", "DEPOSITED",
                      "只有 `VERIFIED` 能关单", "启动常驻 watcher",
                      "docs/COORDINATOR_TICK.zh-CN.md", "FEDERATION_CONTEXT_MISSING",
                      "定时器不是判断"):
            with self.subTest(token=token):
                self.assertIn(token, text)
        self.assertIn("禁止", text, "failure handling must stay explicit")

    def test_relay_drill_card_states_the_completion_criterion(self):
        card = SKILLS / "aep-relay-drill" / "SKILL.md"
        self.assertTrue(card.is_file(), "the relay drill needs a capability card")
        text = card.read_text(encoding="utf-8")
        self.assertEqual(_frontmatter(text)["min_agent_capability"], "cli")
        for token in ("省积分", "操作拟真度", "依次", "run_complete", "VERIFIED",
                      "join-agent.ps1", "不代报在线", "SENDER_NOT_ONLINE",
                      "docs/OPERATIONAL_FIDELITY_AND_RELAY_DRILL.zh-CN.md"):
            with self.subTest(token=token):
                self.assertIn(token, text)
        self.assertIn("只有 `VERIFIED` 能关单", text)

    def test_legion_announce_card_states_the_room_and_verdict(self):
        card = SKILLS / "aep-legion-announce" / "SKILL.md"
        self.assertTrue(card.is_file(), "legion announcements need a capability card")
        text = card.read_text(encoding="utf-8")
        self.assertEqual(_frontmatter(text)["min_agent_capability"], "cli")
        for token in ("agent-legion", "coord-chat-read", "游标",
                      "command_memory.announce", "--mailbox-only", "DEPOSITED",
                      "只有 `VERIFIED` 能关单", "docs/LEGION_ANNOUNCE.zh-CN.md"):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_delivery_preflight_card_states_the_read_only_boundary(self):
        card = SKILLS / "aep-delivery-preflight" / "SKILL.md"
        self.assertTrue(card.is_file(), "desktop preflight needs a capability card")
        text = card.read_text(encoding="utf-8")
        self.assertEqual(_frontmatter(text)["min_agent_capability"], "cli")
        for token in ("只读", "UIPI", "send-after-verification", "第二意见",
                      "action_verification", "delivery_verdict", "advisory_only",
                      "只有 `VERIFIED` 能关单", "docs/DESKTOP_DELIVERY_PREFLIGHT.zh-CN.md"):
            with self.subTest(token=token):
                self.assertIn(token, text)
        self.assertIn("coord-uia deliver", text, "the card must name what it refuses to run")
        for token in ("coord-uia compose", "transcript_verdict",
                      "sources_in_conflict", "并列", "不放宽"):
            with self.subTest(token=token):
                self.assertIn(token, text,
                              "the second verdict source must stay beside, not above, the OCR checks")


if __name__ == "__main__":
    unittest.main()
