"""Proposal layer and the two verdicts it must never overwrite.

The measurements behind these tests (`.tools/MODEL_CHOICE_20261103.zh-CN.md`):
a local 4B generator answered 15/17 phrases that hit no keyword, but misread 4 of 8
explicit negations ("不要上传，也不要提交" -> project-orientation) and was *more permissive*
than the rule engine on the safety-critical subset. So the proposal may fill a gap and
nothing else.
"""

import unittest

from agent_evolution_protocol.routing import (
    KEYWORDS,
    READ_ONLY_SCENARIOS,
    WRITE_INTENTS,
    classify_intent,
    classify_intent_with_proposal,
)


class UnknownIsAVerdictTests(unittest.TestCase):
    def test_a_phrase_with_no_rule_is_unknown_not_orientation(self):
        for phrase in ("shipping improvement", "sleeping", "做一下那个", "嗯"):
            with self.subTest(phrase=phrase):
                result = classify_intent(phrase)
                self.assertTrue(result["unknown"])
                self.assertEqual("project-orientation", result["scenario"])
                self.assertTrue(result["requires_clarification"])

    def test_a_real_orientation_request_is_not_unknown(self):
        result = classify_intent("我们要接入这个协作区")
        self.assertFalse(result["unknown"])
        self.assertEqual("project-orientation", result["scenario"])

    def test_a_task_id_supplies_the_missing_intent(self):
        result = classify_intent("做一下那个", task="T125")
        self.assertFalse(result["unknown"])
        self.assertIn("task-join", result["matched_rules"])

    def test_an_explicit_read_only_phrase_is_known(self):
        # "不要上传" matches no scenario keyword, only the protection list. Reading only
        # `scenario` would call this a fallback; reading `unknown` tells the truth.
        result = classify_intent("不要上传")
        self.assertFalse(result["unknown"])
        self.assertTrue(result["read_only_explicit"])


class TwoKindsOfReadOnlyTests(unittest.TestCase):
    def test_the_fallback_scenario_is_read_only_but_not_explicitly_so(self):
        # This distinction is load-bearing: keying the proposal veto on `read_only` would
        # veto every proposal, because the unknown fallback is itself read-only.
        result = classify_intent("shipping improvement")
        self.assertTrue(result["read_only"])
        self.assertFalse(result["read_only_explicit"])

    def test_an_explicit_constraint_sets_both(self):
        result = classify_intent("只审查，不要执行上传")
        self.assertTrue(result["read_only"])
        self.assertTrue(result["read_only_explicit"])

    def test_a_write_scenario_sets_neither(self):
        result = classify_intent("浏览器先占住，我要跑上传")
        self.assertEqual("shared-resource-lock", result["scenario"])
        self.assertFalse(result["read_only"])
        self.assertFalse(result["read_only_explicit"])


class CompoundWriteTests(unittest.TestCase):
    """Two mutating intents joined by a sequencing marker have no settled lock scope."""

    def test_waking_an_agent_then_uploading_is_ambiguous(self):
        # Regression: `shared-resource-lock` used to be excluded from the independent set,
        # so this returned `wake-agent` and silently dropped the upload.
        result = classify_intent("唤醒它然后上传")
        self.assertTrue(result["ambiguity"])
        self.assertTrue(result["read_only"])
        self.assertEqual("status-report", result["scenario"])

    def test_reporting_then_uploading_is_still_ambiguous(self):
        result = classify_intent("汇报状态，然后上传 T125")
        self.assertTrue(result["ambiguity"])
        self.assertEqual("status-report", result["scenario"])

    def test_read_then_write_with_an_explicit_order_is_not_ambiguous(self):
        # Over-triggering here would block a clear two-step plan; ordering is stated.
        result = classify_intent("先审查，再提交")
        self.assertFalse(result["ambiguity"])
        self.assertEqual("review-only", result["scenario"])

    def test_a_single_write_with_no_marker_is_not_ambiguous(self):
        for phrase, scenario in (("我接手那个上传任务，继续做", "takeover"),
                                 ("浏览器先占住，我要跑上传", "shared-resource-lock"),
                                 ("汇报上传状态", "status-report")):
            with self.subTest(phrase=phrase):
                result = classify_intent(phrase)
                self.assertFalse(result["ambiguity"])
                self.assertEqual(scenario, result["scenario"])


class ProposalGateTests(unittest.TestCase):
    def test_no_proposer_means_no_proposal(self):
        result = classify_intent_with_proposal("shipping improvement")
        self.assertEqual("project-orientation", result["scenario"])
        self.assertFalse(result["proposal"]["applied"])
        self.assertEqual("not-consulted", result["proposal"]["reason"])

    def test_a_matched_rule_is_never_second_guessed(self):
        calls = []
        result = classify_intent_with_proposal(
            "帮我审查一下这段代码", propose=lambda text: calls.append(text) or "shared-resource-lock")
        self.assertEqual("review-only", result["scenario"])
        self.assertEqual([], calls)
        self.assertEqual("rules-matched", result["proposal"]["reason"])

    def test_an_explicit_negation_is_never_handed_to_the_model(self):
        # The measured failure mode: a generator reads the noun and drops the "不要".
        calls = []
        result = classify_intent_with_proposal(
            "不要上传，也不要提交", propose=lambda text: calls.append(text) or "shared-resource-lock")
        self.assertEqual("review-only", result["scenario"])
        self.assertEqual([], calls)
        self.assertEqual("vetoed-explicit-read-only", result["proposal"]["reason"])

    def test_ambiguity_is_never_handed_to_the_model(self):
        calls = []
        result = classify_intent_with_proposal(
            "唤醒它然后上传", propose=lambda text: calls.append(text) or "wake-agent")
        self.assertEqual([], calls)
        self.assertFalse(result["proposal"]["applied"])

    def test_an_invented_scenario_is_discarded_not_mapped(self):
        result = classify_intent_with_proposal(
            "shipping improvement", propose=lambda text: "do-the-thing")
        self.assertEqual("project-orientation", result["scenario"])
        self.assertFalse(result["proposal"]["applied"])
        self.assertEqual("unknown-scenario", result["proposal"]["reason"])

    def test_a_broken_proposer_does_not_take_routing_down(self):
        def explode(_text):
            raise TimeoutError("ollama went away")

        result = classify_intent_with_proposal("shipping improvement", propose=explode)
        self.assertEqual("project-orientation", result["scenario"])
        self.assertTrue(result["requires_clarification"])
        self.assertEqual("proposer-failed:TimeoutError", result["proposal"]["reason"])

    def test_a_read_only_proposal_clears_the_clarification(self):
        result = classify_intent_with_proposal(
            "那批文件你帮我过一遍，别动它们", propose=lambda text: "review-only")
        self.assertEqual("review-only", result["scenario"])
        self.assertTrue(result["proposal"]["applied"])
        self.assertFalse(result["requires_clarification"])
        self.assertTrue(result["read_only"])

    def test_a_write_proposal_is_confirmed_once(self):
        # A guessed write is not a no-op, so it does not get to skip confirmation.
        result = classify_intent_with_proposal(
            "别让他们同时用那个东西", propose=lambda text: "shared-resource-lock")
        self.assertEqual("shared-resource-lock", result["scenario"])
        self.assertTrue(result["requires_clarification"])
        self.assertEqual("filled-unknown-write-needs-confirmation", result["proposal"]["reason"])

    def test_an_applied_proposal_clears_the_unknown_flag(self):
        # Regression: `unknown` stayed True after the gap was filled, so every applied
        # proposal still reported "I don't know" — visible only once the eval scored the
        # verdict instead of the scenario.
        result = classify_intent_with_proposal(
            "现在到底走到哪一步了", propose=lambda text: "status-report")
        self.assertFalse(result["unknown"])
        self.assertFalse(result["requires_clarification"])
        self.assertEqual("status-report", result["scenario"])

    def test_a_dict_proposal_records_its_source(self):
        result = classify_intent_with_proposal(
            "现在到底走到哪一步了",
            propose=lambda text: {"scenario": "status-report", "source": "qwen3:4b"})
        self.assertEqual("status-report", result["scenario"])
        self.assertEqual("qwen3:4b", result["proposal"]["source"])


class ProposalCannotWidenTests(unittest.TestCase):
    """The one-vote-veto property, checked over every scenario name rather than by example."""

    PHRASES = ("不要上传，也不要提交", "只审查，不要执行", "read only: 检查发布脚本",
               "汇报状态就行，不要执行任何命令", "先看看，不要提交")

    def test_no_proposal_turns_an_explicit_constraint_into_a_write(self):
        for phrase in self.PHRASES:
            for candidate in KEYWORDS:
                with self.subTest(phrase=phrase, candidate=candidate):
                    result = classify_intent_with_proposal(
                        phrase, propose=lambda text, c=candidate: c)
                    self.assertTrue(result["read_only"])
                    self.assertTrue(result["read_only_explicit"])
                    self.assertNotIn(result["scenario"], WRITE_INTENTS)
                    self.assertFalse(result["proposal"]["applied"])

    def test_a_read_only_verdict_never_becomes_a_write_scenario(self):
        for phrase in self.PHRASES:
            for candidate in sorted(WRITE_INTENTS):
                with self.subTest(phrase=phrase, candidate=candidate):
                    result = classify_intent_with_proposal(
                        phrase, propose=lambda text, c=candidate: c)
                    self.assertIn(result["scenario"], READ_ONLY_SCENARIOS)


if __name__ == "__main__":
    unittest.main()
