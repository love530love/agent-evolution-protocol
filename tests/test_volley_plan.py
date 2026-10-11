import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from agent_evolution_protocol.coordination.volley_plan import plan


def package(root: Path, name: str) -> tuple[str, str]:
    path = root / name
    with ZipFile(path, "w") as archive:
        archive.writestr("kernel.py", name)
    return str(path), hashlib.sha256(path.read_bytes()).hexdigest()


def candidate(cid: str, task: str, agent: str, path: str, sha: str) -> dict:
    return {"candidate_id": cid, "task": task, "agent": agent, "package": path, "sha256": sha,
            "priority": 5, "rule_review": "rules.md", "local_gate": "gate.log",
            "cross_platform_review": "review.md", "package_review": "zip-check.json",
            "independent_reviewer": "codex" if agent != "codex" else "qoder"}


class VolleyPlanTest(unittest.TestCase):
    def test_repeat_failure_ceasefire_and_other_task_continues(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            p1, s1 = package(root, "a.zip")
            p2, s2 = package(root, "b.zip")
            manifest = root / "manifest.json"
            results = root / "results.json"
            manifest.write_text(json.dumps([candidate("T140-Q-R2", "T140", "qoder", p1, s1),
                                            candidate("T141-W-R2", "T141", "workbuddy", p2, s2)]), encoding="utf-8")
            results.write_text(json.dumps([
                {"task": "T140", "submission_id": "100", "chip": "kunlun", "status": "failed", "failure_signature": "MLIR pass failed"},
                {"task": "T140", "submission_id": "101", "chip": "kunlun", "status": "failed", "failure_signature": " mlir  pass failed "},
            ]), encoding="utf-8")
            report = plan(manifest, results, quota_remaining=3, reserve=1, wave_size=2)
            self.assertEqual("T141-W-R2", report["waves"][0]["candidates"][0]["candidate_id"])
            self.assertEqual("T140-Q-R2", report["held"][0]["candidate_id"])
            self.assertIn("repeat_failure_ceasefire", report["held"][0]["reasons"])
            self.assertEqual(1, len(report["ceasefires"]))

    def test_duplicate_package_is_not_second_round_ammo(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, sha = package(root, "same.zip")
            manifest = root / "m.json"
            results = root / "r.json"
            manifest.write_text(json.dumps([candidate("one", "T130", "qoder", path, sha),
                                            candidate("two", "T131", "workbuddy", path, sha)]), encoding="utf-8")
            results.write_text("[]", encoding="utf-8")
            report = plan(manifest, results, quota_remaining=2)
            self.assertEqual(1, len(report["waves"][0]["candidates"]))
            self.assertIn("duplicate_package_sha", report["held"][0]["reasons"])

    def test_same_task_second_candidate_waits_for_return(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            a, sa = package(root, "a.zip")
            b, sb = package(root, "b.zip")
            manifest = root / "m.json"
            results = root / "r.json"
            manifest.write_text(json.dumps([candidate("T140-Q", "T140", "qoder", a, sa),
                                            candidate("T140-W", "T140", "workbuddy", b, sb)]), encoding="utf-8")
            results.write_text("[]", encoding="utf-8")
            report = plan(manifest, results, quota_remaining=4, wave_size=1)
            self.assertEqual(1, sum(len(wave["candidates"]) for wave in report["waves"]))
            self.assertEqual(1, len(report["reload_wait"]))
