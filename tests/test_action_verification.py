"""Verification is a contract, and every uncertain answer is ``None``.

The point of these tests is not that three helpers work. It is that the two
things that were previously decided ad hoc -- "did the paste land" and "may we
send now" -- are decided in one place, with the direction of every unknown
pinned. A missing check must never read as success, and a substituted character
must not read as absence: both mistakes happened on this host, in opposite
directions, on the same day.
"""

import unittest

from agent_evolution_protocol.coordination import action_verification as av
from agent_evolution_protocol.coordination import input_routing


class FragmentTests(unittest.TestCase):
    def test_an_exact_needle_survives_whole(self):
        self.assertEqual(av.fragment_match("不会按下发送键", "x不会按下发送键y"), "不会按下发送键")

    def test_a_substituted_character_costs_only_that_character(self):
        # Measured: the recogniser rendered 键 as 鍵, and exact matching then
        # reported that a paste which had landed never happened.
        self.assertEqual(av.fragment_match("不会按下发送键", "不会按下发送鍵"), "不会按下发送")

    def test_a_needle_shorter_than_the_floor_never_matches(self):
        self.assertEqual(av.fragment_match("发送", "发送"), "")

    def test_empty_inputs_are_empty(self):
        self.assertEqual(av.fragment_match("", "abc"), "")
        self.assertEqual(av.fragment_match("abcdef", ""), "")


class CheckTests(unittest.TestCase):
    def test_present_reports_the_fragment_it_matched(self):
        result = av.text_present("校验串 2146", "前缀 校验串 2146 后缀")
        self.assertEqual(result["outcome"], av.PASS)
        self.assertEqual(result["evidence"]["fragment"], "校验串 2146")

    def test_present_says_so_when_it_matched_short(self):
        result = av.text_present("abcdefgh", "abcdZZZZ")
        self.assertEqual(result["outcome"], av.PASS)
        self.assertIn("substituted", result["note"])

    def test_absent_is_the_inverse_and_says_why(self):
        self.assertEqual(av.text_absent("探针", "没有")["outcome"], av.PASS)
        self.assertEqual(av.text_absent("探针", "这里有探针")["reason"], "STILL_ON_SCREEN")

    def test_a_missing_hint_is_unknown_not_a_pass(self):
        # A hint that was never on screen cannot have disappeared. Reporting a
        # pass here is how "nothing was checked" becomes "everything is fine".
        result = av.placeholder_gone("", "whatever")
        self.assertEqual(result["outcome"], av.UNKNOWN)
        self.assertEqual(result["reason"], "NO_HINT")

    def test_the_hint_being_back_is_a_pass(self):
        self.assertEqual(av.placeholder_gone("今天帮你做些什么", "… 今天帮你做些什么")
                         ["outcome"], av.PASS)


class MovedTests(unittest.TestCase):
    LINES = [
        {"text": "上面的对话", "y": 100},
        {"text": "校验串 2146 已送达", "y": 400},
        {"text": "这里还是输入框", "y": 900},
    ]

    def test_leaving_the_composer_and_arriving_above_is_the_only_pass(self):
        result = av.text_moved("校验串 2146", lines=self.LINES, split_y=720)
        self.assertEqual(result["outcome"], av.PASS)
        self.assertTrue(result["evidence"]["in_transcript"])
        self.assertFalse(result["evidence"]["in_composer"])

    def test_still_in_the_box_is_not_delivery(self):
        lines = [{"text": "校验串 2146", "y": 900}]
        result = av.text_moved("校验串 2146", lines=lines, split_y=720)
        self.assertEqual(result["outcome"], av.FAIL)
        self.assertEqual(result["reason"], "STILL_IN_COMPOSER")

    def test_in_both_places_is_a_failure_not_a_tie(self):
        # A stale draft plus a delivered copy looks like success to a presence
        # check; the band test is the only thing that separates them.
        lines = [{"text": "校验串 2146", "y": 400}, {"text": "校验串 2146", "y": 900}]
        self.assertEqual(av.text_moved("校验串 2146", lines=lines, split_y=720)["reason"],
                         "STILL_IN_COMPOSER")

    def test_no_probe_or_no_boundary_is_unknown(self):
        self.assertEqual(av.text_moved("", lines=self.LINES, split_y=720)["outcome"], av.UNKNOWN)
        self.assertEqual(av.text_moved("x", lines=self.LINES, split_y=0)["outcome"], av.UNKNOWN)

    def test_lines_without_a_usable_y_are_skipped(self):
        lines = [{"text": "校验串 2146", "y": "nonsense"}, {"text": "校验串 2146", "y": 100}]
        self.assertEqual(av.text_moved("校验串 2146", lines=lines, split_y=720)["outcome"], av.PASS)


class VerdictTests(unittest.TestCase):
    def _pass(self, name):
        return av._check(name, av.PASS, evidence={})

    def _fail(self, name, reason="WHY"):
        return av._check(name, av.FAIL, evidence={}, reason=reason)

    def test_all_required_passing_is_verified(self):
        result = av.verdict([self._pass("a"), self._pass("b")], required=("a", "b"))
        self.assertIs(result["verified"], True)

    def test_a_failure_refutes_even_when_everything_else_passed(self):
        result = av.verdict([self._pass("a"), self._fail("b", "STILL_IN_COMPOSER")],
                            required=("a", "b"))
        self.assertIs(result["verified"], False)
        self.assertEqual(result["reason"], "STILL_IN_COMPOSER")

    def test_a_missing_check_is_unknown(self):
        result = av.verdict([self._pass("a")], required=("a", "b"))
        self.assertIsNone(result["verified"])
        self.assertEqual(result["missing"], ["b"])

    def test_a_failure_outranks_a_missing_check(self):
        # A refutation is information; an absence is not.
        result = av.verdict([self._fail("a")], required=("a", "b"))
        self.assertIs(result["verified"], False)

    def test_no_checks_is_unknown(self):
        self.assertIsNone(av.verdict([])["verified"])

    def test_nothing_required_means_nothing_concluded(self):
        # A pile of passes proves nothing if the caller never said what counts.
        result = av.verdict([self._pass("a")])
        self.assertIsNone(result["verified"])
        self.assertEqual(result["reason"], "NO_REQUIRED_CHECKS")


class SendReportTests(unittest.TestCase):
    def test_the_ocr_path_receipt_verifies_itself(self):
        report = {"probe_in_composer_before_send": True, "probe_in_transcript": True,
                  "probe_in_composer_after": False, "placeholder_gone": True}
        extracted = av.from_send_report(report)
        result = av.verdict(extracted["checks"],
                            required=("text_arrived", "text_left_composer", "composer_emptied"))
        self.assertIs(result["verified"], True)

    def test_the_text_staying_in_the_box_refutes(self):
        report = {"probe_in_transcript": True, "probe_in_composer_after": True,
                  "placeholder_gone": False}
        result = av.verdict(av.from_send_report(report)["checks"],
                            required=("text_arrived", "text_left_composer", "composer_emptied"))
        self.assertIs(result["verified"], False)
        self.assertIn("text_left_composer", result["failed"])

    def test_fields_the_receipt_does_not_carry_stay_absent(self):
        extracted = av.from_send_report({"probe_in_transcript": True})
        self.assertEqual([check["check"] for check in extracted["checks"]], ["text_arrived"])

    def test_a_non_boolean_field_is_ignored_rather_than_coerced(self):
        extracted = av.from_send_report({"probe_in_transcript": "true"})
        self.assertEqual(extracted["checks"], [])

    def test_delivery_and_verification_are_different_facts(self):
        # Order 20261102T145319592152Z-c0272241 reached AGENT_REPLY_VERIFIED with
        # every probe field false: the agent's ACK proved delivery, and the paste
        # was never proven at all. Two independent facts, and collapsing them
        # would have hidden that the probe did not fire.
        report = {"probe_in_composer_before_send": False, "probe_in_transcript": False,
                  "probe_in_composer_after": False, "placeholder_gone": True,
                  "composer_cleared": True, "composer_band_growth": 41}
        result = av.verdict(av.from_send_report(report)["checks"],
                            required=("text_arrived", "text_left_composer", "composer_emptied"))
        self.assertIs(result["verified"], False)
        self.assertIn("text_arrived", result["failed"])
        self.assertIn("composer_emptied", result["passed"])


class DeliveryVerdictTests(unittest.TestCase):
    LOCATE = {"locate_source": "vision", "needs_verification": True}

    def test_without_verification_a_guess_still_needs_verification(self):
        result = input_routing.delivery_verdict(locate=self.LOCATE)
        self.assertEqual(result["state"], "READY_UNVERIFIED")

    def test_a_pass_lets_a_guess_be_sent(self):
        verification = av.verdict([av._check("a", av.PASS, evidence={})], required=("a",))
        result = input_routing.delivery_verdict(locate=self.LOCATE, verification=verification)
        self.assertEqual(result["state"], "READY")
        self.assertEqual(result["action"], "send")
        self.assertIn("verified", result["note"])

    def test_a_refutation_defers(self):
        verification = av.verdict([av._check("a", av.FAIL, evidence={}, reason="NEVER_ARRIVED")])
        result = input_routing.delivery_verdict(locate=self.LOCATE, verification=verification)
        self.assertEqual(result["action"], "defer")
        self.assertEqual(result["reason"], "VERIFICATION_REFUTED")

    def test_an_incomplete_verification_does_not_become_a_send(self):
        verification = av.verdict([], required=("a",))
        result = input_routing.delivery_verdict(locate=self.LOCATE, verification=verification)
        self.assertEqual(result["state"], "READY_UNVERIFIED")
        self.assertEqual(result["reason"], "VERIFICATION_INCOMPLETE")

    def test_an_observation_does_not_need_a_verification_to_send(self):
        locate = {"locate_source": "uia", "needs_verification": False}
        self.assertEqual(input_routing.delivery_verdict(locate=locate)["state"], "READY")

    def test_nothing_located_defers_before_verification_is_consulted(self):
        verification = av.verdict([av._check("a", av.PASS, evidence={})], required=("a",))
        result = input_routing.delivery_verdict(locate={"locate_source": None},
                                                verification=verification)
        self.assertEqual(result["action"], "defer")

    def test_an_unproven_policy_step_still_outranks_a_pass(self):
        verification = av.verdict([av._check("a", av.PASS, evidence={})], required=("a",))
        policy = {"stopped_because": "policy-finished",
                  "steps": [{"index": 0, "operation": "click", "verified": None}]}
        result = input_routing.delivery_verdict(locate=self.LOCATE, policy_result=policy,
                                                verification=verification)
        self.assertEqual(result["reason"], "STEP_NOT_VERIFIED")


if __name__ == "__main__":
    unittest.main()
