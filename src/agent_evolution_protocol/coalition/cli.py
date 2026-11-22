"""CLI surface for the coalition planner, capacity ledger and guarded dispatch."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import capacity, dispatch, io, planner, spec as _spec


def _protocol_error(message):
    from agent_evolution_protocol.cli import ProtocolError
    return ProtocolError(message)


def _root(args):
    from agent_evolution_protocol.cli import coord_root
    override = getattr(args, "coalition_coord_root", None)
    return Path(override) if override else coord_root(args)


def _print(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _guard(handler, args):
    try:
        return handler(args)
    except ValueError as exc:
        raise _protocol_error(str(exc)) from exc


def _validate(args):
    """Validate a mission spec and print its normalized surface."""
    mission = _spec.load_spec(args.mission)
    _print({
        "schema": "aep-coalition-spec-summary-v1",
        "source": str(args.mission),
        "mission": mission["mission"],
        "description": mission["description"],
        "agents": [{"agent": item["agent"], "vendor": item["vendor"],
                    "capabilities": item["capabilities"],
                    "max_primary_roles": item["max_primary_roles"],
                    "subagent_slots": item["subagent_slots"]}
                   for item in mission["agents"]],
        "roles": [{"id": item["id"], "criticality": item["criticality"],
                   "required_capabilities": item["required_capabilities"],
                   "acting_capabilities": item["acting_capabilities"],
                   "allow_scribe_fallback": item["allow_scribe_fallback"],
                   "write_scope": item["write_scope"]}
                  for item in mission["roles"]],
        "current_holders": mission["current_holders"],
        "vendor_policy": mission["vendor_policy"],
        "valid": True,
    })


def _emit(args, payload, *, summary):
    if args.out:
        io.atomic_write_json(args.out, payload)
        summary = dict(summary)
        summary["out"] = str(Path(args.out))
        _print(summary)
    else:
        _print(payload)


def _plan(args):
    """Build the deterministic primary/backup report without dispatching."""
    report = planner.plan(_root(args), args.mission, now=args.now)
    _emit(args, report, summary={
        "schema": report["schema"],
        "mission": report["mission"],
        "at": report["at"],
        "verdict": report["verdict"],
        "plan_digest": report["plan_digest"],
        "roles": {role_id: {"coverage": item["coverage"],
                            "primary": item["primary"]["agent"] if item["primary"] else None,
                            "action": item["tick"]["action"]}
                  for role_id, item in report["roles"].items()},
    })


def _tick(args):
    """Print the next coalition actions; nothing is dispatched."""
    report = planner.tick(_root(args), args.mission, now=args.now)
    _emit(args, report, summary={
        "schema": report["schema"],
        "mission": report["mission"],
        "at": report["at"],
        "verdict": report["verdict"],
        "plan_digest": report["plan_digest"],
        "actions": [{"role": item["role"], "action": item["action"],
                     "target": item["target"], "execute_allowed": item["execute_allowed"]}
                    for item in report["actions"]],
    })


def _capacity_set(args):
    """Record one expiring capacity observation from a named source."""
    payload = capacity.record(
        _root(args), agent=args.agent, vendor=args.vendor, state=args.state,
        source=args.source, evidence=args.evidence,
        remaining_fraction=args.remaining_fraction,
        remaining_seconds=args.remaining_seconds, resets_at=args.resets_at,
        ttl_minutes=args.ttl_minutes, now=args.now)
    _print(payload)


def _capacity_status(args):
    """Read effective capacity states, never promoting stale evidence."""
    _print(capacity.status(_root(args), agents=args.agents or None, now=args.now))


def _presence_signal(args):
    from ..coordination import presence
    _print(presence.signal(
        _root(args), agent=args.agent, state=args.state, source=args.source,
        session_id=args.session_id, ttl_minutes=args.ttl_minutes,
        reason=args.reason))


def _presence_status(args):
    from ..coordination import presence
    _print(presence.snapshot(_root(args)))


def _presence_reconcile(args):
    from ..coordination import presence
    at = io.coerce_utc(args.now, "now") if args.now else None
    _print(presence.reconcile(_root(args), at=at))


def _presence_next_due(args):
    from ..coordination import presence
    _print({"next_due": presence.next_due(_root(args))})


def _takeover(args):
    """Preview or execute one guarded handoff exactly once."""
    payload = dispatch.takeover(
        _root(args), args.mission, role=args.role, to_agent=args.to_agent,
        trigger=args.trigger, sender=args.sender, evidence=args.evidence,
        from_agent=args.from_agent, authority=args.authority,
        session_id=args.session_id, reply_minutes=args.reply_minutes,
        task=args.task or "", bridge_root=args.coalition_bridge_root,
        execute=args.execute, now=args.now)
    _print(payload)


def _handoffs(args):
    """List durable handoff records without resending anything."""
    _print(dispatch.status(_root(args), key=args.key))


def _handoff_reconcile(args):
    """Refresh handoff records from delegation receipts, never resend."""
    _print(dispatch.reconcile(_root(args), key=args.key))


def _handoff_ack(args, accepted=True):
    """Record an explicit target ACK or decline for one handoff."""
    _print(dispatch.acknowledge(
        _root(args), key=args.key, agent=args.agent,
        session_id=args.session_id, reply=args.reply, accepted=accepted))


def _handoff_decline(args):
    """Record an explicit target decline for one handoff."""
    return _handoff_ack(args, accepted=False)


def register_presence(commands):
    presence = commands.add_parser(
        "coord-presence",
        help="signal, inspect or reconcile team presence evidence",
        description="Signal, inspect or reconcile expiring team presence evidence.")
    presence.add_argument("action", choices=("signal", "status", "reconcile",
                                              "next-due"))
    presence.add_argument("--agent")
    presence.add_argument("--state", choices=("online", "offline"))
    presence.add_argument("--source", default="manual")
    presence.add_argument("--session-id", default="")
    presence.add_argument("--ttl-minutes", type=int, default=30)
    presence.add_argument("--reason", default="")
    presence.add_argument("--now", help="ISO evaluation instant for reconcile")
    presence.add_argument("--coord-root", dest="coalition_coord_root", type=Path,
                          default=argparse.SUPPRESS,
                          help="coordination root (else the global --coord-root)")
    presence.set_defaults(func=lambda args: _guard(
        {"signal": _presence_signal, "status": _presence_status,
         "reconcile": _presence_reconcile, "next-due": _presence_next_due}[
             args.action], args))


def register(commands):
    coalition = commands.add_parser(
        "coord-coalition",
        help="vendor-diverse succession: capacity ledger, plan, tick and guarded takeover",
        description="Plan vendor-diverse role succession and execute guarded handoffs.")
    coalition.add_argument(
        "action",
        choices=("validate", "plan", "tick", "capacity-set", "capacity-status",
                 "takeover", "handoffs", "handoff-reconcile", "handoff-status",
                 "handoff-ack", "handoff-decline"))
    coalition.add_argument("--mission", "--spec", dest="mission",
                           help="mission spec JSON path")
    coalition.add_argument("--out", type=Path, help="write the full report here")
    coalition.add_argument("--now", help="ISO evaluation instant")
    coalition.add_argument("--coord-root", dest="coalition_coord_root", type=Path,
                           default=argparse.SUPPRESS,
                           help="coordination root (else the global --coord-root)")
    coalition.add_argument("--bridge-root", dest="coalition_bridge_root", type=Path,
                           default=None,
                           help="mailbox/wake root; defaults to the coordination root")
    coalition.add_argument("--agent")
    coalition.add_argument("--agents", nargs="*", default=[],
                           help="capacity-status: agents to inspect; default all")
    coalition.add_argument("--vendor")
    coalition.add_argument("--state", choices=capacity.STATES + ("online", "offline"))
    coalition.add_argument("--remaining-fraction", type=float)
    coalition.add_argument("--remaining-seconds", type=float)
    coalition.add_argument("--resets-at")
    coalition.add_argument("--source", default="manual")
    coalition.add_argument("--evidence", nargs="*", default=[])
    coalition.add_argument("--ttl-minutes", type=int, default=60)
    coalition.add_argument("--role")
    coalition.add_argument("--from", dest="from_agent")
    coalition.add_argument("--to", dest="to_agent")
    coalition.add_argument("--trigger", choices=dispatch.ALLOWED_TRIGGERS)
    coalition.add_argument("--authority", choices=("primary", "acting", "scribe"))
    coalition.add_argument("--sender")
    coalition.add_argument("--session-id", default="")
    coalition.add_argument("--reply-minutes", type=int, default=30)
    coalition.add_argument("--task")
    coalition.add_argument("--key")
    coalition.add_argument("--reply")
    mode = coalition.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="preview only (default)")
    mode.add_argument("--execute", action="store_true",
                      help="perform the guarded dispatch exactly once")
    coalition.set_defaults(func=_dispatch_action)


def _dispatch_action(args):
    handlers = {
        "validate": _validate,
        "plan": _plan,
        "tick": _tick,
        "capacity-set": _capacity_set,
        "capacity-status": _capacity_status,
        "takeover": _takeover,
        "handoffs": _handoffs,
        "handoff-reconcile": _handoff_reconcile,
        "handoff-status": _handoffs,
        "handoff-ack": _handoff_ack,
        "handoff-decline": _handoff_decline,
    }
    handler = handlers[args.action]
    if args.action in ("validate", "plan", "tick", "takeover") and not args.mission:
        raise _protocol_error("--mission is required for " + args.action)
    return _guard(handler, args)
