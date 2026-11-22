"""Read-only succession situation board for quota-constrained agent fleets.

The project already owns the pieces: ``capability_roster`` says who declared a
skill, ``legion_ledger`` says who did work, ``collab_health`` says whether the
fleet is alive, ``command_handoff`` says who holds a role and ``relay`` says who
has quota.  None of them answers the question a weak incoming agent actually
has:

    Where is the battle now, what is unfinished, and who can safely take over
    next without guessing or double-commanding?

This module composes those sources into one recomputable board.  It deliberately
does **not** redefine their verdicts: freshness comes from ``presence.effective``,
fleet health from ``collab_health.snapshot``, role state from
``command_handoff.status`` and quota state from ``relay.resources.snapshot``.

Hard boundaries
---------------
* read-only: no writes, no subprocess, no network, no model import;
* advisory-only: advice never advances a role, sends a message, marks ACK or
  starts an execution; ``may_advance`` is always false;
* fail-closed: a missing or unreadable source becomes a ``SOURCE_UNAVAILABLE``
  gap, never a guessed ``online`` / ``ACTIVE`` / ``has quota``;
* stale presence stays ``stale`` (evidence expired), never ``offline``;
* mailbox files, MESSAGE_VISIBLE and self-reported quota are not ACK, not
  delivery and not a provider balance proof.

No new schema file is registered: the board is a composition view, not a second
standard.  No ``coord-*`` parser is touched, so it cannot collide with another
agent's in-flight CLI claim.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Any

from ..isotime import parse_iso_or_none
from . import collab_health, presence

SCHEMA = "aep-succession-board-v1"
NOTE = (
    "Composition view only. Advisory advice never advances a role, sends, "
    "marks ACK or starts execution. Self-reported quota is not a provider balance."
)
STALE_CLAIM_HOURS = 2.0
_SAFE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def _now(at: datetime | None) -> datetime:
    if at is None:
        return datetime.now(timezone.utc)
    if at.tzinfo is None:
        return at.replace(tzinfo=timezone.utc)
    return at.astimezone(timezone.utc)


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    return value if isinstance(value, dict) else None


def _load_optional_sources(coord_root: Path, at: datetime) -> tuple[dict, dict, list[str]]:
    """Reuse existing verdict functions; degrade loudly if they are unavailable."""
    gaps: list[str] = []
    handoff: dict = {"schema": None, "seq": -1, "roles": {}, "events": [], "chain_valid": False}
    quota: dict = {"schema": None, "resources": [], "invalid": []}

    try:
        from ..federation import command_handoff

        value = command_handoff.status(coord_root)
        if isinstance(value, dict):
            handoff = value
        else:
            gaps.append("SOURCE_UNAVAILABLE:command_handoff returned a non-object")
    except Exception as exc:  # concurrent edits must not crash the board
        gaps.append(f"SOURCE_UNAVAILABLE:command_handoff:{type(exc).__name__}")

    try:
        from ..relay import resources

        value = resources.snapshot(coord_root, at=at)
        if isinstance(value, dict):
            quota = value
        else:
            gaps.append("SOURCE_UNAVAILABLE:relay.resources returned a non-object")
    except Exception as exc:
        gaps.append(f"SOURCE_UNAVAILABLE:relay.resources:{type(exc).__name__}")

    return handoff, quota, gaps


def _load_claims(coord_root: Path, at: datetime) -> list[dict]:
    rows: list[dict] = []
    claims_root = coord_root / "claims"
    # Claim paths are repository-relative in real data.  Resolve against the
    # project root (the parent of coordination/) without writing anywhere.
    base = coord_root.parent if coord_root.name == "coordination" else Path.cwd()
    for folder in sorted(p for p in claims_root.glob("*") if p.is_dir()):
        owner = _read_json(folder / "owner.json")
        if owner is None:
            continue
        agent = owner.get("agent")
        if not isinstance(agent, str) or not _SAFE.fullmatch(agent):
            continue
        expiry = parse_iso_or_none(owner.get("expires_at"))
        if expiry is not None and expiry < at:
            continue
        paths = owner.get("paths", [])
        if isinstance(paths, str):
            # One real claim wrote paths as a single quoted string.  Tolerate it
            # as evidence, but never rewrite the claim itself.
            paths = [paths]
        if not isinstance(paths, list):
            paths = []
        visible: list[str] = []
        newest = None
        for raw in paths:
            if not isinstance(raw, str):
                continue
            token = raw.strip().strip("'\"")
            if not token or token.startswith("/") or ".." in token.split("/"):
                continue
            candidate = Path(token)
            if not candidate.is_absolute():
                candidate = base / candidate
            targets = [candidate] if candidate.is_file() else list(candidate.glob("**/*")) if candidate.is_dir() else []
            for target in targets[:200]:
                if not target.is_file():
                    continue
                try:
                    changed = datetime.fromtimestamp(target.stat().st_mtime, tz=timezone.utc)
                except OSError:
                    continue
                visible.append(str(target).replace("\\", "/"))
                if newest is None or changed > newest:
                    newest = changed
        rows.append({
            "task": str(owner.get("task", folder.name)),
            "agent": agent,
            "at": owner.get("at"),
            "expires_at": owner.get("expires_at"),
            "declared_paths": [str(p) for p in paths],
            "visible_artifacts": sorted(visible)[:60],
            "visible_artifact_count": len(visible),
            "last_change": newest.isoformat() if newest else None,
            "activity": (
                "STALE" if newest is not None and at - newest > timedelta(hours=STALE_CLAIM_HOURS)
                else "ACTIVE" if newest is not None else "NO_VISIBLE_ARTIFACT"
            ),
        })
    return rows


def _load_capabilities(coord_root: Path, at: datetime) -> tuple[dict[str, dict], list[str]]:
    rows: dict[str, dict] = {}
    gaps: list[str] = []
    for path in sorted((coord_root / "capabilities").glob("*.json")):
        data = _read_json(path)
        if data is None:
            gaps.append(f"INVALID_CAPABILITY_DECLARATION:{path.name}")
            continue
        expiry = parse_iso_or_none(data.get("available_until"))
        fresh = bool(expiry and expiry >= at)
        tokens: list[str] = []
        for key in ("chips", "compilers", "strengths", "capabilities"):
            value = data.get(key, [])
            if isinstance(value, list):
                tokens.extend(str(item) for item in value if isinstance(item, str))
        rows[path.stem] = {
            "agent": data.get("agent", path.stem),
            "fresh": fresh,
            "available_until": data.get("available_until"),
            "tokens": sorted(set(tokens)),
            "evidence": data.get("evidence", []) if isinstance(data.get("evidence"), list) else [],
        }
    if not rows:
        gaps.append("NO_CAPABILITY_DECLARATION")
    return rows, gaps


def _load_presence(coord_root: Path, at: datetime) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for path in sorted((coord_root / "presence" / "agents").glob("*.json")):
        data = _read_json(path)
        if data is None or not isinstance(data.get("agent"), str):
            continue
        state = presence.effective(data, at)
        rows[data["agent"]] = {
            "state": state,
            "source": data.get("source"),
            "observed_at": data.get("observed_at"),
            "expires_at": data.get("expires_at"),
            "session_id": data.get("session_id"),
        }
    return rows


def _load_messages(coord_root: Path, at: datetime) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for folder in sorted(p for p in (coord_root / "messages").glob("*") if p.is_dir()):
        newest_path = None
        newest_at = None
        for path in sorted(folder.glob("*.json")):
            data = _read_json(path)
            moment = parse_iso_or_none(data.get("at")) if data else None
            if moment is None:
                continue
            if newest_at is None or moment > newest_at:
                newest_at, newest_path = moment, path
        if newest_path is None or newest_at is None:
            continue
        data = _read_json(newest_path) or {}
        rows[folder.name] = {
            "id": data.get("id", newest_path.stem),
            "at": newest_at.isoformat(),
            "kind": data.get("kind"),
            "topic": data.get("topic"),
            "sender": data.get("sender"),
            "age_hours": round((at - newest_at).total_seconds() / 3600.0, 2),
        }
    return rows


def _quota_by_agent(quota: dict) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for row in quota.get("resources", []):
        if isinstance(row, dict) and isinstance(row.get("agent"), str):
            rows[row["agent"]] = {
                "status": row.get("state") or "UNKNOWN",
                "available": row.get("available"),
                "capabilities": row.get("capabilities", []),
                "source": row.get("source"),
            }
    return rows


def _capability_tokens(agent: str, caps: dict[str, dict], quota: dict[str, dict]) -> set[str]:
    tokens = set(caps.get(agent, {}).get("tokens", []))
    row = quota.get(agent, {})
    value = row.get("capabilities", [])
    if isinstance(value, list):
        tokens.update(str(item) for item in value if isinstance(item, str))
    return tokens


def _advice_for_role(role: str, spec: dict, agents: dict[str, dict], caps: dict[str, dict],
                     quota: dict[str, dict], claims: list[dict], at: datetime) -> list[dict]:
    holder = spec.get("holder")
    needed = spec.get("required_capability")
    claim_by_agent: dict[str, list[str]] = {}
    for claim in claims:
        claim_by_agent.setdefault(claim["agent"], []).append(claim["task"])

    advice = []
    for agent in sorted(agents):
        if agent == holder:
            continue
        presence_row = agents[agent]
        if presence_row["state"] != "online":
            continue  # stale is evidence expiry, never a take-over proof
        quota_row = quota.get(agent, {})
        if quota_row.get("status") not in ("ACTIVE",):
            continue  # UNDECLARED / EXHAUSTED / EXPIRED never qualifies
        tokens = _capability_tokens(agent, caps, quota)
        if not isinstance(needed, str) or needed not in tokens:
            continue
        reasons = [f"capability:{needed}", "presence:fresh", "quota:ACTIVE"]
        if claim_by_agent.get(agent):
            reasons.append("active_claim:" + ",".join(sorted(claim_by_agent[agent])[:3]))
        advice.append({
            "role": role,
            "candidate": agent,
            "reason_codes": reasons,
            "advisory_only": True,
            "may_advance": False,
            "execution_started": False,
            "requires_human_review": True,
            "dry_run_command": (
                f"aep coord-wake --from workbuddyai --to {agent} "
                f"--task {role}:succession-advice --reason quota-handoff --dry-run"
            ),
            "note": "Dry-run previews a message only. It does not assign the role or prove ACK.",
        })
    return advice[:5]


def build(coord_root: str | Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Compose the board from on-disk evidence. Never writes."""
    root = Path(coord_root)
    at = _now(now)
    handoff, quota, gaps = _load_optional_sources(root, at)
    claims = _load_claims(root, at)
    caps, cap_gaps = _load_capabilities(root, at)
    gaps.extend(cap_gaps)
    people = _load_presence(root, at)
    messages = _load_messages(root, at)
    quota_rows = _quota_by_agent(quota)

    try:
        health = collab_health.snapshot(root, now=at)
    except Exception as exc:
        health = {"schema": None, "verdict": "UNAVAILABLE"}
        gaps.append(f"SOURCE_UNAVAILABLE:collab_health:{type(exc).__name__}")

    holder_roles: dict[str, list[str]] = {}
    roles: list[dict] = []
    advice: list[dict] = []
    for role in sorted(handoff.get("roles", {})):
        spec = handoff["roles"][role]
        if not isinstance(spec, dict):
            continue
        holder = spec.get("holder")
        if isinstance(holder, str) and holder:
            holder_roles.setdefault(holder, []).append(role)
        state = spec.get("state", "UNKNOWN")
        entries = _advice_for_role(role, spec, people, caps, quota_rows, claims, at)
        advice.extend(entries)
        roles.append({
            "role": role,
            "state": state,
            "holder": holder,
            "required_capability": spec.get("required_capability"),
            "chain_valid": bool(handoff.get("chain_valid")),
            "candidate_count": len(entries),
            "coverage": "HELD" if state == "ACTIVE" and holder else "UNCOVERED",
        })
        if state != "ACTIVE" or not holder:
            gaps.append(f"ROLE_NOT_HELD:{role}")

    agents: list[dict] = []
    for agent in sorted(set(people) | set(caps) | set(quota_rows) | set(holder_roles) | {c["agent"] for c in claims}):
        p = people.get(agent, {})
        q = quota_rows.get(agent, {})
        c = caps.get(agent, {})
        agents.append({
            "agent": agent,
            "presence": p.get("state", "UNKNOWN"),
            "presence_source": p.get("source"),
            "quota": q.get("status", "UNDECLARED"),
            "capability_fresh": bool(c.get("fresh")),
            "capability_tokens": c.get("tokens", []),
            "holder_roles": sorted(holder_roles.get(agent, [])),
            "claims": sorted(cl["task"] for cl in claims if cl["agent"] == agent),
            "last_message": messages.get(agent),
            "message_is_ack": False,
            "note": "Mailbox/message evidence is not ACK and not delivery.",
        })
        if agent not in quota_rows:
            gaps.append(f"QUOTA_UNDECLARED:{agent}")
        if p.get("state") != "online":
            gaps.append(f"NO_FRESH_PRESENCE:{agent}")

    if not any(row["state"] == "online" for row in people.values()):
        gaps.append("NO_FRESH_ONLINE_AGENT")

    delivery = health.get("delivery", {}) if isinstance(health, dict) else {}
    required = delivery.get("next_required", {})
    pending = required.get("AGENT_ACK") if isinstance(required, dict) else None
    if pending:
        gaps.append(f"ACK_PENDING:{pending}")

    for claim in claims:
        if claim["activity"] == "STALE":
            gaps.append(f"CLAIM_ACTIVITY_STALE:{claim['task']}:{claim['agent']}")

    verdict = "ACTIONABLE" if advice and not any(g.startswith("SOURCE_UNAVAILABLE") for g in gaps) else "GAPS"
    if any(g.startswith("SOURCE_UNAVAILABLE") for g in gaps):
        verdict = "DEGRADED"

    return {
        "schema": SCHEMA,
        "at": at.isoformat(),
        "coord_root": str(root),
        "verdict": verdict,
        "advisory_only": True,
        "may_advance": False,
        "execution_started": False,
        "roles": roles,
        "agents": agents,
        "claims": claims,
        "gaps": sorted(set(gaps)),
        "takeover_advice": advice,
        "health_verdict": health.get("verdict", "UNAVAILABLE"),
        "note": NOTE,
    }


def brief(board: dict) -> str:
    lines = [f"接手态势板 · {board['at']} · {board['verdict']}"]
    lines.append("角色：")
    for row in board["roles"]:
        lines.append(f"  - {row['role']}: {row['coverage']} / {row['state']} / holder={row['holder']}")
    lines.append("Agent：")
    for row in board["agents"]:
        lines.append(
            f"  - {row['agent']}: presence={row['presence']}, quota={row['quota']}, "
            f"claims={','.join(row['claims']) or '-'}"
        )
    lines.append(f"缺口（{len(board['gaps'])}）：")
    for gap in board["gaps"][:20]:
        lines.append(f"  - {gap}")
    lines.append(f"接手建议（{len(board['takeover_advice'])}，全部 advisory-only）：")
    for item in board["takeover_advice"]:
        lines.append(f"  - {item['role']} ← {item['candidate']}: {','.join(item['reason_codes'])}")
    lines.append(board["note"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="succession-board",
        description="Read-only succession situation board; advice never advances a role or sends.",
    )
    parser.add_argument("--coord-root", default="coordination", help="coordination root (default: coordination)")
    parser.add_argument("--at", default=None, help="ISO 8601 timestamp for deterministic replay")
    parser.add_argument("--format", choices=("json", "brief"), default="json")
    args = parser.parse_args(argv)
    at = parse_iso_or_none(args.at) if args.at else None
    if args.at and at is None:
        parser.error("--at must be an ISO 8601 timestamp")
    board = build(args.coord_root, now=at)
    if args.format == "brief":
        print(brief(board))
    else:
        print(json.dumps(board, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
