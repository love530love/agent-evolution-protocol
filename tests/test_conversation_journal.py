import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from agent_evolution_protocol import cli
from agent_evolution_protocol.coordination.conversation_journal import (
    acknowledge, append_event, cursor_for, read_events,
)


class ConversationJournalTests(unittest.TestCase):
    def test_full_local_event_survives_without_hub_and_cursor_ack(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = {"prompt": "不要重复提交", "extra": {"tool_details": "original content"}}
            first = append_event(root, room="contest", actor="user", source="workbuddy",
                                 kind="message", text=raw["prompt"], session_id="s1",
                                 cwd=temporary, raw_hook=raw)
            second = append_event(root, room="contest", actor="workbuddy", source="workbuddy",
                                  kind="result", text="收到", session_id="s1", cwd=temporary)
            self.assertEqual([first["id"], second["id"]], [e["id"] for e in read_events(root)])
            self.assertEqual(raw, read_events(root, include_raw=True)[0]["raw_hook"])
            self.assertNotIn("raw_hook", read_events(root)[0])
            acknowledge(root, agent="qoder", through=first["id"])
            self.assertEqual(first["id"], cursor_for(root, "qoder"))
            self.assertEqual([second["id"]], [e["id"] for e in read_events(root, after=cursor_for(root, "qoder"))])
            acknowledge(root, agent="qoder", through=second["id"])
            with self.assertRaisesRegex(ValueError, "backwards"):
                acknowledge(root, agent="qoder", through=first["id"])

    def test_cli_publish_read_ack(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with redirect_stdout(StringIO()):
                cli.coord_chat_publish(SimpleNamespace(coord_root=root, source="codex", role="user",
                    room="contest", session_id="s2", text="@all 新评测规则", text_file=None,
                    workspace=temporary))
            output = StringIO()
            with redirect_stdout(output):
                cli.coord_chat_read(SimpleNamespace(coord_root=root, agent="qoder", after=None,
                    limit=50, include_raw=False))
            packet = json.loads(output.getvalue())
            self.assertEqual("@all 新评测规则", packet["events"][0]["text"])
            with redirect_stdout(StringIO()):
                cli.coord_chat_ack(SimpleNamespace(coord_root=root, agent="qoder", through=packet["next_cursor"]))
            with redirect_stdout(output := StringIO()):
                cli.coord_chat_read(SimpleNamespace(coord_root=root, agent="qoder", after=None,
                    limit=50, include_raw=False))
            self.assertEqual([], json.loads(output.getvalue())["events"])


if __name__ == "__main__":
    unittest.main()
