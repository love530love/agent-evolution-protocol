"""The resident loop has to honour what it was told, and say that it ran.

Both tests here are regressions for defects measured on 2026-11-02, and both had
the same shape: the daemon looked alive while doing nothing.

* ``--no-perceive`` stopped at the CLI.  ``watch`` called ``tick(root)`` and the
  flag never reached it, so a process asked to skip perception ran it anyway and
  blocked inside the first tick -- ten minutes, no order touched, no log line.
* The loop printed nothing, so an empty log was indistinguishable from a healthy
  quiet one.
"""

import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from agent_evolution_protocol.cli import build_parser
from agent_evolution_protocol.coordination import commander


class WatchLoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_no_perceive_reaches_the_loop(self):
        stream = io.StringIO()
        commander.watch(self.root, interval=1, max_ticks=1,
                        perceive_first=False, stream=stream)
        lines = stream.getvalue().strip().splitlines()
        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertFalse(payload["perceived"])
        self.assertEqual(payload["tick"], 1)

    def test_one_line_per_tick(self):
        stream = io.StringIO()
        commander.watch(self.root, interval=1, max_ticks=3,
                        perceive_first=False, stream=stream)
        payloads = [json.loads(line) for line in stream.getvalue().strip().splitlines()]
        self.assertEqual([item["tick"] for item in payloads], [1, 2, 3])
        for item in payloads:
            self.assertIn("at", item)
            self.assertEqual(item["dispatched"], [])

    def test_the_stream_is_optional(self):
        # A caller that wants no output must not have to invent a sink.
        self.assertEqual(commander.watch(self.root, interval=1, max_ticks=1,
                                         perceive_first=False), 0)


class CliWiringTests(unittest.TestCase):
    """The flag has to survive the trip from argv to the loop."""

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def _run(self, argv):
        args = build_parser().parse_args(["--coord-root", str(self.root)] + argv)
        with mock.patch.object(commander, "watch", return_value=0) as fake:
            with self.assertRaises(SystemExit):
                args.func(args)
        return fake.call_args

    def test_watch_skips_perception_when_asked(self):
        call = self._run(["coord-commander", "watch", "--no-perceive", "--max-ticks", "1"])
        self.assertFalse(call.kwargs["perceive_first"])
        self.assertEqual(call.kwargs["max_ticks"], 1)

    def test_watch_perceives_by_default(self):
        call = self._run(["coord-commander", "watch", "--max-ticks", "1"])
        self.assertTrue(call.kwargs["perceive_first"])

    def test_the_loop_is_given_something_to_print_to(self):
        call = self._run(["coord-commander", "watch", "--no-perceive", "--max-ticks", "1"])
        self.assertIsNotNone(call.kwargs["stream"])

    def test_tick_still_honours_the_flag_on_its_own(self):
        # The same flag on the one-shot action must keep working; the bug was
        # only in the loop, and a fix that broke this would be a regression.
        args = build_parser().parse_args(
            ["--coord-root", str(self.root), "coord-commander", "tick", "--no-perceive"])
        with mock.patch.object(commander, "tick", return_value={"at": "", "dispatched": []}) as fake:
            args.func(args)
        self.assertFalse(fake.call_args.kwargs["perceive_first"])


if __name__ == "__main__":
    unittest.main()
