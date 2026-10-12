"""Windows-only event watcher smoke test; mailbox-only, no real agent wake."""

from datetime import timedelta
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import time
import unittest

from agent_evolution_protocol.coordination import campaign_cycle as cycle


@unittest.skipUnless(os.name == "nt" and shutil.which("pwsh.exe"), "PowerShell 7 on Windows required")
class CampaignWatcherIntegrationTests(unittest.TestCase):
    def test_file_event_reconciles_expired_assignment(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / "coordination"
            bridge = Path(folder) / "bridge"
            state = cycle.open_mission(root, bridge, task="T999", goal="isolated watcher test",
                lead="codex", deputy="workbuddy", cross_platform="qoder",
                catfish="hermes_desktop", reviewer="codebuddy")
            lead = next(item for item in state["assignments"] if item["role"] == "lead")
            script = Path(__file__).resolve().parents[1] / "scripts" / "campaign-watch.ps1"
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            watcher = subprocess.Popen(
                [shutil.which("pwsh.exe"), "-NoProfile", "-File", str(script),
                 "-CoordRoot", str(root), "-BridgeRoot", str(bridge), "-MailboxOnly"],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                env=environment, creationflags=flags)
            try:
                time.sleep(2)
                self.assertIsNone(watcher.poll(), "watcher exited before the event test")
                state = cycle.status(root, "T999")["missions"][0]
                current = next(item for item in state["assignments"] if item["id"] == lead["id"])
                current["ack_due_at"] = (cycle.now() - timedelta(seconds=1)).isoformat()
                cycle._atomic(cycle._state_path(root, "T999"), state)
                marker = cycle.event_id()
                cycle._write_once(root / "campaign" / "events" / f"{marker}.json",
                                  {"id": marker, "task": "T999", "kind": "TEST_TRIGGER"})
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    current = next(item for item in cycle.status(root, "T999")["missions"][0]["assignments"]
                                   if item["id"] == lead["id"])
                    if current["status"] == "timed_out":
                        break
                    if watcher.poll() is not None:
                        self.fail("watcher exited before processing the file event")
                    time.sleep(0.25)
                self.assertEqual(current["status"], "timed_out")
            finally:
                watcher.terminate()
                try:
                    watcher.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    watcher.kill()
                    watcher.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
