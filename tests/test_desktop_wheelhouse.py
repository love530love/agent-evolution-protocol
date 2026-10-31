"""Offline fixture tests; no dependency downloads or package execution."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
# Interop is not Windows: WSL resolves pwsh.exe from the Windows PATH, but a
# Windows shell cannot use a /tmp path, so every test below would fail for a
# reason unrelated to the wheelhouse.
PWSH = (shutil.which("pwsh.exe") or shutil.which("pwsh")) if os.name == "nt" else None


@unittest.skipUnless(PWSH, "PowerShell 7 not installed")
class DesktopWheelhouseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="aep wheels ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ("scripts/prepare-desktop-wheelhouse.ps1", "scripts/wheelhouse-common.ps1",
                     "scripts/bootstrap-desktop.ps1", "aep-desktop.ps1", "runtime/desktop-win-cp313.lock.json"):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, target)
        self.lock_path = self.root / "runtime/desktop-win-cp313.lock.json"
        self.lock = json.loads(self.lock_path.read_text())
        self.lock["wheels"] = [row for row in self.lock["wheels"] if row["name"] == "mcp"]
        self.wheel = self.lock["wheels"][0]
        self.wheel["sha256"] = hashlib.sha256(b"fixture-not-executable").hexdigest()
        self.save()

    def save(self):
        self.lock_path.write_text(json.dumps(self.lock))

    def run_script(self, name="scripts/prepare-desktop-wheelhouse.ps1", *args):
        return subprocess.run([PWSH, "-NoProfile", "-File", str(self.root / name), *args],
                              capture_output=True, text=True, encoding="utf-8", timeout=30)

    def test_default_plan_and_missing_sdk_do_not_create_files(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "PLAN_ONLY")
        self.assertFalse((self.root / ".tools").exists())
        self.assertNotEqual(self.run_script("aep-desktop.ps1", "--help").returncode, 0)
        self.assertFalse((self.root / ".tools").exists())

    def test_verify_is_read_only_and_rejects_missing_changed_and_extra(self):
        directory = self.root / "offline bundle"
        directory.mkdir()
        args = ("-Action", "Verify", "-Directory", str(directory))
        self.assertNotEqual(self.run_script("scripts/prepare-desktop-wheelhouse.ps1", *args).returncode, 0)
        artifact = directory / self.wheel["filename"]
        artifact.write_bytes(b"fixture-not-executable")
        good = self.run_script("scripts/prepare-desktop-wheelhouse.ps1", *args)
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertFalse(json.loads(good.stdout)["packages_imported"])
        extra = directory / "extra.whl"
        extra.write_bytes(b"extra")
        self.assertNotEqual(self.run_script("scripts/prepare-desktop-wheelhouse.ps1", *args).returncode, 0)
        extra.unlink()
        artifact.write_bytes(b"tampered")
        self.assertNotEqual(self.run_script("scripts/prepare-desktop-wheelhouse.ps1", *args).returncode, 0)

    def test_malformed_lock_rejected_before_download_or_directory_creation(self):
        for change in ({"filename": "../escape.whl"}, {"url": "http://example.com/payload.whl"}):
            original = self.wheel.copy()
            self.wheel.update(change)
            self.save()
            self.assertNotEqual(self.run_script().returncode, 0)
            self.assertFalse((self.root / ".tools").exists())
            self.wheel.clear()
            self.wheel.update(original)
        self.lock["wheels"].append(self.wheel.copy())
        self.save()
        self.assertNotEqual(self.run_script().returncode, 0)

    def test_explicit_candidate_lock_does_not_replace_default(self):
        directory = self.root / "candidate wheels"
        directory.mkdir()
        (directory / self.wheel["filename"]).write_bytes(b"fixture-not-executable")
        candidate_lock = self.root / "candidate.lock.json"
        candidate_lock.write_text(json.dumps(self.lock))
        self.lock_path.write_text("{}")
        result = self.run_script("scripts/prepare-desktop-wheelhouse.ps1", "-Action", "Verify",
                                 "-Directory", str(directory), "-LockFile", str(candidate_lock))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.lock_path.read_text(), "{}")

    def test_installer_whatif_and_bad_bundle_never_create_environment(self):
        target = self.root / "sdk"
        preview = self.run_script("scripts/bootstrap-desktop.ps1", "-Python", sys.executable,
                                  "-EnvironmentPath", str(target), "-Install", "-WhatIf")
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertFalse(target.exists())
        empty = self.root / "empty wheels"
        empty.mkdir()
        rejected = self.run_script("scripts/bootstrap-desktop.ps1", "-Python", sys.executable,
                                   "-EnvironmentPath", str(target), "-Wheelhouse", str(empty), "-Install")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertFalse(target.exists())
