"""PowerShell launcher tests with isolated fixtures; never installs from network."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
# Finding pwsh.exe is not the same as being Windows: WSL puts the Windows PATH
# on Linux, so these tests would launch a Windows shell that cannot read a
# /tmp path and fail for a reason that has nothing to do with the installer.
PWSH = (shutil.which("pwsh.exe") or shutil.which("pwsh")) if os.name == "nt" else None


@unittest.skipUnless(PWSH, "PowerShell 7 not installed")
class PortableRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="aep portable ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ("aep.ps1", "runtime/python-runtime.json", "scripts/runtime-common.ps1",
                     "scripts/install-portable-python.ps1"):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, target)
        self.spec = json.loads((self.root / "runtime/python-runtime.json").read_text())

    def run_script(self, name, *args):
        return subprocess.run([PWSH, "-NoProfile", "-File", str(self.root / name), *args],
                              capture_output=True, text=True, encoding="utf-8", timeout=30)

    def test_plan_creates_no_environment_and_missing_doctor_fails(self):
        plan = self.run_script("scripts/install-portable-python.ps1")
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertEqual(json.loads(plan.stdout)["status"], "PLAN_ONLY")
        self.assertFalse((self.root / ".tools").exists())
        doctor = self.run_script("aep.ps1", "--runtime-doctor")
        self.assertEqual(doctor.returncode, 2)
        self.assertEqual(json.loads(doctor.stdout)["status"], "UNAVAILABLE")
        self.assertFalse((self.root / ".tools").exists())

    def test_integrity_rejects_changed_and_extra_files_without_execution(self):
        runtime = self.root / ".tools/portable-python" / self.spec["directory"]
        runtime.mkdir(parents=True)
        files = []
        for name in ("python.exe", self.spec["pth"], self.spec["stdlib"]):
            content = b"not executable; doctor must never run this"
            (runtime / name).write_bytes(content)
            files.append({"name": name, "sha256": hashlib.sha256(content).hexdigest()})
        (runtime / "aep-runtime.json").write_text(json.dumps({
            "version": self.spec["version"], "archive_sha256": self.spec["sha256"], "files": files
        }))
        self.assertEqual(json.loads(self.run_script("aep.ps1", "--runtime-doctor").stdout)["status"], "VERIFIED")
        (runtime / "extra.dll").write_bytes(b"extra")
        self.assertEqual(self.run_script("aep.ps1", "--runtime-doctor").returncode, 2)
        (runtime / "extra.dll").unlink()
        (runtime / "python.exe").write_bytes(b"changed")
        self.assertEqual(self.run_script("aep.ps1", "--runtime-doctor").returncode, 2)

    @unittest.skipUnless(__import__("os").name == "nt", "Windows install profile")
    def test_bad_offline_archive_is_rejected_before_extraction(self):
        archive = self.root / "bad.zip"
        archive.write_bytes(b"not the pinned runtime")
        result = self.run_script("scripts/install-portable-python.ps1", "-Install", "-Archive", str(archive))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA256 mismatch", result.stderr)
        self.assertFalse((self.root / ".tools/portable-python" / self.spec["directory"]).exists())
