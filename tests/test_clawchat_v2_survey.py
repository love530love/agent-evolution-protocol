"""Keep the ClawChat v2 org survey a fact baseline, not an integration claim.

A survey that silently turns into "we integrated it" is exactly the kind of
unverified assertion this project refuses to let into a team. The guard also
pins the reproducible commands and the authoritative-count finding, so a
future reader cannot quietly drop the evidence and keep the conclusion.
"""
from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SURVEY = ROOT / "docs" / "CLAWCHAT_V2_SURVEY.zh-CN.md"
EVALUATION = ROOT / "docs" / "CLAWCHAT_EVALUATION.md"


class SurveyFactBaselineTests(unittest.TestCase):
    def test_survey_exists(self):
        self.assertTrue(SURVEY.is_file(), "the org survey must be committed")

    def test_both_repositories_and_org_are_named(self):
        text = SURVEY.read_text(encoding="utf-8")
        for token in ("clawling", "CLAWLING PTE.LTD.", "Organization",
                      "clawchat-plugin-hermes-agent",
                      "clawchat-plugin-install-cli", "public_repos=2"):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_protocol_gap_is_stated_explicitly(self):
        text = SURVEY.read_text(encoding="utf-8")
        for token in ("Protocol v2", "WebSocket", "websockets>=13,<16",
                      "NDJSON", "HTTP `/v1/send`", "协议代差",
                      "clawchat_transport.py", "CLAWLING_INTEGRATION.md",
                      "CLAWCHAT_EVALUATION.md"):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_authoritative_count_and_the_drift_are_recorded(self):
        text = SURVEY.read_text(encoding="utf-8")
        for token in ("plugin.yaml", "provides_tools", "55", "53",
                      "文档漂移", "provides_hooks"):
            with self.subTest(token=token):
                self.assertIn(token, text)
        self.assertIn("55 个", text, "the authoritative count must be spelled out")

    def test_no_integration_claim_slips_in(self):
        text = SURVEY.read_text(encoding="utf-8")
        banned = ("本项目已接入官方", "已成功集成官方", "已部署 ClawChat 服务端",
                  "现在可以直连官方 v2", "本项目已兼容 Protocol v2")
        for phrase in banned:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, text)
        self.assertIn("明确未做", text)

    def test_reproducible_commands_and_raw_snapshot_are_present(self):
        text = SURVEY.read_text(encoding="utf-8")
        for token in ("gh api orgs/clawling", "type=all", "git/trees/main",
                      "附录 A", "0.14.0-125", "d18f8b1c", "cadd1995"):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_hard_constraints_and_reusable_ideas_are_both_present(self):
        text = SURVEY.read_text(encoding="utf-8")
        for token in ("不引入运行时", "库级无服务", "不新增 schema",
                      "默认 dry-run", "能力卡", "原子写", "prompt-injection",
                      "hello-ok"):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_legacy_evaluation_points_at_the_new_survey(self):
        text = EVALUATION.read_text(encoding="utf-8")
        self.assertIn("CLAWCHAT_V2_SURVEY.zh-CN.md", text,
                      "readers must not mistake the same-name projects for this org")
        self.assertIn("同名不同项目", text)


if __name__ == "__main__":
    unittest.main()
