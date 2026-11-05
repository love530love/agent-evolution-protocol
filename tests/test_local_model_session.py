"""Acceptance gate for the ephemeral, use-and-release model layer.

The point is not model quality. It is that a weak/offline host stays fast:
no resident service, no GPU grab, no leaked model, and a clean rule-only
fallback whenever the backend is unavailable.
"""
from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agent_evolution_protocol.coordination import insight_proposer as proposer
from agent_evolution_protocol.coordination import local_model_session as session


def _ok(_argv):
    return type("Result", (), {"returncode": 0})()


class PlanTests(unittest.TestCase):
    def test_default_plan_never_starts_a_server(self):
        commands = session.Plan().commands()
        rendered = " ".join(" ".join(v) for v in commands.values() if v)
        self.assertNotIn("server start", rendered)
        self.assertNotIn("--all", rendered)
        self.assertNotIn("shell=True", rendered)

    def test_load_uses_ttl_cpu_only_and_a_small_context(self):
        load = session.Plan().commands()["load"]
        self.assertIn("--ttl", load)
        self.assertEqual(load[load.index("--ttl") + 1], "60")
        self.assertEqual(load[load.index("--gpu") + 1], "off")
        self.assertEqual(load[load.index("-c") + 1], "2048")

    def test_unload_targets_only_our_identifier(self):
        unload = session.Plan().commands()["unload"]
        self.assertEqual(unload[-1], session.DEFAULT_IDENTIFIER)
        self.assertNotIn("--all", unload)

    def test_commands_are_argv_lists_not_shell_strings(self):
        for name, argv in session.Plan().commands().items():
            if argv is None:
                continue
            self.assertIsInstance(argv, list, name)
            self.assertTrue(all(isinstance(part, str) for part in argv), name)

    def test_plan_rejects_resource_hungry_settings(self):
        for kwargs in ({"ttl_seconds": 5}, {"ttl_seconds": 99999},
                       {"context_length": 100000}, {"backend": "cuda"},
                       {"gpu": "everything"}, {"model": ""}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    session.Plan(**kwargs)

    def test_ollama_backend_never_starts_a_service(self):
        commands = session.Plan(backend="ollama").commands()
        self.assertIsNone(commands["load"])
        self.assertEqual(commands["unload"][1], "stop")


class SessionTests(unittest.TestCase):
    def test_load_failure_leaves_nothing_loaded_and_no_unload_attempt(self):
        calls = []
        with self.assertRaises(session.ModelUnavailable):
            with session.EphemeralModelSession(runner=lambda argv: (
                    calls.append(argv) or type("R", (), {"returncode": 1})())):
                pass
        self.assertEqual(len(calls), 1, "a failed load must not trigger unload")

    def test_probe_off_raises_without_running_any_command(self):
        calls = []
        with self.assertRaises(session.ModelUnavailable):
            with session.EphemeralModelSession(runner=lambda argv: calls.append(argv) or _ok(argv),
                                               probe=lambda: False):
                pass
        self.assertEqual(calls, [], "an offline server must never be started")

    def test_session_unloads_even_when_the_body_raises(self):
        calls = []
        with self.assertRaisesRegex(RuntimeError, "body failed"):
            with session.EphemeralModelSession(
                    runner=lambda argv: calls.append(list(argv)) or _ok(argv)) as active:
                self.assertTrue(active.loaded)
                raise RuntimeError("body failed")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][1], "unload")
        self.assertFalse(active.loaded)

    def test_unload_failure_does_not_mask_the_original_exception(self):
        def runner(argv):
            if argv[1] == "unload":
                raise OSError("lms disappeared")
            return _ok(argv)

        with self.assertRaisesRegex(RuntimeError, "original"):
            with session.EphemeralModelSession(runner=runner):
                raise RuntimeError("original")
        with session.EphemeralModelSession(runner=runner):
            pass  # no body exception: unload failure is swallowed

    def test_ollama_session_only_stops_its_own_model(self):
        calls = []
        with session.EphemeralModelSession(
                plan=session.Plan(backend="ollama"),
                runner=lambda argv: calls.append(list(argv)) or _ok(argv)):
            pass
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1:], ["stop", session.Plan(backend="ollama").model])


class ProposerBackendTests(unittest.TestCase):
    def test_lmstudio_uses_openai_compatible_shape(self):
        seen = {}

        def fake_post(url, payload, timeout):
            seen.update(url=url, payload=payload)
            return {"choices": [{"message": {"content": json.dumps({
                "title": "标题", "hypothesis": "假设", "checks": ["检查"]
            }, ensure_ascii=False)}}]}

        with patch.object(proposer, "_post_json", fake_post):
            fn = proposer.local_model_proposer(backend="lmstudio")
            result = fn({"rule": "unverified_overlay", "count": 1, "scanned": 1,
                         "summary": "summary"})
        self.assertTrue(seen["url"].endswith("/v1/chat/completions"))
        self.assertEqual(seen["payload"]["response_format"], {"type": "json_object"})
        self.assertEqual(result["title"], "标题")

    def test_ollama_uses_native_chat_shape(self):
        seen = {}

        def fake_post(url, payload, timeout):
            seen.update(url=url, payload=payload)
            return {"message": {"content": '{"unknown": true}'}}

        with patch.object(proposer, "_post_json", fake_post):
            fn = proposer.local_model_proposer(backend="ollama")
            result = fn({"rule": "x", "count": 1, "scanned": 1, "summary": "s"})
        self.assertTrue(seen["url"].endswith("/api/chat"))
        self.assertEqual(seen["payload"]["format"], "json")
        self.assertIsNone(result)

    def test_describe_never_leaks_evidence_contents(self):
        finding = {"rule": "r", "count": 2, "scanned": 3, "summary": "s",
                   "evidence_files": [str(Path("C:/secret/evidence.json"))],
                   "evidence_sha256": ["deadbeef"]}
        text = proposer.describe(finding)
        self.assertNotIn("secret", text)
        self.assertNotIn("deadbeef", text)
        self.assertEqual(set(json.loads(text)), {"rule", "count", "scanned", "summary"})

    def test_unknown_backend_is_rejected(self):
        with self.assertRaises(ValueError):
            proposer.local_model_proposer(backend="cuda")


if __name__ == "__main__":
    unittest.main()
