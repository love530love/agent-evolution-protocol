import unittest

from agent_evolution_protocol.federation import diagnosis


EXPECTED = {
    "token_exhausted": ("agent", "handoff"),
    "no_heartbeat": ("agent", "switch_agent"),
    "refusal": ("agent", "switch_agent"),
    "loop": ("agent", "cool_down"),
    "browser_blocked": ("resource", "cool_down"),
    "captcha": ("resource", "escalate"),
    "network_error": ("resource", "retry"),
    "foreign_message": ("external", "quarantine"),
    "conflict": ("external", "arbitrate"),
    "manual_pause": ("external", "pause"),
}


class ClassifyTests(unittest.TestCase):
    def test_every_known_signal_maps_to_layer_and_action(self):
        for signal, (layer, action) in EXPECTED.items():
            row = diagnosis.classify(signal)
            self.assertEqual(row["layer"], layer, signal)
            self.assertEqual(row["action"], action, signal)

    def test_unknown_signal_escalates_instead_of_guessing(self):
        row = diagnosis.classify("cosmic_ray")
        self.assertEqual(row["layer"], "unknown")
        self.assertEqual(row["action"], "escalate")
        self.assertTrue(row["needs_human"])

    def test_retry_budget_is_bounded(self):
        self.assertEqual(diagnosis.classify("network_error", attempts=0)["action"], "retry")
        self.assertEqual(diagnosis.classify("network_error", attempts=1)["action"], "retry")
        exhausted = diagnosis.classify("network_error", attempts=diagnosis.MAX_RETRY)
        self.assertEqual(exhausted["action"], "escalate")
        self.assertTrue(exhausted["needs_human"])
        self.assertIn("retry budget", exhausted["reason"])

    def test_non_retryable_actions_ignore_attempts(self):
        row = diagnosis.classify("token_exhausted", attempts=9)
        self.assertEqual(row["action"], "handoff")

    def test_cooldown_only_for_cool_down_and_retry(self):
        self.assertGreater(diagnosis.classify("browser_blocked")["cooldown_seconds"], 0)
        self.assertGreater(diagnosis.classify("network_error")["cooldown_seconds"], 0)
        self.assertEqual(diagnosis.classify("conflict")["cooldown_seconds"], 0)

    def test_invalid_input(self):
        with self.assertRaises(ValueError):
            diagnosis.classify("")
        with self.assertRaises(ValueError):
            diagnosis.classify("loop", attempts=-1)


class DecideTests(unittest.TestCase):
    def test_empty_signal_list(self):
        self.assertEqual(diagnosis.decide([])["action"], "none")

    def test_escalation_outranks_everything(self):
        result = diagnosis.decide(["network_error", "captcha", "conflict"])
        self.assertEqual(result["action"], "escalate")
        self.assertTrue(result["escalate"])

    def test_pause_outranks_handoff(self):
        result = diagnosis.decide(["token_exhausted", "manual_pause"])
        self.assertEqual(result["action"], "pause")

    def test_handoff_outranks_switch(self):
        result = diagnosis.decide(["no_heartbeat", "token_exhausted"])
        self.assertEqual(result["action"], "handoff")

    def test_attempts_can_be_supplied_per_signal(self):
        result = diagnosis.decide([("network_error", diagnosis.MAX_RETRY)])
        self.assertEqual(result["action"], "escalate")

    def test_result_is_deterministic(self):
        signals = ["conflict", "browser_blocked", "network_error"]
        self.assertEqual(diagnosis.decide(signals), diagnosis.decide(signals))

    def test_bad_signal_shape_is_refused(self):
        with self.assertRaises(ValueError):
            diagnosis.decide([("a", "b", "c")])


if __name__ == "__main__":
    unittest.main()
