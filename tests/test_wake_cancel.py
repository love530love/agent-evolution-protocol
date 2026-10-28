import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent_evolution_protocol import cli


class WakeCancelTests(unittest.TestCase):
    def test_cancels_only_unconsumed_queue_entry(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            event_id = "20261028T134900000000Z-a1b2c3d4"
            queue = root / "wake_queue"
            queue.mkdir()
            (queue / f"{event_id}.json").write_text(json.dumps({"id": event_id, "type": "wake_request", "to": "qoder"}), encoding="utf-8")
            args = cli.build_parser().parse_args(["--coord-root", str(root), "coord-wake-cancel", "--id", event_id, "--actor", "codex", "--reason", "stale route"])
            output = StringIO()
            with redirect_stdout(output):
                args.func(args)
            result = json.loads(output.getvalue())
            self.assertEqual(result["state"], "CANCELLED_BEFORE_CONSUMPTION")
            self.assertFalse((queue / f"{event_id}.json").exists())
            failed = json.loads((root / "wake_failed" / f"{event_id}.json").read_text(encoding="utf-8"))
            self.assertTrue(failed["cancelled"])
            self.assertFalse(result["adapter_executed"])

    def test_refuses_pending_execution(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            event_id = "20261028T134900000000Z-a1b2c3d5"
            pending = root / "wake_pending"
            pending.mkdir()
            (pending / f"{event_id}.json").write_text("{}", encoding="utf-8")
            args = cli.build_parser().parse_args(["--coord-root", str(root), "coord-wake-cancel", "--id", event_id, "--actor", "codex", "--reason", "wrong"])
            with self.assertRaises(cli.ProtocolError):
                args.func(args)


if __name__ == "__main__":
    unittest.main()
