import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class CollaborationTests(unittest.TestCase):
    def test_post_executes_http_request(self):
        hook = load("realtime_hook_post_test", "realtime_hook.py")
        response = mock.MagicMock()
        response.__enter__.return_value.status = 201
        response.__exit__.return_value = False
        with tempfile.TemporaryDirectory() as temp:
            token = Path(temp) / "token.txt"
            token.write_text("x" * 43, encoding="utf-8")
            with mock.patch.object(hook, "TOKEN_FILE", token), mock.patch.object(hook, "urlopen", return_value=response) as opened:
                self.assertTrue(hook.post("/api/event", {"text": "test"}))
                opened.assert_called_once()
                request = opened.call_args.args[0]
                self.assertEqual(request.full_url, "http://127.0.0.1:8765/api/event")
                self.assertEqual(request.method, "POST")

    def test_wake_phrase_is_exact(self):
        hook = load("realtime_hook_test", "realtime_hook.py")
        self.assertIsNone(hook.WAKE_RE.match("please wake workbuddy later"))
        match = hook.WAKE_RE.match("@唤醒 workbuddy: 检查当前任务")
        self.assertEqual(match.groups(), ("workbuddy", "检查当前任务"))

    def test_ordinary_prompt_never_requests_execution(self):
        hook = load("realtime_hook_prompt_test", "realtime_hook.py")
        calls = []
        payload = json.dumps({"prompt": "ordinary message", "cwd": str(HERE)})
        with mock.patch.object(hook, "post", side_effect=lambda path, body: calls.append((path, body)) or True), mock.patch.object(
            hook.sys, "stdin", io.StringIO(payload)
        ), mock.patch.object(hook.sys, "argv", ["hook", "--agent", "codex", "--event", "prompt"]):
            self.assertEqual(hook.main(), 0)
        self.assertEqual([path for path, _ in calls], ["/api/event"])

    def test_exact_wake_requests_one_execution(self):
        hook = load("realtime_hook_wake_test", "realtime_hook.py")
        calls = []
        payload = json.dumps({"extra": {"user_message": "@wake codex: run tests"}, "cwd": str(HERE)})
        with mock.patch.object(hook, "post", side_effect=lambda path, body: calls.append((path, body)) or True), mock.patch.object(
            hook.sys, "stdin", io.StringIO(payload)
        ), mock.patch.object(hook.sys, "argv", ["hook", "--agent", "workbuddy", "--event", "prompt"]):
            self.assertEqual(hook.main(), 0)
        self.assertEqual([path for path, _ in calls], ["/api/event", "/api/wake"])
        self.assertTrue(calls[-1][1]["execute"])

    def test_worker_is_only_spawned_for_execute_true(self):
        hub = load("realtime_hub_test", "realtime_hub.py")
        with tempfile.TemporaryDirectory() as temp:
            hub.EVENTS = Path(temp) / "events.jsonl"
            hub.STORE = hub.EventStore()
            with mock.patch.object(hub, "launch_worker") as launch:
                data = {"room": "test", "actor": "user", "kind": "wake", "target": "codex", "text": "task"}
                event = hub.STORE.append(hub.validate_event(data))
                self.assertEqual(event["kind"], "wake")
                launch.assert_not_called()

    def test_out_of_scope_prompt_is_ignored(self):
        hook = load("realtime_hook_scope_test", "realtime_hook.py")
        calls = []
        payload = json.dumps({"prompt": "private other project", "cwd": "C:/unrelated"})
        with mock.patch.object(hook, "post", side_effect=lambda path, body: calls.append((path, body)) or True), mock.patch.object(
            hook.sys, "stdin", io.StringIO(payload)
        ), mock.patch.object(hook.sys, "argv", ["hook", "--agent", "codex", "--event", "prompt"]):
            self.assertEqual(hook.main(), 0)
        self.assertEqual(calls, [])

    def test_disconnected_client_is_handled_without_traceback(self):
        hub = load("realtime_hub_disconnect_test", "realtime_hub.py")
        handler = object.__new__(hub.Handler)
        for error in (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            with self.subTest(error=error), mock.patch.object(hub.BaseHTTPRequestHandler, "handle", side_effect=error):
                handler.close_connection = False
                handler.handle()
                self.assertTrue(handler.close_connection)

    def test_health_does_not_expose_token(self):
        hub = load("realtime_hub_health_test", "realtime_hub.py")
        handler = object.__new__(hub.Handler)
        handler.path = "/api/health"
        with mock.patch.object(handler, "send_json") as send:
            handler.do_GET()
        status, payload = send.call_args.args
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertNotIn("token", payload)

    def test_resume_recovers_more_than_snapshot_limit_in_append_order(self):
        hub = load("realtime_hub_resume_test", "realtime_hub.py")
        with tempfile.TemporaryDirectory() as temp:
            hub.EVENTS = Path(temp) / "events.jsonl"
            store = hub.EventStore()
            store.events = [{"id": "z", "room": "test"}] + [
                {"id": str(index), "room": "test"} for index in range(600)
            ]
            self.assertEqual(len(store.snapshot("test")), 500)
            pending = store.pending("test", "z")
            self.assertEqual(len(pending), 600)
            self.assertEqual(pending[0]["id"], "0")
            self.assertEqual(len(store.pending("test", "missing")), 601)


if __name__ == "__main__":
    unittest.main()
