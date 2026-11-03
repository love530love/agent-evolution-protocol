"""The Ollama adapter: parsing, abstention, and the promise that routing stays socket-free."""

import ast
import json
import unittest
from pathlib import Path

from agent_evolution_protocol.routing import classify_intent_with_proposal
from agent_evolution_protocol.routing_proposer import SCENARIOS, build_prompt, ollama_proposer

ROUTING_SOURCE = Path(__file__).resolve().parents[1] / "src" / "agent_evolution_protocol" / "routing.py"
NETWORK_MODULES = {"urllib", "http", "socket", "ssl", "requests", "httpx", "aiohttp"}


class PromptTests(unittest.TestCase):
    def test_the_prompt_names_every_scenario(self):
        prompt = build_prompt()
        for name in SCENARIOS:
            with self.subTest(scenario=name):
                self.assertIn(name, prompt)

    def test_the_prompt_asks_for_an_explicit_abstention(self):
        # Without this line the model answers anyway, and a forced answer on a nonsense
        # phrase is the failure we measured.
        self.assertIn("unknown=true", build_prompt())


class ParsingTests(unittest.TestCase):
    def test_a_plain_answer_becomes_a_sourced_proposal(self):
        propose = ollama_proposer(ask=lambda text: json.dumps({"scenario": "review-only", "unknown": False}))
        self.assertEqual({"scenario": "review-only", "source": "ollama:qwen3:4b"}, propose("随便"))
        self.assertEqual({"scenario": "review-only", "source": "ollama:qwen3:4b"}, propose("随便"))

    def test_abstention_returns_none(self):
        propose = ollama_proposer(ask=lambda text: json.dumps({"scenario": "project-orientation", "unknown": True}))
        self.assertIsNone(propose("shipping improvement"))

    def test_malformed_json_returns_none(self):
        propose = ollama_proposer(ask=lambda text: "not json at all")
        self.assertIsNone(propose("随便"))

    def test_an_invented_scenario_returns_none(self):
        propose = ollama_proposer(ask=lambda text: json.dumps({"scenario": "do-the-thing", "unknown": False}))
        self.assertIsNone(propose("随便"))

    def test_a_non_object_answer_returns_none(self):
        propose = ollama_proposer(ask=lambda text: json.dumps(["review-only"]))
        self.assertIsNone(propose("随便"))

    def test_a_dead_backend_raises_and_routing_survives(self):
        def dead(_text):
            raise OSError("connection refused")

        result = classify_intent_with_proposal("shipping improvement", propose=ollama_proposer(ask=dead))
        self.assertEqual("project-orientation", result["scenario"])
        self.assertTrue(result["requires_clarification"])
        self.assertEqual("proposer-failed:OSError", result["proposal"]["reason"])

    def test_the_model_name_reaches_the_source_field(self):
        propose = ollama_proposer(model="qwen2.5:0.5b",
                                  ask=lambda text: json.dumps({"scenario": "status-report"}))
        self.assertEqual("ollama:qwen2.5:0.5b", propose("x")["source"])


class RoutingStaysSocketFreeTests(unittest.TestCase):
    """`routing.py` must remain provable without a model, a network, or a GPU."""

    def test_routing_imports_no_network_module(self):
        tree = ast.parse(ROUTING_SOURCE.read_text(encoding="utf-8"))
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.append(node.module)
        offences = sorted({name.split(".")[0] for name in found} & NETWORK_MODULES)
        self.assertEqual([], offences)
        # A scanner that reads nothing would pass the assertion above.
        self.assertGreater(len(found), 3)


if __name__ == "__main__":
    unittest.main()
