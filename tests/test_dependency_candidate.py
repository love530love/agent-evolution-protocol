import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from agent_evolution_protocol.coordination import dependency_candidate as candidate


class DependencyCandidateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.wheels = self.root / "wheels"
        self.wheels.mkdir()
        self.wheel = self.make_wheel("mcp", "1.30.0")
        self.baseline = self.root / "baseline.json"
        self.base = {"schema": 1, "profile": "desktop-win-cp313", "python": "3.13",
                     "platform": "win_amd64", "root": "mcp==1.30.0", "source": "https://pypi.org",
                     "wheels": [self.artifact(self.wheel, "mcp", "1.30.0")]}
        self.baseline.write_text(json.dumps(self.base))
        self.output = self.root / "candidate"

    def make_wheel(self, name, version, *, metadata_name=None, platform="any", duplicate=False):
        path = self.wheels / f"{name}-{version}-py3-none-{platform}.whl"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(f"{name}-{version}.dist-info/METADATA",
                f"Metadata-Version: 2.4\nName: {metadata_name or name}\nVersion: {version}\nLicense-Expression: MIT\nRequires-Dist: example>=1\n\n")
            archive.writestr("payload.py", "raise RuntimeError('MUST NEVER EXECUTE')")
            if duplicate:
                archive.writestr("other-1.0.dist-info/METADATA", "Name: other\nVersion: 1.0\n")
        return path

    def artifact(self, path, name, version):
        return {"name": name, "version": version, "filename": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "url": "https://files.pythonhosted.org/packages/aa/bb/" + path.name}

    def build(self, **kwargs):
        return candidate.build(baseline=self.baseline, wheelhouse=self.wheels, output=self.output, **kwargs)

    def test_plan_does_not_read_download_write_or_execute(self):
        with patch.object(candidate, "_fetch") as fetch:
            result = self.build(verify_pypi=True)
            fetch.assert_not_called()
        self.assertEqual(result["status"], "PLAN_ONLY")
        self.assertFalse(result["network_performed"])
        self.assertFalse(self.output.exists())

    def test_offline_baseline_reproduction_is_candidate_not_adoption(self):
        original = self.baseline.read_bytes()
        with patch.object(candidate, "_fetch") as fetch:
            result = self.build(apply=True)
            fetch.assert_not_called()
        self.assertFalse(result["installed"])
        self.assertFalse(result["adopted"])
        self.assertFalse(result["dependency_closure_verified"])
        self.assertEqual(result["changed"], [])
        self.assertEqual(result["declarations"][0]["license_expression"], "MIT")
        self.assertEqual(result["candidate_sha256"], hashlib.sha256((self.output / "candidate.lock.json").read_bytes()).hexdigest())
        self.assertEqual(self.baseline.read_bytes(), original)
        with self.assertRaises(ValueError):
            self.build(apply=True)

    def test_changed_artifact_needs_opt_in_and_verified_digest(self):
        extra = self.make_wheel("extra", "1.0")
        with patch.object(candidate, "_fetch") as fetch, self.assertRaises(ValueError):
            self.build(apply=True)
        fetch.assert_not_called()
        self.assertFalse(self.output.exists())
        def verified(name, version, timeout):
            path = self.wheels / f"{name}-{version}-py3-none-any.whl"
            row = self.artifact(path, name, version)
            return {"urls": [{"filename": path.name, "digests": {"sha256": row["sha256"]}, "url": row["url"], "yanked": False}]}
        result = self.build(apply=True, verify_pypi=True, fetch=verified)
        self.assertEqual(result["added"], ["extra"])
        self.assertEqual(result["artifacts"][0]["verification"], "pypi-json-sha256")

    def test_bad_identity_duplicate_metadata_and_platform_rejected(self):
        for options in ({"metadata_name": "wrong"}, {"duplicate": True}, {"platform": "manylinux_2_28_x86_64"}):
            with self.subTest(options=options):
                path = self.make_wheel("bad", "1.0", **options)
                with self.assertRaises(ValueError):
                    self.build(apply=True)
                self.assertFalse(self.output.exists())
                path.unlink()

    def test_yanked_or_bad_remote_response_never_published(self):
        valid = {"filename": self.wheel.name, "digests": {"sha256": self.base["wheels"][0]["sha256"]},
                 "url": self.base["wheels"][0]["url"], "yanked": False}
        for response in ({"urls": []}, [], {"urls": [{**valid, "yanked": True}]},
                         {"urls": [{**valid, "url": "http://example.com/wheel.whl"}]},
                         {"urls": [{**valid, "digests": None}]}):
            with self.subTest(response=response), self.assertRaises(ValueError):
                self.build(apply=True, verify_pypi=True, fetch=lambda *args: response)
            self.assertFalse(self.output.exists())

    def test_compatible_native_abi_and_removed_dependency_are_visible(self):
        self.assertTrue(candidate._compatible("cp311", "abi3", "win_amd64"))
        self.assertFalse(candidate._compatible("cp314", "abi3", "win_amd64"))
        self.assertFalse(candidate._compatible("cp313", "cp313", "win_arm64"))
        self.base["wheels"].append({"name": "removed", "version": "1.0"})
        self.baseline.write_text(json.dumps(self.base))
        self.assertEqual(self.build(apply=True)["removed"], ["removed"])
