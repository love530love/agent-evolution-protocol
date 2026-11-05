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
            for name in set(re.findall(r"\b(coord-[a-z0-9-]+)\b", text)):
                scanned += 1
                with self.subTest(card=card.parent.name, command=name):
                    self.assertIn(name, known)
        self.assertGreater(scanned, 0, "no commands found: guard would be vacuous")

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

    def test_file_only_card_really_offers_a_file_path(self):
        card = (SKILLS / "aep-desktop-wake" / "SKILL.md")
        meta = _frontmatter(card.read_text(encoding="utf-8"))
        self.assertEqual(meta["min_agent_capability"], "file-only")
        self.assertIn("coordination/delegation/inbox/<id>.json",
                      card.read_text(encoding="utf-8"))

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


if __name__ == "__main__":
    unittest.main()
