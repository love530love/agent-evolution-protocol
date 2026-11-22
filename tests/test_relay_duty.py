from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
from pathlib import Path

import pytest

from agent_evolution_protocol.coordination import wake_ingress
from agent_evolution_protocol.federation import command_handoff
from agent_evolution_protocol.relay import cli as relay_cli
from agent_evolution_protocol.relay import duty, resources

NOW = datetime(2026, 11, 22, 6, 0, tzinfo=timezone.utc)
CHECKPOINT = {
    "next_step": "compile the next kernel batch and report the exit code",
    "completed": ["fixture setup"],
    "artifacts": ["build/first.o"],
    "pending_operations": [],
    "unknowns": ["which agent holds the upload permission"],
}


def declare(root, agent, remaining=50):
    return resources.declare(
        root, agent=agent, provider="vendor-" + agent, unit="credits",
        remaining=remaining, evidence=[f"{agent} quota declared"], now=NOW,
        capabilities=["command"])


def setup_fleet(tmp_path):
    root = tmp_path / "coord"
    bridge = tmp_path / "bridge"
    command_handoff.claim(root, agent="cdr", role="commander",
                          capability="command", evidence=["cdr joined"])
    declare(root, "cdr", remaining=100)
    duty.beat(root, agent="cdr", role="commander",
              evidence=["cdr alive"], checkpoint=CHECKPOINT, now=NOW)
    command_handoff.volunteer(root, from_agent="agent-b", role="commander",
                              capability="command", reason="ready successor",
                              evidence=["agent-b completed shadow drill"])
    declare(root, "agent-b", remaining=2)
    command_handoff.volunteer(root, from_agent="weak-builder", role="commander",
                              capability="build", reason="wrong specialty",
                              evidence=["weak-builder is a builder only"])
    declare(root, "weak-builder", remaining=100)
    command_handoff.volunteer(root, from_agent="undeclared", role="commander",
                              capability="command", reason="no quota yet",
                              evidence=["undeclared volunteered"])
    return root, bridge


def make_offer(root, bridge, **overrides):
    resources.mark_exhausted(root, agent="cdr", reason="credits exhausted",
                             evidence=["provider 402"], now=NOW)
    values = dict(actor="cdr", role="commander", to="agent-b",
                  signal="quota_exhausted", reason="vendor credits exhausted",
                  evidence=["provider 402 at 05:59Z"], checkpoint=CHECKPOINT,
                  bridge_root=bridge, now=NOW)
    values.update(overrides)
    return duty.offer(root, **values)


def test_claim_beat_is_ready_but_is_not_ack(tmp_path):
    root, _ = setup_fleet(tmp_path)
    plan = duty.plan(root, "commander", now=NOW)
    assert plan["action_required"] == "HOLD"
    assert plan["seat"]["seat_state"] == "READY"
    assert plan["seat"]["heartbeat"]["state"] == "FRESH"
    assert plan["recommended"] is None
    assert "not target ACK" in plan["note"]


def test_stale_heartbeat_blocks_offer_and_requires_upstream(tmp_path):
    root, bridge = setup_fleet(tmp_path)
    later = NOW + timedelta(minutes=20)
    plan = duty.plan(root, "commander", now=later)
    assert plan["seat"]["heartbeat"]["state"] == "STALE"
    assert plan["action_required"] == "EMERGENCY_REQUIRES_UPSTREAM"
    assert "no_heartbeat" in plan["blocked_by"]
    resources.mark_exhausted(root, agent="cdr", reason="credits exhausted",
                             evidence=["provider 402"], now=NOW)
    with pytest.raises(ValueError, match="HOLDER_HEARTBEAT_NOT_FRESH:STALE"):
        duty.offer(root, actor="cdr", role="commander", to="agent-b",
                   signal="quota_exhausted", reason="must hand off",
                   evidence=["provider 402"], checkpoint=CHECKPOINT,
                   bridge_root=bridge, now=later)


def test_offer_deposits_mailbox_but_never_accepts_or_changes_role(tmp_path):
    root, bridge = setup_fleet(tmp_path)
    offered = make_offer(root, bridge)
    assert offered["state"] == "OFFERED"
    assert offered["delivery"]["agent_accepted"] is False
    assert offered["delivery"]["mailbox_state"] == "DEPOSITED"
    assert offered["delivery"]["wake_queued"] is False
    mailbox = Path(offered["delivery"]["mailbox_path"])
    assert mailbox.is_file()
    message = json.loads(mailbox.read_text(encoding="utf-8"))
    assert message["id"] == offered["offer_id"]
    assert "不等于已接单" in message["text"]
    state = command_handoff.status(root)
    assert state["roles"]["commander"]["holder"] == "cdr"
    assert state["roles"]["commander"]["state"] == command_handoff.ACTIVE
    assert state["roles"]["commander"]["handoffs"] == []
    plan = duty.plan(root, "commander", now=NOW)
    assert plan["action_required"] == "PREPARE_OFFER"
    assert plan["recommended"]["agent"] == "agent-b"
    again = make_offer(root, bridge)
    assert again["idempotent"] is True
    assert again["no_redelivery"] is True


def test_accept_requires_explicit_target_digest_and_quota(tmp_path):
    root, bridge = setup_fleet(tmp_path)
    offered = make_offer(root, bridge)
    with pytest.raises(ValueError, match="NOT_OFFER_TARGET"):
        duty.accept(root, agent="weak-builder", role="commander",
                    offer_id=offered["offer_id"],
                    checkpoint_sha256=offered["checkpoint_sha256"],
                    evidence=["try"], bridge_root=bridge, now=NOW)
    with pytest.raises(ValueError, match="CHECKPOINT_MISMATCH"):
        duty.accept(root, agent="agent-b", role="commander",
                    offer_id=offered["offer_id"],
                    checkpoint_sha256="0" * 64,
                    evidence=["try"], bridge_root=bridge, now=NOW)
    accepted = duty.accept(root, agent="agent-b", role="commander",
                           offer_id=offered["offer_id"],
                           checkpoint_sha256=offered["checkpoint_sha256"],
                           evidence=["agent-b accepted in its own session"],
                           bridge_root=bridge, now=NOW)
    assert accepted["state"] == "ACCEPTED"
    assert accepted["transition"] == ["YIELDED", "TAKING_OVER", "ACTIVE"]
    assert accepted["heartbeat"]["state"] == "MISSING"
    assert accepted["heartbeat_required"] is True
    assert accepted["delivery"]["agent_accepted"] is False
    current = command_handoff.status(root)["roles"]["commander"]
    assert current["holder"] == "agent-b"
    assert current["state"] == command_handoff.ACTIVE
    assert Path(bridge / "messages" / "cdr" / f"{offered['offer_id']}-A.json").is_file()
    replay = duty.accept(root, agent="agent-b", role="commander",
                         offer_id=offered["offer_id"],
                         checkpoint_sha256=offered["checkpoint_sha256"],
                         evidence=["replay"], bridge_root=bridge, now=NOW)
    assert replay["idempotent"] is True
    assert replay["no_redelivery"] is True
    assert replay["still_holder"] is True
    with pytest.raises(ValueError, match="NOT_HOLDER"):
        duty.beat(root, agent="cdr", role="commander",
                  evidence=["old holder still alive"], checkpoint=CHECKPOINT, now=NOW)
    plan = duty.plan(root, "commander", now=NOW)
    assert plan["seat"]["heartbeat"]["state"] == "MISSING"
    assert plan["action_required"] == "EMERGENCY_REQUIRES_UPSTREAM"
    beat = duty.beat(root, agent="agent-b", role="commander",
                     evidence=["agent-b took command"], checkpoint=CHECKPOINT, now=NOW)
    assert beat["state"] == "FRESH"
    assert duty.plan(root, "commander", now=NOW)["action_required"] == "HOLD"


def test_accept_rechecks_target_quota_after_offer(tmp_path):
    root, bridge = setup_fleet(tmp_path)
    offered = make_offer(root, bridge)
    resources.mark_exhausted(root, agent="agent-b", reason="successor also hit limit",
                             evidence=["provider 429"], now=NOW)
    with pytest.raises(ValueError, match="TARGET_QUOTA_GATE:BLOCKED"):
        duty.accept(root, agent="agent-b", role="commander",
                    offer_id=offered["offer_id"],
                    checkpoint_sha256=offered["checkpoint_sha256"],
                    evidence=["try"], bridge_root=bridge, now=NOW)


def test_offer_rejects_undeclared_or_mismatched_successor(tmp_path):
    root, bridge = setup_fleet(tmp_path)
    resources.mark_exhausted(root, agent="cdr", reason="credits exhausted",
                             evidence=["provider 402"], now=NOW)
    with pytest.raises(ValueError, match="TARGET_QUOTA_NOT_ELIGIBLE:QUOTA_UNDECLARED"):
        duty.offer(root, actor="cdr", role="commander", to="undeclared",
                   signal="quota_exhausted", reason="try undeclared",
                   evidence=["provider 402"], checkpoint=CHECKPOINT,
                   bridge_root=bridge, now=NOW)
    with pytest.raises(ValueError, match="TARGET_CAPABILITY_MISMATCH:command"):
        duty.offer(root, actor="cdr", role="commander", to="weak-builder",
                   signal="quota_exhausted", reason="wrong specialty",
                   evidence=["provider 402"], checkpoint=CHECKPOINT,
                   bridge_root=bridge, now=NOW)


def test_decline_leaves_role_unchanged_and_blocks_acceptance(tmp_path):
    root, bridge = setup_fleet(tmp_path)
    offered = make_offer(root, bridge)
    declined = duty.decline(root, agent="agent-b", role="commander",
                            offer_id=offered["offer_id"],
                            reason="no budget in this window",
                            bridge_root=bridge, now=NOW)
    assert declined["schema"] == duty.DECLINE_SCHEMA
    assert declined["idempotent"] is False
    assert command_handoff.status(root)["roles"]["commander"]["holder"] == "cdr"
    assert Path(bridge / "messages" / "cdr" / f"{offered['offer_id']}-D.json").is_file()
    with pytest.raises(ValueError, match="OFFER_DECLINED"):
        duty.accept(root, agent="agent-b", role="commander",
                    offer_id=offered["offer_id"],
                    checkpoint_sha256=offered["checkpoint_sha256"],
                    evidence=["try anyway"], bridge_root=bridge, now=NOW)
    again = duty.decline(root, agent="agent-b", role="commander",
                         offer_id=offered["offer_id"], reason="same",
                         bridge_root=bridge, now=NOW)
    assert again["idempotent"] is True


def test_expired_offer_cannot_be_accepted(tmp_path):
    root, bridge = setup_fleet(tmp_path)
    offered = make_offer(root, bridge, expires_minutes=1)
    later = NOW + timedelta(minutes=2)
    with pytest.raises(ValueError, match="OFFER_EXPIRED"):
        duty.accept(root, agent="agent-b", role="commander",
                    offer_id=offered["offer_id"],
                    checkpoint_sha256=offered["checkpoint_sha256"],
                    evidence=["late"], bridge_root=bridge, now=later)
    again = make_offer(root, bridge, expires_minutes=1, now=later)
    assert again["idempotent"] is True
    assert again["state"] == "EXPIRED"


def test_dispatch_is_opt_in_and_never_replayed(tmp_path, monkeypatch):
    root, bridge = setup_fleet(tmp_path)
    (root / "ui_profiles").mkdir(parents=True, exist_ok=True)
    (root / "ui_profiles" / "agent-b.json").write_text("{}", encoding="utf-8")
    calls = []

    def fake_dispatch(wake_root, event_id, *, apply=False, retry_deferred=False):
        calls.append((str(wake_root), event_id, apply))
        return {"state": "MESSAGE_VISIBLE", "event_id": event_id,
                "agent_accepted": False}

    monkeypatch.setattr(wake_ingress, "dispatch_wake", fake_dispatch)
    offered = make_offer(root, bridge, dispatch=True)
    assert offered["delivery"]["wake_queued"] is True
    assert offered["delivery"]["dispatch_state"] == "MESSAGE_VISIBLE"
    assert offered["delivery"]["agent_accepted"] is False
    assert calls == [(str(bridge), offered["offer_id"], True)]
    again = make_offer(root, bridge, dispatch=True)
    assert again["idempotent"] is True
    assert len(calls) == 1
    assert command_handoff.status(root)["roles"]["commander"]["holder"] == "cdr"


def test_takeover_yielded_seat_and_old_holder_is_notified(tmp_path):
    root, bridge = setup_fleet(tmp_path)
    command_handoff.yield_role(root, from_agent="cdr", role="commander",
                               signal="time_limit", evidence=["session window ended"])
    plan = duty.plan(root, "commander", now=NOW)
    assert plan["action_required"] == "TAKEOVER_VACANT"
    result = duty.takeover_vacant(root, agent="agent-b", role="commander",
                                  evidence=["agent-b takes the vacant seat"],
                                  checkpoint=CHECKPOINT, bridge_root=bridge, now=NOW)
    assert result["state"] == "ACCEPTED"
    assert result["heartbeat"]["state"] == "MISSING"
    assert result["heartbeat_required"] is True
    current = command_handoff.status(root)["roles"]["commander"]
    assert current["holder"] == "agent-b"
    assert Path(bridge / "messages" / "cdr" / f"{result['handoff_id']}-N.json").is_file()
    assert Path(result["receipt_path"]).is_file()


def test_takeover_limited_seat_requires_live_holder_to_offer(tmp_path):
    root, bridge = setup_fleet(tmp_path)
    command_handoff.yield_role(root, from_agent="cdr", role="commander",
                               signal="credit_warning", evidence=["balance is low"])
    with pytest.raises(ValueError, match="HOLDER_STILL_LIVE_USE_OFFER"):
        duty.takeover_vacant(root, agent="agent-b", role="commander",
                             evidence=["premature takeover"], bridge_root=bridge, now=NOW)
    resources.mark_exhausted(root, agent="cdr", reason="balance reached zero",
                             evidence=["provider 402"], now=NOW)
    result = duty.takeover_vacant(root, agent="agent-b", role="commander",
                                  evidence=["provider already refused cdr"],
                                  bridge_root=bridge, now=NOW)
    assert result["state"] == "ACCEPTED"
    assert command_handoff.status(root)["roles"]["commander"]["holder"] == "agent-b"


def test_takeover_active_role_is_rejected(tmp_path):
    root, bridge = setup_fleet(tmp_path)
    with pytest.raises(ValueError, match="ROLE_NOT_TAKEOVERABLE:ACTIVE"):
        duty.takeover_vacant(root, agent="agent-b", role="commander",
                             evidence=["no vacancy"], bridge_root=bridge, now=NOW)


def test_old_heartbeat_does_not_validate_a_new_tenure(tmp_path):
    root, bridge = setup_fleet(tmp_path)
    command_handoff.yield_role(root, from_agent="cdr", role="commander",
                               signal="time_limit", evidence=["cdr window ended"])
    duty.takeover_vacant(root, agent="agent-b", role="commander",
                         evidence=["agent-b first tenure"], bridge_root=bridge, now=NOW)
    duty.beat(root, agent="agent-b", role="commander",
              evidence=["agent-b alive"], checkpoint=CHECKPOINT, now=NOW)
    command_handoff.volunteer(root, from_agent="cdr", role="commander",
                              capability="command", reason="cdr quota reset evidence pending",
                              evidence=["cdr asks to return after reset"])
    command_handoff.yield_role(root, from_agent="agent-b", role="commander",
                               signal="time_limit", evidence=["agent-b window ended"])
    duty.takeover_vacant(root, agent="cdr", role="commander",
                         evidence=["cdr returns"], bridge_root=bridge, now=NOW)
    heartbeat = duty.heartbeat_status(root, "commander", "cdr", now=NOW)
    assert heartbeat["state"] == "STALE_TENURE"
    seat = duty.seat_status(root, "commander", now=NOW)
    assert seat["seat_state"] == "HEARTBEAT_LOST"
    duty.beat(root, agent="cdr", role="commander",
              evidence=["cdr alive again"], checkpoint=CHECKPOINT, now=NOW)
    assert duty.seat_status(root, "commander", now=NOW)["seat_state"] == "READY"


def test_cli_parser_and_handler_round_trip(tmp_path):
    root = tmp_path / "coord"
    real_now = datetime.now(timezone.utc)
    command_handoff.claim(root, agent="cdr", role="commander",
                          capability="command", evidence=["cdr joined"])
    resources.declare(root, agent="cdr", provider="vendor", unit="credits",
                      remaining=100, evidence=["quota declared"],
                      capabilities=["command"], now=real_now)
    parser = relay_cli.build_parser()
    beat_args = parser.parse_args([
        "--coord-root", str(root),
        "beat",
        "--agent", "cdr", "--role", "commander", "--evidence", "alive",
        "--next-step", "keep going", "--completed", "beat through CLI",
    ])
    with redirect_stdout(StringIO()) as output:
        relay_cli.coord_command_relay(beat_args)
    beat = json.loads(output.getvalue())
    assert beat["state"] == "FRESH"
    plan_args = parser.parse_args([
        "--coord-root", str(root),
        "plan", "--role", "commander",
    ])
    with redirect_stdout(StringIO()) as output:
        relay_cli.coord_command_relay(plan_args)
    plan = json.loads(output.getvalue())
    assert plan["action_required"] == "HOLD"
    assert plan["seat"]["seat_state"] == "READY"
    status_args = parser.parse_args([
        "--coord-root", str(root), "status",
    ])
    with redirect_stdout(StringIO()) as output:
        relay_cli.coord_command_relay(status_args)
    status = json.loads(output.getvalue())
    assert status["seats"]["commander"]["holder"] == "cdr"


def test_summary_reports_chain_seats_and_offer_state(tmp_path):
    root, bridge = setup_fleet(tmp_path)
    offered = make_offer(root, bridge)
    duty.accept(root, agent="agent-b", role="commander",
                offer_id=offered["offer_id"],
                checkpoint_sha256=offered["checkpoint_sha256"],
                evidence=["agent-b accepted"], bridge_root=bridge, now=NOW)
    summary = duty.summary(root, now=NOW)
    assert summary["chain_valid"] is True
    assert summary["seats"]["commander"]["holder"] == "agent-b"
    assert summary["seats"]["commander"]["heartbeat"]["state"] == "MISSING"
    assert summary["offers"][0]["offer_id"] == offered["offer_id"]
    assert summary["offers"][0]["state"] == "ACCEPTED"
    assert summary["resources"]["invalid"] == []