"""Guard the join-troubleshooting baseline against turning into folklore.

The failure this document exists for is an Agent misreading a host-platform
permission error as "I cannot join the AEP coordination area". If the four
online layers, the two-system comparison, or the "never self-report for
another Agent" rule ever get diluted, the same misdiagnosis returns.
"""
from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "AGENT_JOIN_TROUBLESHOOTING.zh-CN.md"
JOIN = ROOT / "docs" / "JOINING_ANY_TASK.zh-CN.md"


class JoinTroubleshootingTests(unittest.TestCase):
    def test_document_exists(self):
        self.assertTrue(DOC.is_file())

    def test_four_online_layers_are_separated(self):
        text = DOC.read_text(encoding="utf-8")
        for token in ("四层", "目录痕迹", "新鲜在线证据", "OS 进程在跑",
                      "窗口可操作", "fresh_online", "presence.effective",
                      "SENDER_NOT_ONLINE", "TTL"):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_two_coordination_systems_are_contrasted(self):
        text = DOC.read_text(encoding="utf-8")
        for token in ("两套", "no_memberships", "项目成员资格", "讨论区 ID",
                      "全仓 Grep 零匹配", "valid_agent", "known_agents",
                      "agent-legion", "coordination/"):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_the_five_recurring_pitfalls_are_named(self):
        text = DOC.read_text(encoding="utf-8")
        for token in ("游标正序", "coord-chat-read --limit", "默认 `room=workspace`",
                      "--source", "DEPOSITED_UNCONFIRMED", "只有 `VERIFIED` 能关单",
                      "--mailbox-only"):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_self_report_is_the_only_fix_and_never_delegated(self):
        text = DOC.read_text(encoding="utf-8")
        for token in ("join-agent.ps1", "-TtlMinutes 30", "自己在原会话",
                      "不代任何 Agent 上报在线", "自导自演", "不新建会话"):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_reproducible_commands_and_channel_are_present(self):
        text = DOC.read_text(encoding="utf-8")
        for token in ("coord-status", "coord-chat-publish", "coord-send",
                      "read_events", "coordination/private_conversations/events/",
                      "grep -rn", "loop_verdict"):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_real_case_is_recorded_without_claiming_the_loop_finished(self):
        text = DOC.read_text(encoding="utf-8")
        for token in ("2026-11-08 实证案例", "qoder_cn", "17:24:42",
                      "fresh_online=['qoder_cn']", "仍需要至少 2 个"):
            with self.subTest(token=token):
                self.assertIn(token, text)
        self.assertIn("不算跑完", text)

    def test_no_platform_membership_claim_slips_in(self):
        text = DOC.read_text(encoding="utf-8")
        banned = ("已获得项目成员资格", "已将 Agent 加入项目",
                  "AEP 支持成员审批", "已接入宿主讨论区")
        for phrase in banned:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, text)
        self.assertIn("明确不做", text)

    def test_normal_join_doc_points_here(self):
        text = JOIN.read_text(encoding="utf-8")
        self.assertIn("AGENT_JOIN_TROUBLESHOOTING.zh-CN.md", text,
                      "a stuck agent must be able to find the diagnostic doc")


if __name__ == "__main__":
    unittest.main()
