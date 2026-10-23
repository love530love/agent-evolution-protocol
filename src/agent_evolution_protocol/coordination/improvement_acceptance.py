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
        # Exercise evidence recovery using real failing and repaired test processes.
        failed_workspace = folder / "failure-workspace"
        (failed_workspace / "tests").mkdir(parents=True)
        failed_fixture = failed_workspace / "tests" / "test_recovery.py"
        failed_fixture.write_text("import unittest\nclass Recovery(unittest.TestCase):\n"
            "    def test_recovery(self):\n        self.fail('intentional acceptance fixture failure')\n", encoding="utf-8")
        failure_id = improvement.propose(coord, {"author": "fixture-author", "change": "failure handoff exercise",
            "acceptance": "successor reads frozen failure then explicitly validates a repair",
            "scope": "isolated failure-workspace", "rollback": "discard generated fixture",
            "evidence": [str(proof)]})["id"]
        first = improvement_work.claim(coord, failure_id, actor="fixture-first")
        failed = improvement.validate_tests(coord, failure_id, workspace=failed_workspace,
            actor="fixture-first", token=first["token"], timeout=30)
        record("real_failure_preserves_stage", failed["status"] == "FAILED" and failed["proposal"]["state"] == "PROPOSED")
        improvement_work.finish(coord, failure_id, actor="fixture-first", token=first["token"], release=True)
        successor = improvement_work.claim(coord, failure_id, actor="fixture-successor")
        packet = improvement_work.brief(coord, failure_id, actor="fixture-successor", token=successor["token"])
        record("successor_receives_verified_failure", len(packet["validation_runs"]) == 1
               and packet["validation_runs"][0]["actor"] == "fixture-first"
               and packet["validation_runs"][0]["test_status"] == "FAILED")
        failed_fixture.write_text("import unittest\nclass Recovery(unittest.TestCase):\n"
            "    def test_recovery(self):\n        self.assertEqual(2 + 2, 4)\n", encoding="utf-8")
        repaired = improvement.validate_tests(coord, failure_id, workspace=failed_workspace,
            actor="fixture-successor", token=successor["token"], timeout=30)
        record("explicit_repair_validates", repaired["status"] == "PASSED"
               and repaired["proposal"]["state"] == "VALIDATED")
        report["failure_handoff"] = {"proposal_id": failure_id, "failed_receipt": failed["execution_receipt"],
            "repaired_receipt": repaired["execution_receipt"], "scripted_roles": True}
        report.update(status="PASSED", proposal_id=identity)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report["error"] = str(exc)
    report["report"] = str(report_path)
    _atomic_json(report_path, report)
    return report
