import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from agent_evolution_protocol import cli


class JoinCliTest(unittest.TestCase):
    def test_join_packet_for_new_agent_and_task(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            coord = root / "coord"
            kernel = root / "kernel"
            cli.coord_bootstrap(SimpleNamespace(coord_root=coord))
            cli.coord_send(SimpleNamespace(
                coord_root=coord,
                sender="codex",
                to="hermes_desktop",
                topic="T125",
                kind="context",
                text="read first",
                text_file=None,
            ))

            out = StringIO()
            with redirect_stdout(out):
                cli.join_agent(SimpleNamespace(
                    coord_root=coord,
                    kernel_root=kernel,
                    agent="hermes_desktop",
                    task="T125",
                    workspace=str(root),
                    requested_session_id="",
                    allow_new_session=False,
                    markdown=False,
                ))
            packet = json.loads(out.getvalue())
            self.assertEqual("hermes_desktop", packet["agent"])
            self.assertEqual("T125", packet["task"])
            self.assertEqual(1, packet["unread_count"])
            self.assertEqual("BLOCKED_HANDOFF_REQUIRED", packet["continuation_plan"]["action"])
            self.assertTrue(packet["claim_guidance"]["claim_available"])

            out = StringIO()
            with redirect_stdout(out):
                cli.join_agent(SimpleNamespace(
                    coord_root=coord,
                    kernel_root=kernel,
                    agent="hermes_desktop",
                    task="T125",
                    workspace=str(root),
                    requested_session_id="session-1",
                    allow_new_session=False,
                    markdown=True,
                ))
            self.assertIn("Agent Join Packet", out.getvalue())


if __name__ == "__main__":
    unittest.main()
