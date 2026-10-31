"""The worked example in the research doc must match what the code emits.

The schema name in that doc had already drifted once (``aep-next-action-v1``
versus the real ``agent-evolution-next-action-v1``) and nobody noticed for two
days, because prose cannot fail. This test makes the example fail instead.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

from agent_evolution_protocol.route_guidance import build_next_action


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "ROBUSTNESS_RESEARCH_20261028.zh-CN.md"
HEADING = "### P0：把已有路由变成可检查的一步指引"


def documented_example():
    text = DOC.read_text(encoding="utf-8")
    start = text.index(HEADING)
    match = re.search(r"```json\n(.*?)\n```", text[start:], re.S)
    if match is None:
        raise AssertionError("the research doc no longer carries a json example")
    return json.loads(match.group(1))


def live_example():
    result = {"scenario": "review-only", "task": "AEP-EXAMPLE-20261031",
              "agent": "workbuddyai", "recovery": {"status": "unclaimed"},
              "requires_clarification": False, "ambiguity": False, "blockers": []}
    workspace = "K:/PythonProjects5/agent-evolution-protocol"
    return build_next_action(result, workspace, f"{workspace}/coordination")


class DocumentedExampleTests(unittest.TestCase):
    def setUp(self):
        self.doc = documented_example()
        self.live = live_example()

    def test_schema_name_matches_the_code(self):
        self.assertEqual(self.doc["schema"], self.live["schema"])

    def test_field_set_matches_the_code(self):
        self.assertEqual(set(self.doc), set(self.live))

    def test_fallback_field_set_matches_the_code(self):
        self.assertEqual(set(self.doc["fallback"]), set(self.live["fallback"]))

    def test_kind_is_one_of_the_real_values(self):
        self.assertIn(self.doc["kind"], {"command", "needs_input", "file_only"})
        self.assertEqual(self.doc["kind"], self.live["kind"])

    def test_example_claims_no_execution(self):
        self.assertTrue(self.doc["read_only"])
        self.assertFalse(self.doc["execution_started"])
        self.assertFalse(self.doc["shell"])


class NoStaleSchemaNameTests(unittest.TestCase):
    def test_research_doc_never_mentions_the_old_schema_name(self):
        text = DOC.read_text(encoding="utf-8")
        self.assertNotIn("aep-next-action-v1", text)


if __name__ == "__main__":
    unittest.main()
