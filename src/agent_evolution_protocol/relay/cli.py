"""Standalone CLI for the quota-aware command relay.

The main ``aep`` parser belongs to another active claim, so this module keeps
its command surface inside the relay package.  It can be run as::

    python -m agent_evolution_protocol.relay --coord-root coordination ...

Nothing here changes role state by itself beyond the explicit relay actions;
``plan`` and ``status`` are read-only.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from ..federation import command_handoff
from ..federation.diagnosis import CAPACITY_SIGNALS
from . import duty as relay_duty
from . import resources as relay_resources


class RelayCliError(RuntimeError):
    pass


def _relay_checkpoint(args: argparse.Namespace, *, required: bool = True):
    path = getattr(args, "checkpoint_file", None)
    if path:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except OSError as exc:
            raise RelayCliError(f"cannot read checkpoint file: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RelayCliError(f"checkpoint file is not JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise RelayCliError("checkpoint file must contain a JSON object")
        return data
    if getattr(args, "next_step", None):
        return {
            "next_step": args.next_step,
            "completed": list(getattr(args, "completed", []) or []),
            "artifacts": list(getattr(args, "artifacts", []) or []),
            "pending_operations": [],
            "unknowns": list(getattr(args, "unknowns", []) or []),
        }
    if required:
        raise RelayCliError("--checkpoint-file or --next-step is required")
    return None


def coord_command_relay(args: argparse.Namespace) -> None:
    """Quota-aware seats: declare, heartbeat, plan, offer, accept and takeover."""
    root = Path(args.coord_root)
    action = args.action
    evidence = list(getattr(args, "evidence", []) or [])
    bridge = getattr(args, "bridge_root", None)
    dispatch = bool(getattr(args, "dispatch", False))
    try:
        if action == "declare":
            if args.remaining is None:
                raise RelayCliError("declare requires --remaining")
            result = relay_resources.declare(
                root, agent=args.agent, provider=args.provider, unit=args.unit,
                remaining=args.remaining, reserve=args.reserve, limit=args.limit,
                reset_at=args.reset_at or "", source=args.source, evidence=evidence,
                capabilities=getattr(args, "capabilities", None),
                subagent_children=args.subagent_children,
                subagent_units=args.subagent_units,
                ttl_minutes=args.ttl_minutes)
        elif action == "exhausted":
            result = relay_resources.mark_exhausted(
                root, agent=args.agent, reason=args.reason, evidence=evidence,
                source=args.source)
        elif action == "gate":
            result = relay_resources.gate(
                root, agent=args.agent, units=args.units, children=args.children,
                allow_unknown=bool(args.allow_unknown))
        elif action == "register":
            result = command_handoff.register_role(
                root, role=args.role, capability=args.capability)
        elif action == "claim":
            result = command_handoff.claim(
                root, agent=args.agent, role=args.role, capability=args.capability,
                evidence=evidence)
        elif action == "volunteer":
            result = command_handoff.volunteer(
                root, from_agent=args.agent, role=args.role,
                capability=args.capability, reason=args.reason, evidence=evidence)
        elif action == "yield":
            result = command_handoff.yield_role(
                root, from_agent=args.agent, role=args.role, signal=args.signal,
                evidence=evidence)
        elif action == "beat":
            result = relay_duty.beat(
                root, agent=args.agent, role=args.role, evidence=evidence,
                checkpoint=_relay_checkpoint(args),
                ttl_minutes=getattr(args, "ttl_minutes", None))
        elif action == "plan":
            result = relay_duty.plan(root, args.role,
                                     capability=getattr(args, "capability", "") or "")
        elif action == "offer":
            result = relay_duty.offer(
                root, actor=args.agent, role=args.role, to=args.to or "",
                signal=args.signal, reason=args.reason, evidence=evidence,
                checkpoint=_relay_checkpoint(args),
                expires_minutes=getattr(args, "expires_minutes", None),
                bridge_root=bridge, dispatch=dispatch,
                nonce=getattr(args, "nonce", "") or "")
        elif action == "accept":
            result = relay_duty.accept(
                root, agent=args.agent, role=args.role, offer_id=args.offer_id,
                checkpoint_sha256=args.checkpoint_sha256, evidence=evidence,
                bridge_root=bridge, dispatch=dispatch)
        elif action == "decline":
            result = relay_duty.decline(
                root, agent=args.agent, role=args.role, offer_id=args.offer_id,
                reason=args.reason, bridge_root=bridge)
        elif action == "takeover":
            result = relay_duty.takeover_vacant(
                root, agent=args.agent, role=args.role, evidence=evidence,
                checkpoint=_relay_checkpoint(args, required=False),
                bridge_root=bridge, dispatch=dispatch)
        elif action == "status":
            result = relay_duty.summary(root)
        else:
            raise RelayCliError(f"unknown action: {action}")
    except (ValueError, TypeError, OSError) as exc:
        raise RelayCliError(str(exc)) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aep-relay",
        description="Quota-aware command relay: heartbeat, succession and handoff")
    parser.add_argument(
        "--coord-root", type=Path,
        default=Path(os.environ.get("AEP_COORD_ROOT", "coordination")),
        help="coordination root (default: AEP_COORD_ROOT or ./coordination)")
    parser.add_argument("action", choices=(
        "declare", "exhausted", "gate", "register", "claim", "volunteer",
        "yield", "beat", "plan", "offer", "accept", "decline", "takeover",
        "status"))
    parser.add_argument("--agent")
    parser.add_argument("--role")
    parser.add_argument("--provider")
    parser.add_argument("--unit", choices=relay_resources.UNITS, default="credits")
    parser.add_argument("--remaining", type=int)
    parser.add_argument("--reserve", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--reset-at", default="")
    parser.add_argument("--source", choices=relay_resources.SOURCES, default="manual")
    parser.add_argument("--capabilities", nargs="*", default=None,
                      help="declare: capability tokens this agent can cover")
    parser.add_argument("--capability",
                      help="register/claim/volunteer/plan: capability token")
    parser.add_argument("--subagent-children", type=int)
    parser.add_argument("--subagent-units", type=int)
    parser.add_argument("--ttl-minutes", type=int)
    parser.add_argument("--evidence", nargs="*", default=[])
    parser.add_argument("--reason")
    parser.add_argument("--signal", choices=tuple(sorted(CAPACITY_SIGNALS)))
    parser.add_argument("--checkpoint-file", type=Path,
                      help="JSON checkpoint with next_step and work lists")
    parser.add_argument("--next-step")
    parser.add_argument("--completed", nargs="*", default=[])
    parser.add_argument("--artifacts", nargs="*", default=[])
    parser.add_argument("--unknowns", nargs="*", default=[])
    parser.add_argument("--to")
    parser.add_argument("--offer-id")
    parser.add_argument("--checkpoint-sha256")
    parser.add_argument("--expires-minutes", type=int)
    parser.add_argument("--nonce", default="")
    parser.add_argument("--dispatch", action="store_true",
                      help="attempt exactly one real wake dispatch; never replayed")
    parser.add_argument("--bridge-root", type=Path)
    parser.add_argument("--units", type=int, default=1)
    parser.add_argument("--children", type=int, default=0)
    parser.add_argument("--allow-unknown", action="store_true")
    parser.set_defaults(func=coord_command_relay)
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        args = build_parser().parse_args()
        args.func(args)
    except RelayCliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())