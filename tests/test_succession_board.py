"""Tests for the read-only succession situation board.

The board is the missing "where is the battle now / who can take over" view for
quota-constrained fleets.  These tests lock its hardest boundaries: advice is
advisory-only, stale presence and undeclared quota never qualify, message
evidence never becomes ACK, and the board never writes anything.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_evolution_protocol.coordination import succession_board as sb

AT = datetime(2026, 11, 22, 6, 0, tzinfo=timezone.utc)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")


def _fleet(root: Path, *, quota: bool = True, capability: bool = True,
           presence: str = "online", role_state: str = "STANDBY",
           holder: str | None = None) -> Path:
    coord = root / "coordination"
    later = (AT + timedelta(minutes=30)).isoformat()
    _write(coord / "presence" / "agents" / "alpha.json", {
        "agent": "alpha", "state": presence, "source": "agent-self-report",
        "session_id": "alpha-overnight", "observed_at": AT.isoformat(),
        "expires_at": later,
    })
    if capability:
        _write(coord / "capabilities" / "alpha.json", {
            "agent": "alpha", "chips": [], "compilers": [], "strengths": ["command"],
            "evidence": ["tests/test_succession_board.py"],
            "declared_at": AT.isoformat(), "available_until": later,
        })
    if quota:
        _write(coord / "relay" / "resources" / "alpha.json", {
            "schema": "aep-quota-envelope-v1", "agent": "alpha", "provider": "test",
            "unit": "credits", "remaining": 10, "reserve": 1, "limit": 20,
            "capabilities": ["command"], "evidence": ["unit-test"],
            "declared_at": AT.isoformat(), "expires_at": later,
            "blocked": False, "source": "self_report",
        })
    _write(coord / "command_handoff" / "state.json", {
        "schema": "aep-command-handoff-v1", "seq": 0,
        "roles": {
            "commander": {
                "state": role_state, "holder": holder,
                "required_capability": "command", "candidates": {},
                "handoffs": [], "updated_seq": 0,
            }
        },
        "events": [],
    })
    return coord


def _digest(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def test_empty_fleet_names_its_gaps_and_never_advances(tmp_path: Path):
    board = sb.build(tmp_path / "coordination", now=AT)
    assert board["verdict"] == "GAPS"
    assert board["advisory_only"] is True
    assert board["may_advance"] is False
    assert board["execution_started"] is False
    assert "NO_CAPABILITY_DECLARATION" in board["gaps"]
    assert "NO_FRESH_ONLINE_AGENT" in board["gaps"]
    assert board["takeover_advice"] == []
    assert all(row["coverage"] == "UNCOVERED" for row in board["roles"])


def test_advice_requires_presence_capability_and_active_quota(tmp_path: Path):
    coord = _fleet(tmp_path)
    board = sb.build(coord, now=AT)
    assert board["verdict"] == "ACTIONABLE"
    advice = board["takeover_advice"]
    assert len(advice) == 1
    row = advice[0]
    assert row["candidate"] == "alpha"
    assert row["role"] == "commander"
    assert row["advisory_only"] is True
    assert row["may_advance"] is False
    assert row["execution_started"] is False
    assert row["requires_human_review"] is True
    assert "--dry-run" in row["dry_run_command"]
    assert "capability:command" in row["reason_codes"]


def test_stale_presence_is_not_offline_and_never_qualifies(tmp_path: Path):
    coord = _fleet(tmp_path, presence="online")
    row = json.loads((coord / "presence" / "agents" / "alpha.json").read_text(encoding="utf-8"))
    row["expires_at"] = (AT - timedelta(minutes=5)).isoformat()
    _write(coord / "presence" / "agents" / "alpha.json", row)
    board = sb.build(coord, now=AT)
    agent = next(item for item in board["agents"] if item["agent"] == "alpha")
    assert agent["presence"] == "stale"
    assert "NO_FRESH_PRESENCE:alpha" in board["gaps"]
    assert board["takeover_advice"] == []


def test_exhausted_quota_blocks_advice(tmp_path: Path):
    coord = _fleet(tmp_path)
    path = coord / "relay" / "resources" / "alpha.json"
    row = json.loads(path.read_text(encoding="utf-8"))
    row["reserve"] = row["remaining"]
    _write(path, row)
    board = sb.build(coord, now=AT)
    assert board["takeover_advice"] == []
    agent = next(item for item in board["agents"] if item["agent"] == "alpha")
    assert agent["quota"] == "EXHAUSTED"


def test_undeclared_quota_blocks_advice_and_names_the_gap(tmp_path: Path):
    coord = _fleet(tmp_path, quota=False)
    board = sb.build(coord, now=AT)
    assert board["takeover_advice"] == []
    assert "QUOTA_UNDECLARED:alpha" in board["gaps"]


def test_capability_token_must_match_the_role(tmp_path: Path):
    coord = _fleet(tmp_path)
    path = coord / "capabilities" / "alpha.json"
    row = json.loads(path.read_text(encoding="utf-8"))
    row["strengths"] = ["recon"]
    row["capabilities"] = ["recon"]
    _write(path, row)
    quota_path = coord / "relay" / "resources" / "alpha.json"
    quota = json.loads(quota_path.read_text(encoding="utf-8"))
    quota["capabilities"] = ["recon"]
    _write(quota_path, quota)
    board = sb.build(coord, now=AT)
    assert board["takeover_advice"] == []
    assert "ROLE_NOT_HELD:commander" in board["gaps"]


def test_current_holder_is_not_offered_as_its_own_successor(tmp_path: Path):
    coord = _fleet(tmp_path, role_state="ACTIVE", holder="alpha")
    board = sb.build(coord, now=AT)
    assert board["roles"][0]["coverage"] == "HELD"
    assert board["takeover_advice"] == []


def test_expired_claim_is_ignored(tmp_path: Path):
    coord = _fleet(tmp_path)
    _write(coord / "claims" / "OLD-CLAIM" / "owner.json", {
        "task": "OLD-CLAIM", "agent": "alpha", "at": AT.isoformat(),
        "expires_at": (AT - timedelta(hours=1)).isoformat(), "paths": [],
    })
    _write(coord / "claims" / "LIVE-CLAIM" / "owner.json", {
        "task": "LIVE-CLAIM", "agent": "alpha", "at": AT.isoformat(),
        "expires_at": (AT + timedelta(hours=1)).isoformat(), "paths": ["artifact.md"],
    })
    (tmp_path / "artifact.md").write_text("evidence", encoding="utf-8")
    board = sb.build(coord, now=AT)
    tasks = {row["task"] for row in board["claims"]}
    assert tasks == {"LIVE-CLAIM"}
    live = board["claims"][0]
    assert live["visible_artifact_count"] == 1
    assert live["activity"] == "ACTIVE"


def test_messy_quoted_paths_string_is_tolerated(tmp_path: Path):
    coord = _fleet(tmp_path)
    _write(coord / "claims" / "MESSY" / "owner.json", {
        "task": "MESSY", "agent": "alpha", "at": AT.isoformat(),
        "expires_at": (AT + timedelta(hours=1)).isoformat(),
        "paths": "'artifact.md'",  # a real claim wrote paths as a quoted string
    })
    (tmp_path / "artifact.md").write_text("evidence", encoding="utf-8")
    board = sb.build(coord, now=AT)
    assert board["claims"][0]["visible_artifact_count"] == 1


def test_message_evidence_is_never_ack(tmp_path: Path):
    coord = _fleet(tmp_path)
    _write(coord / "messages" / "alpha" / "20261122T055900000000Z-abc.json", {
        "id": "20261122T055900000000Z-abc", "at": AT.isoformat(),
        "kind": "result", "sender": "alpha", "topic": "T",
        "text": "AEP-ACK: ABCD already present in a mailbox file",
    })
    board = sb.build(coord, now=AT)
    agent = next(item for item in board["agents"] if item["agent"] == "alpha")
    assert agent["last_message"]["id"] == "20261122T055900000000Z-abc"
    assert agent["message_is_ack"] is False


def test_board_never_writes_anything(tmp_path: Path):
    coord = _fleet(tmp_path)
    before = _digest(tmp_path)
    sb.build(coord, now=AT)
    assert _digest(tmp_path) == before


def test_board_is_deterministic_for_a_fixed_instant(tmp_path: Path):
    coord = _fleet(tmp_path)
    assert sb.build(coord, now=AT) == sb.build(coord, now=AT)


def test_unavailable_source_degrades_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from agent_evolution_protocol.federation import command_handoff

    def broken(_root):
        raise RuntimeError("concurrent edit")

    monkeypatch.setattr(command_handoff, "status", broken)
    coord = _fleet(tmp_path)
    board = sb.build(coord, now=AT)
    assert board["verdict"] == "DEGRADED"
    assert any(gap.startswith("SOURCE_UNAVAILABLE:command_handoff") for gap in board["gaps"])


def test_cli_json_and_brief(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    coord = _fleet(tmp_path)
    assert sb.main(["--coord-root", str(coord), "--at", AT.isoformat(), "--format", "brief"]) == 0
    assert "接手态势板" in capsys.readouterr().out
    assert sb.main(["--coord-root", str(coord), "--at", AT.isoformat()]) == 0
    board = json.loads(capsys.readouterr().out)
    assert board["schema"] == sb.SCHEMA
    assert board["may_advance"] is False
