from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from agent_evolution_protocol import cli
from agent_evolution_protocol.coordination import desktop_acceptance as acceptance
from agent_evolution_protocol.coordination import transcript_bus


SESSION_PROOF = {
    "session_region": {"left": 0.2, "top": 0.1, "right": 0.8, "bottom": 0.2},
    "selector_region": {"left": 0.0, "top": 0.1, "right": 0.15, "bottom": 0.9},
    "model_region": {"left": 0.7, "top": 0.8, "right": 0.99, "bottom": 0.99},
    "composer_region": {"left": 0.2, "top": 0.8, "right": 0.65, "bottom": 0.99},
}
SESSION_PROOF_DIGEST = "sha256:" + sha256(json.dumps(
    SESSION_PROOF, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
).encode("utf-8")).hexdigest()

PROFILE = {
    "agent": "qoder_cn",
    "process": "QoderCN",
    "title": "Qoder CN",
    "exe": "D:\\Programs\\QoderCN\\QoderCN.exe",
    "session_marker": "请加入项目协作区",
    "model_marker": "Model",
    "transport": "uia",
    "reply_timeout_seconds": 5,
    "require_strict_session_attestation": True,
    "session_proof": SESSION_PROOF,
    "route_calibration": {
        "schema": "aep-client-route-calibration-v1",
        "id": "AEP-CAL-TEST-01",
        "digest": "sha256:" + "0" * 64,
        "session_proof_digest": SESSION_PROOF_DIGEST,
        "surface_digest": "sha256:" + "1" * 64,
        "calibrated_at": "2026-11-28T00:00:00+00:00",
    },
}
MESSAGE = "请回报当前进展，并在结果中写 AEP-RESULT:EVT-ACCEPT-01。"
EVENT = "EVT-ACCEPT-01"
PERSISTED_RECEIPT = {"verified": True, "reason": "TEST_PERSISTED_RECEIPT"}


def _relay(**updates):
    base = {
        "agent": PROFILE["agent"],
        "event_id": EVENT,
        "state": "AGENT_REPLY_VERIFIED",
        "session_marker": PROFILE["session_marker"],
        "task_delivered": True,
        "agent_accepted": True,
        "ack_evidence": "exact",
        "reply_excerpt": "AEP-ACK:ABC\nAEP-RESULT:EVT-ACCEPT-01\n完成。",
        "session_attestation": {
            "valid": True,
            "attestation_path": "coordination/session_attestations/qoder_cn.json",
            "evidence_path": "coordination/session_attestation_evidence/qoder_cn-EVT-ACCEPT-01.json",
        },
    }
    base.update(updates)
    return base


def test_preview_does_not_claim_any_delivery_stage():
    result = acceptance.preview(profile=PROFILE, message=MESSAGE, event_id=EVENT,
                                result_marker="AEP-RESULT:EVT-ACCEPT-01")
    assert result["mode"] == "PREVIEW"
    assert result["verdict"] == {"state": "PREVIEW", "next_required": "SESSION_LOCATED"}
    assert not any(result["stages"][name]["verified"] for name in acceptance.STAGES)
    assert len(result["manifest_sha256"]) == 64


def test_complete_requires_all_five_stages():
    result = acceptance.build_manifest(profile=PROFILE, message=MESSAGE, event_id=EVENT,
                                       relay=_relay(), result_marker="AEP-RESULT:EVT-ACCEPT-01",
                                       mode="APPLY", receipt_integrity=PERSISTED_RECEIPT)
    assert result["verdict"] == {"state": "COMPLETE", "next_required": "NONE"}
    assert all(result["stages"][name]["verified"] for name in acceptance.STAGES)
    assert result["stages"]["TASK_RESULT_READBACK"]["evidence"]["marker"]["seen"] is True


def test_ack_without_result_marker_is_not_completion():
    result = acceptance.build_manifest(profile=PROFILE, message=MESSAGE, event_id=EVENT,
                                       relay=_relay(reply_excerpt="AEP-ACK:ABC\n已收到。"),
                                       result_marker="AEP-RESULT:EVT-ACCEPT-01",
                                       receipt_integrity=PERSISTED_RECEIPT)
    assert result["stages"]["AGENT_ACK"]["verified"] is True
    assert result["stages"]["TASK_RESULT_READBACK"]["verified"] is False
    assert result["verdict"] == {"state": "AWAITING_TASK_RESULT", "next_required": "TASK_RESULT_READBACK"}


def test_visible_message_without_ack_stays_awaiting_ack():
    result = acceptance.build_manifest(profile=PROFILE, message=MESSAGE, event_id=EVENT,
                                       relay=_relay(state="NO_REPLY_WITHIN_TIMEOUT",
                                                    agent_accepted=False, reply_excerpt=""),
                                       result_marker="AEP-RESULT:EVT-ACCEPT-01",
                                       receipt_integrity=PERSISTED_RECEIPT)
    assert result["stages"]["MESSAGE_VISIBLE"]["verified"] is True
    assert result["stages"]["AGENT_ACK"]["verified"] is False
    assert result["verdict"] == {"state": "AWAITING_ACK", "next_required": "AGENT_ACK"}


def test_unconfirmed_submit_is_held_not_promoted_to_input():
    result = acceptance.build_manifest(profile=PROFILE, message=MESSAGE, event_id=EVENT,
                                       relay=_relay(state="NO_REPLY_WITHIN_TIMEOUT",
                                                    task_delivered=False, agent_accepted=False,
                                                    reply_excerpt=""),
                                       result_marker="AEP-RESULT:EVT-ACCEPT-01",
                                       receipt_integrity=PERSISTED_RECEIPT)
    assert result["stages"]["INPUT_SUBMITTED"]["verified"] is False
    assert result["stages"]["MESSAGE_VISIBLE"]["verified"] is False
    assert result["verdict"] == {"state": "HOLD", "next_required": "INPUT_SUBMITTED"}


def test_strict_profile_requires_stored_valid_attestation():
    result = acceptance.build_manifest(profile=PROFILE, message=MESSAGE, event_id=EVENT,
                                       relay=_relay(session_attestation={"valid": False}),
                                       result_marker="AEP-RESULT:EVT-ACCEPT-01",
                                       receipt_integrity=PERSISTED_RECEIPT)
    assert result["stages"]["SESSION_LOCATED"]["verified"] is False
    assert not any(result["stages"][stage]["verified"] for stage in acceptance.STAGES[1:])
    assert result["verdict"] == {"state": "HOLD", "next_required": "SESSION_LOCATED"}


def test_uncertain_relay_never_becomes_a_retry_instruction():
    result = acceptance.build_manifest(profile=PROFILE, message=MESSAGE, event_id=EVENT,
                                       relay=_relay(state="SEND_UNCERTAIN", task_delivered=False,
                                                    agent_accepted=False, reply_excerpt=""),
                                       result_marker="AEP-RESULT:EVT-ACCEPT-01",
                                       receipt_integrity=PERSISTED_RECEIPT)
    assert result["verdict"] == {"state": "UNCERTAIN", "next_required": "RECONCILE_ORIGINAL_SESSION"}


def test_execute_without_apply_is_preview_and_does_not_touch_paths(tmp_path):
    output = tmp_path / "manifest.json"
    relay = tmp_path / "relay.json"
    result = acceptance.execute(profile=PROFILE, message=MESSAGE, event_id=EVENT,
                                receipt_path=relay, manifest_path=output,
                                lock_path=tmp_path / "desktop.lock", driver=object(),
                                result_marker="AEP-RESULT:EVT-ACCEPT-01", apply=False)
    assert result["mode"] == "PREVIEW"
    assert not output.exists()
    assert not relay.exists()


def test_execute_persists_an_atomic_manifest_after_single_relay(monkeypatch, tmp_path):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        relay = _relay()
        kwargs["receipt_path"].write_text(json.dumps(relay), encoding="utf-8")
        return relay

    monkeypatch.setattr(acceptance.ui_fallback, "run", fake_run)
    output = tmp_path / "acceptance.json"
    result = acceptance.execute(profile=PROFILE, message=MESSAGE, event_id=EVENT,
                                receipt_path=tmp_path / "relay.json", manifest_path=output,
                                lock_path=tmp_path / "desktop.lock", driver=object(),
                                result_marker="AEP-RESULT:EVT-ACCEPT-01", apply=True)
    assert len(calls) == 1
    assert result["verdict"]["state"] == "COMPLETE"
    on_disk = json.loads(output.read_text(encoding="utf-8"))
    assert on_disk["manifest_sha256"] == result["manifest_sha256"]
    assert not (tmp_path / "acceptance.json.partial").exists()


def test_mismatched_relay_identity_cannot_promote_any_later_stage():
    result = acceptance.build_manifest(
        profile=PROFILE, message=MESSAGE, event_id=EVENT,
        relay=_relay(event_id="EVT-OTHER-01"),
        result_marker="AEP-RESULT:EVT-ACCEPT-01", receipt_integrity=PERSISTED_RECEIPT,
    )
    assert result["verdict"] == {"state": "HOLD", "next_required": "RELAY_IDENTITY"}
    assert not any(result["stages"][stage]["verified"] for stage in acceptance.STAGES)


def test_execute_holds_when_relay_return_was_not_persisted(monkeypatch, tmp_path):
    monkeypatch.setattr(acceptance.ui_fallback, "run", lambda *_args, **_kwargs: _relay())
    result = acceptance.execute(
        profile=PROFILE, message=MESSAGE, event_id=EVENT,
        receipt_path=tmp_path / "missing-relay.json", manifest_path=tmp_path / "acceptance.json",
        lock_path=tmp_path / "desktop.lock", driver=object(),
        result_marker="AEP-RESULT:EVT-ACCEPT-01", apply=True,
    )
    assert result["verdict"] == {"state": "HOLD", "next_required": "PERSISTED_RELAY_RECEIPT"}
    assert result["relay"]["receipt_integrity"]["verified"] is False


def test_readback_reconcile_completes_from_later_bound_transcript(tmp_path):
    receipt = tmp_path / "relay.json"
    receipt.write_text(json.dumps(_relay(reply_excerpt="AEP-ACK:ABC\n正在执行。")), encoding="utf-8")
    marker = "AEP-RESULT:EVT-ACCEPT-01"
    events = [
        {"id": "foreign", "role": "assistant", "direction": "incoming",
         "session_id": "别的会话", "added_lines": [marker]},
        {"id": "bound", "role": "assistant", "direction": "incoming",
         "session_id": PROFILE["session_marker"], "added_lines": [marker, "产物已写入。"]},
    ]
    result = acceptance.reconcile_readback(
        profile=PROFILE, message=MESSAGE, event_id=EVENT, receipt_path=receipt,
        manifest_path=tmp_path / "acceptance.json", result_marker=marker,
        readback_events=events,
    )
    assert result["mode"] == "READBACK_RECONCILE"
    assert result["verdict"] == {"state": "COMPLETE", "next_required": "NONE"}
    evidence = result["stages"]["TASK_RESULT_READBACK"]["evidence"]
    assert evidence["source"] == "TRANSCRIPT_BUS"
    assert evidence["marker"]["seen"] is True
    assert "foreign" in evidence["session"]["rejected"][-1]["event"]


def test_foreign_or_unbound_readback_cannot_complete(tmp_path):
    receipt = tmp_path / "relay.json"
    receipt.write_text(json.dumps(_relay(reply_excerpt="AEP-ACK:ABC\n正在执行。")), encoding="utf-8")
    result = acceptance.reconcile_readback(
        profile=PROFILE, message=MESSAGE, event_id=EVENT, receipt_path=receipt,
        manifest_path=tmp_path / "acceptance.json", result_marker="AEP-RESULT:EVT-ACCEPT-01",
        readback_events=[{"id": "foreign", "role": "assistant", "direction": "incoming",
                          "session_id": "别的会话", "added_lines": ["AEP-RESULT:EVT-ACCEPT-01"]}],
    )
    assert result["verdict"] == {"state": "AWAITING_TASK_RESULT", "next_required": "TASK_RESULT_READBACK"}
    assert result["stages"]["TASK_RESULT_READBACK"]["verified"] is False


def test_cli_preview_is_registered_and_has_no_desktop_or_file_side_effect(monkeypatch, tmp_path, capsys):
    profile_path = tmp_path / "qoder_cn.json"
    profile_path.write_text(json.dumps(PROFILE), encoding="utf-8")

    def must_not_execute(**_kwargs):
        raise AssertionError("preview must not construct a driver or invoke the relay")

    monkeypatch.setattr(cli.desktop_acceptance, "execute", must_not_execute)
    args = cli.build_parser().parse_args([
        "--coord-root", str(tmp_path), "coord-desktop-acceptance",
        "--agent", "qoder_cn", "--event-id", EVENT, "--text", MESSAGE,
        "--profile-file", str(profile_path), "--result-marker", "AEP-RESULT:EVT-ACCEPT-01",
    ])
    assert args.func(args) == 0
    row = json.loads(capsys.readouterr().out)
    assert row["apply_requested"] is False
    assert row["acceptance"]["verdict"]["state"] == "PREVIEW"
    assert not (tmp_path / "acceptance_receipts").exists()
    assert not (tmp_path / "commander" / "receipts").exists()


def test_cli_apply_propagates_incomplete_envelope_as_nonzero(monkeypatch, tmp_path, capsys):
    profile_path = tmp_path / "qoder_cn.json"
    profile_path.write_text(json.dumps(PROFILE), encoding="utf-8")
    preview = acceptance.preview(profile=PROFILE, message=MESSAGE, event_id=EVENT)
    held = {**preview, "mode": "APPLY", "verdict": {"state": "HOLD", "next_required": "SESSION_LOCATED"}}
    monkeypatch.setattr(cli.uia3, "driver_for", lambda _profile: object())
    monkeypatch.setattr(cli.desktop_acceptance, "execute", lambda **_kwargs: held)
    args = cli.build_parser().parse_args([
        "--coord-root", str(tmp_path), "coord-desktop-acceptance",
        "--agent", "qoder_cn", "--event-id", EVENT, "--text", MESSAGE,
        "--profile-file", str(profile_path), "--apply",
    ])
    assert args.func(args) == 4
    assert json.loads(capsys.readouterr().out)["acceptance"]["verdict"]["state"] == "HOLD"


def test_cli_readback_only_uses_bound_transcript_without_a_second_send(monkeypatch, tmp_path, capsys):
    profile_path = tmp_path / "qoder_cn.json"
    profile_path.write_text(json.dumps(PROFILE), encoding="utf-8")
    receipt_path = tmp_path / "commander" / "receipts" / f"{EVENT}-qoder_cn-profile.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(json.dumps(_relay(reply_excerpt="AEP-ACK:ABC\n正在执行。")), encoding="utf-8")
    transcript_bus.publish(
        tmp_path, agent="qoder_cn", text="AEP-RESULT:EVT-ACCEPT-01\n产物完成。",
        session_id=PROFILE["session_marker"], role="assistant", direction="incoming",
    )
    monkeypatch.setattr(cli.uia3, "driver_for", lambda _profile: (_ for _ in ()).throw(
        AssertionError("readback-only must not construct a desktop driver")))
    args = cli.build_parser().parse_args([
        "--coord-root", str(tmp_path), "coord-desktop-acceptance",
        "--agent", "qoder_cn", "--event-id", EVENT, "--text", MESSAGE,
        "--profile-file", str(profile_path), "--result-marker", "AEP-RESULT:EVT-ACCEPT-01",
        "--readback-only",
    ])
    assert args.func(args) == 0
    row = json.loads(capsys.readouterr().out)
    assert row["readback_event_count"] == 1
    assert row["acceptance"]["mode"] == "READBACK_RECONCILE"
    assert row["acceptance"]["verdict"]["state"] == "COMPLETE"
