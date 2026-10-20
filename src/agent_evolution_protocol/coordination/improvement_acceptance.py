"""Reproducible isolated workflow exercise, explicitly not multi-Agent acceptance."""
import json
from pathlib import Path
from uuid import uuid4

from . import improvement, improvement_policy, improvement_spool, improvement_work
from .battle_orders import _atomic_json


def run(root):
    folder = Path(root).resolve() / "acceptance_runs" / uuid4().hex
    folder.mkdir(parents=True)
    coord = folder / "coordination"
    workspace = folder / "workspace"
    (workspace / "tests").mkdir(parents=True)
    report_path = folder / "acceptance-report.json"
    report = {"schema_version": 1, "status": "FAILED", "run_directory": str(folder),
              "checks": [], "actual_test_process": False, "scripted_roles": True,
              "independent_agent_review": False, "desktop_delivery_verified": False,
              "agent_ack_verified": False, "deployment_performed": False,
              "security_sandbox": False}

    def record(name, passed):
        report["checks"].append({"name": name, "passed": bool(passed)})
        if not passed:
            raise ValueError(f"acceptance check failed: {name}")

    try:
        fixture = workspace / "tests" / "test_fixture.py"
        fixture.write_text("import unittest\nclass Fixture(unittest.TestCase):\n"
                           "    def test_sum(self):\n        self.assertEqual(sum([1, 2, 3]), 6)\n", encoding="utf-8")
        proof = folder / "baseline.txt"
        proof.write_text("Synthetic acceptance fixture; not a production optimization result.\n", encoding="utf-8")
        event_file = folder / "completion.json"
        _atomic_json(event_file, {"event_id": "acceptance-" + folder.name, "kind": "TASK_COMPLETED",
            "improvement": {"author": "fixture-author", "change": "exercise workflow only",
                "acceptance": "one real fixture test passes; stage gates remain active",
                "scope": "isolated generated workspace", "rollback": "record scripted rollback",
                "evidence": [str(proof)]}})
        improvement_policy.configure(coord, mode="enforced", actor="fixture-operator", note="isolated exercise")
        improvement_spool.enqueue(coord, event_file)
        consumed = improvement_spool.drain(coord)
        record("completion_event_consumed", consumed["processed"] == 1 and consumed["results"][0]["status"] == "PROPOSED")
        identity = consumed["results"][0]["id"]
        record("replay_is_idempotent", improvement_spool.drain(coord)["processed"] == 0)
        try:
            improvement.advance(coord, identity, state="VALIDATED", actor="fixture-validator",
                                evidence=[str(proof)], note="invalid bypass")
        except ValueError:
            record("unleased_promotion_blocked", True)
        else:
            record("unleased_promotion_blocked", False)
        lease = improvement_work.claim(coord, identity, actor="fixture-validator")
        record("brief_evidence_verified", not improvement_work.brief(coord, identity,
               actor="fixture-validator", token=lease["token"])["execution_started"])
        tested = improvement.validate_tests(coord, identity, workspace=workspace, actor="fixture-validator",
                                            token=lease["token"], timeout=30)
        report["actual_test_process"] = True
        report["test_report"] = tested["report"]
        record("real_test_promoted", tested["status"] == "PASSED" and tested["tests_run"] == 1
               and tested["proposal"]["state"] == "VALIDATED")
        try:
            improvement_work.claim(coord, identity, actor="fixture-validator")
        except ValueError:
            record("self_review_blocked", True)
        else:
            record("self_review_blocked", False)
        for actor, expected in (("fixture-reviewer", "REVIEWED"), ("fixture-trial", "TRIAL"),
                                ("fixture-assessor", "ADOPTED")):
            lease = improvement_work.claim(coord, identity, actor=actor)
            evidence = folder / f"{expected.lower()}.txt"
            evidence.write_text(f"Scripted {expected} record; no independent Agent or deployment.\n", encoding="utf-8")
            result = improvement_work.finish(coord, identity, actor=actor, token=lease["token"],
                                             evidence=[str(evidence)], note="scripted API contract exercise")
            record("scripted_" + expected.lower(), result["state"] == expected)
        final = improvement.advance(coord, identity, state="ROLLED_BACK", actor="fixture-operator",
                                    evidence=[str(proof)], note="scripted rollback record, no deployment")
        record("rollback_recorded", final["state"] == "ROLLED_BACK")
        record("journal_chain_readable", len(final["events"]) == 6)
        report.update(status="PASSED", proposal_id=identity)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report["error"] = str(exc)
    report["report"] = str(report_path)
    _atomic_json(report_path, report)
    return report
