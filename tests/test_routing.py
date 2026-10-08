import json
import tempfile
import unittest
from pathlib import Path

from agent_evolution_protocol.routing import classify_intent, load_route_config


class RoutingTests(unittest.TestCase):
    def test_combined_intents_respect_read_only_and_resource_guards(self):
        for intent, scenario in [
            ("接手上传 T125，不执行", "review-only"),
            ("并行上传 T125", "shared-resource-lock"),
            ("汇报上传状态", "status-report"),
            ("do not wake qoder", "review-only"),
            ("parallel upload", "shared-resource-lock"),
            ("WorkBuddy卡住了，接手", "takeover"),
            ("不支持 CLI，只能读文件，审查方案", "cli-less-fallback"),
        ]:
            with self.subTest(intent=intent):
                self.assertEqual(scenario, classify_intent(intent)["scenario"])

    def test_conflicting_commands_require_clarification(self):
        result = classify_intent("汇报状态，然后上传 T125")
        self.assertTrue(result["requires_clarification"])
        self.assertEqual("low", result["confidence"])
        self.assertEqual("status-report", result["scenario"])
        self.assertTrue(result["decision_trace"])

    def test_unknown_phrase_and_english_substrings_do_not_trigger_wake(self):
        for intent in ("shipping improvement", "sleeping", "做一下那个"):
            result = classify_intent(intent)
            self.assertEqual("project-orientation", result["scenario"])
            self.assertTrue(result["requires_clarification"])

    def test_config_overrides_do_not_remove_protected_guards(self):
        config = {"schema": "agent-evolution-routing-v1", "keywords": {"takeover": ["接棒"]}, "replace_keywords": {"review-only": [], "shared-resource-lock": []}}
        self.assertEqual("takeover", classify_intent("接棒", config=config)["scenario"])
        self.assertEqual("review-only", classify_intent("接棒但不要执行", config=config)["scenario"])
        self.assertEqual("shared-resource-lock", classify_intent("上传", config=config)["scenario"])

    def test_invalid_and_missing_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertIsNone(load_route_config(root))
            path = root / ".aep" / "routing.json"
            path.parent.mkdir()
            for config in ({"schema": "wrong"}, {"schema": "agent-evolution-routing-v1", "keywords": {"made-up": ["go"]}}, {"schema": "agent-evolution-routing-v1", "keywords": {"takeover": [""]}}):
                path.write_text(json.dumps(config), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_route_config(root)
