"""Installer contract tests; never clone, compile or send desktop input."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == "win32" and shutil.which("pwsh.exe"), "Windows PowerShell 7 required")
class DesktopInstallerTests(unittest.TestCase):
    def test_plan_and_whatif_have_no_install_side_effect(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "install-desktop-provider.ps1"
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "providers"
            base = [shutil.which("pwsh.exe"), "-NoProfile", "-File", str(script),
                    "-SdkPython", sys.executable, "-InstallRoot", str(target)]
            plan = subprocess.run(base, capture_output=True, text=True, encoding="utf-8", timeout=30)
            # A workstation with no dotnet/git cannot satisfy even discovery preflight.
            if not shutil.which("dotnet.exe") or not shutil.which("git.exe"):
                self.skipTest("dotnet and git discovery required")
            self.assertEqual(plan.returncode, 0, plan.stderr)
            self.assertFalse(json.loads(plan.stdout)["install"])
            self.assertFalse(target.exists())
            preview = subprocess.run(base + ["-Install", "-WhatIf"], capture_output=True,
                                     text=True, encoding="utf-8", timeout=30)
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertFalse(target.exists())
