"""closure_audit: three independent layers, never promoted into one another."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_evolution_protocol.coordination import closure_audit, relay_drill


def put(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_plan(root: Path, legs: list[dict]) -> None:
    put(root / relay_drill.PLAN_NAME, {"legs": legs})


def test_empty_root_opens_all_three_layers():
    with TemporaryDirectory() as folder:
        root = Path(folder)
        result = closure_audit.audit(root, root)
        assert result["all_closed"] is False
        assert result["layers_closed"] == []
        assert result["layers"]["evidence"]["state"] == "OPEN"
        assert result["layers"]["delivery"]["state"] == "OPEN"
        assert result["layers"]["reporting"]["state"] == "OPEN"
        assert "OFFICIAL" in result["layers"]["reporting"]["next_required"]


def test_evidence_verified_does_not_close_delivery_or_reporting():
    with TemporaryDirectory() as folder:
        root = Path(folder)
        bridge = root / "bridge"
        event = "D20261122T000000000000Z-test00000001"
        write_plan(root, [{"event_id": event, "sender": "workbuddyai",
                           "target": "workbuddy"}])
        put(root / "delegation" / "inbox" / f"{event}.json",
            {"from": "workbuddyai", "to": "workbuddy"})
        digest = hashlib.sha256("text".encode("utf-8")).hexdigest()
        put(root / "delegation" / "receipts" / f"{event}.json",
            {"state": "AGENT_ACK", "agent_accepted": True, "to": "workbuddy",
             "reply": f"analysis_sha256={digest}"})
        put(root / "delegation" / "artifacts" / event / "workbuddy.json",
            {"agent": "workbuddy", "analysis": "text", "sha256": digest})
        put(bridge / "messages" / "workbuddyai" / f"{event}-reply.json",
            {"text": f"analysis_sha256={digest}"})

        result = closure_audit.audit(root, bridge)
        assert result["layers"]["evidence"]["verified"] == 1
        assert result["layers"]["evidence"]["state"] == "CLOSED"
        assert result["layers"]["delivery"]["state"] == "OPEN"
        assert result["layers"]["reporting"]["state"] == "OPEN"
        assert result["all_closed"] is False


def test_identity_only_agent_is_mailbox_only_by_design():
    with TemporaryDirectory() as folder:
        root = Path(folder)
        event = "D20261122T000000000000Z-test00000002"
        write_plan(root, [{"event_id": event, "sender": "workbuddy",
                           "target": "workbuddyai"}])
        result = closure_audit.audit(root, root)
        row = result["layers"]["delivery"]["messages"][0]
        assert row["identity_only"] is True
        assert row["state"] == "MAILBOX_ONLY_BY_DESIGN"
        assert row["desktop"] == "NOT_ATTEMPTED"


def test_unverified_return_does_not_close_reporting_layer():
    # Regression for the expert-robust P1 finding: a locally supplied snapshot
    # with verified_official=False must NOT read as an official delivery.
    with TemporaryDirectory() as folder:
        root = Path(folder)
        put(root / "campaign" / "missions" / "T999.json", {
            "task": "T999",
            "returns": [{"submission_id": "12345", "package_sha256": "b" * 64,
                         "verified_official": False, "failures": []}],
        })
        result = closure_audit.audit(root, root)
        reporting = result["layers"]["reporting"]
        assert reporting["state"] == "OPEN"
        assert reporting["officially_verified"] == 0
        assert reporting["submissions"][0]["submission_id"] == "12345"
        assert reporting["next_required"] == "OFFICIAL_VERIFIED_RETURN_WITH_SUBMISSION_ID_AND_SHA256"
        assert result["all_closed"] is False


def test_officially_verified_return_closes_reporting_layer_only():
    with TemporaryDirectory() as folder:
        root = Path(folder)
        put(root / "campaign" / "missions" / "T999.json", {
            "task": "T999",
            "returns": [{"submission_id": "12345", "package_sha256": "b" * 64,
                         "verified_official": True, "failures": []}],
        })
        result = closure_audit.audit(root, root)
        reporting = result["layers"]["reporting"]
        assert reporting["state"] == "CLOSED"
        assert reporting["officially_verified"] == 1
        assert result["layers"]["evidence"]["state"] == "OPEN"
        assert result["all_closed"] is False


def test_malformed_hash_never_closes_reporting_layer():
    with TemporaryDirectory() as folder:
        root = Path(folder)
        put(root / "campaign" / "missions" / "T999.json", {
            "task": "T999",
            "returns": [
                {"submission_id": "111", "package_sha256": "not-a-sha", "verified_official": True},
                {"submission_id": "", "package_sha256": "c" * 64, "verified_official": True},
            ],
        })
        result = closure_audit.audit(root, root)
        reporting = result["layers"]["reporting"]
        assert reporting["state"] == "OPEN"
        assert reporting["officially_verified"] == 0
