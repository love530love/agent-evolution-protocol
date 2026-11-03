"""Web Feishu is not a collaboration surface, and the base has to know that.

The rule arrived as a message: "网页版飞书不在此次协作 agent 的范围内，请不要发送飞书的
测试消息".  A rule that only exists in a conversation is a rule the next session
does not know about, so it lives in the client roster as ``out_of_scope`` and is
enforced in three places:

* ``discover`` reports the refusal instead of pretending the window is usable;
* ``perceive`` refuses to publish the tab into the shared transcript bus, because
  perception is read-only but not harmless -- it copies an unrelated human
  conversation into every agent's context;
* ``dispatch`` refuses to type into it, and a later ``chrome.json`` registration
  cannot clear the refusal by shadowing the catalogue entry.

The last one is the interesting one: ``profile()`` returns a registered file
verbatim, so before this change anyone could register chrome and put a live
person-to-person chat back into service.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agent_evolution_protocol.coordination import client_registry, commander

FEISHU_WINDOW = {
    "hwnd": 4242,
    "pid": 9999,
    "title": "消息 - 飞书 (6) - Google Chrome",
    "exe": r"D:\Program Files\Google\Chrome\Application\chrome.exe",
}


class CatalogueTests(unittest.TestCase):
    def test_chrome_is_out_of_scope_and_says_why(self):
        reason = client_registry.BUILTIN["chrome"].get("out_of_scope", "")
        self.assertTrue(reason.strip(), "chrome must carry a refusal, not just a transport list")
        self.assertIn("Feishu", reason)

    def test_ordinary_clients_are_in_scope(self):
        # Only the browser tab is out of scope.  A refusal that quietly spread
        # to the real clients would be indistinguishable from a broken base.
        for name in ("qoder", "qoder_cn", "chatgpt", "workbuddy"):
            with self.subTest(agent=name):
                self.assertFalse(client_registry.BUILTIN[name].get("out_of_scope"))

    def test_a_registered_profile_cannot_clear_the_refusal(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            clients = root / "clients"
            clients.mkdir()
            # A registration that says nothing about scope -- the common case,
            # and the one that used to silently re-enable the tab.
            (clients / "chrome.json").write_text(json.dumps({
                "agent": "chrome", "display": "Chrome", "process": "chrome",
                "window_titles": ["Chrome"], "transports": ["uia"],
            }), encoding="utf-8")
            row = client_registry.profile(root, "chrome")
            self.assertEqual(row["source"], "registered")
            self.assertIn("out_of_scope", row)

    def test_a_registration_cannot_overwrite_the_reason_either(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            clients = root / "clients"
            clients.mkdir()
            (clients / "chrome.json").write_text(json.dumps({
                "agent": "chrome", "display": "Chrome", "process": "chrome",
                "window_titles": ["Chrome"], "out_of_scope": "",
            }), encoding="utf-8")
            self.assertTrue(client_registry.profile(root, "chrome")["out_of_scope"].strip())


class DiscoverTests(unittest.TestCase):
    def test_discover_reports_the_refusal(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(client_registry, "visible_windows",
                                   return_value=[dict(FEISHU_WINDOW)]):
                found = client_registry.discover(root, "chrome")
            row = next(item for item in found["clients"] if item["agent"] == "chrome")
            self.assertTrue(row["out_of_scope"].strip())


class PerceiveTests(unittest.TestCase):
    def test_perceive_refuses_to_publish_the_tab(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            discovery = {"clients": [{
                "agent": "chrome", "windows": [dict(FEISHU_WINDOW)],
                "unique": True, "anchored": False,
                "out_of_scope": client_registry.BUILTIN["chrome"]["out_of_scope"],
            }]}
            with mock.patch.object(commander.registry, "discover", return_value=discovery):
                result = commander.perceive(root)
            entry = result["perceived"][0]
            self.assertEqual(entry["status"], "OUT_OF_SCOPE")
            self.assertFalse(entry["published"])
            # And nothing reached the shared bus.
            snapshots = root / "transcript_bus" / "snapshots"
            self.assertFalse((snapshots / "chrome.json").exists())


class DispatchTests(unittest.TestCase):
    def test_dispatch_refuses_an_out_of_scope_target_without_touching_the_desktop(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            order = commander.submit_order(
                root, target="chrome", task="AEP-FEISHU-GUARD",
                text="this must never be typed into a live chat",
                sender="workbuddyai")
            with mock.patch.object(commander.registry, "discover") as discovery:
                result = commander.dispatch(root, order=order)
            self.assertEqual(result["state"], "FAILED")
            self.assertIn("OUT_OF_SCOPE_TARGET", result["attempts"][-1]["note"])
            discovery.assert_not_called()


if __name__ == "__main__":
    unittest.main()
