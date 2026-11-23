"""Fault-injection drill: quota exhaustion -> eligible successor takes over.

This module exercises the *real* handoff state machine and capability bridge
in an isolated coordination root. It never touches the live ``coordination/``
directory, never sends a desktop message, and never fabricates an ACK: the
result describes a replayable state-machine drill, not a real cross-vendor
agent having accepted work.

Hard properties asserted by ``run()``:
  1. A capability-ineligible volunteer is recorded but cannot take over.
  2. Only an eligible successor becomes the single ACTIVE holder.
  3. Replaying yield / volunteer / takeover is idempotent (no extra event).
  4. The hash-linked evidence chain validates after the whole sequence.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

from . import capability_bridge, command_handoff

SCHEMA = "aep-capacity-drill-v1"

# Evidence strings are drill fixtures, deliberately prefixed so nobody can
# mistake them for a real agent's self-report or an original-session ACK.
_DRILL = "fault-injection-drill"
COMMANDER = "commander"


def _evidence(label: str) -> list[str]:
    return [f"{_DRILL}:{label}"]


def run(root: Path | None = None) -> dict:
    """Run the drill in ``root`` (a throwaway temp dir when omitted)."""
    owned = root is None
    coord = Path(root) if root is not None else Path(tempfile.mkdtemp(prefix="aep-capacity-drill-"))
    checks: dict[str, bool] = {}
    try:
        # 1. Establish the initial commander via the role contract vocabulary.
        required = capability_bridge.handoff_capability(COMMANDER)
        mapped = capability_bridge.map_capabilities(COMMANDER, ["desktop-ui"])
        claim = command_handoff.claim(
            coord, agent="holder_alpha", role=COMMANDER,
            capability=mapped["capability"],
            evidence=_evidence("initial-claim"))
        checks["initial_claim_active"] = claim["state"] == command_handoff.ACTIVE

        # 2. A weak candidate registers but is ineligible, then is refused.
        weak = capability_bridge.eligibility(COMMANDER, ["research"])
        checks["weak_ineligible"] = weak["eligible"] is False
        weak_vol = command_handoff.volunteer(
            coord, from_agent="weak_beta", role=COMMANDER,
            capability=weak["capability"], reason="offers to help",
            evidence=_evidence("weak-volunteer"))
        checks["weak_recorded_ineligible"] = weak_vol["eligible"] is False

        # 3. An eligible successor registers through the capability bridge.
        strong = capability_bridge.eligibility(COMMANDER, ["desktop-ui"])
        checks["successor_eligible"] = strong["eligible"] is True
        command_handoff.volunteer(
            coord, from_agent="successor_gamma", role=COMMANDER,
            capability=strong["capability"], reason="ready to take over on quota loss",
            evidence=_evidence("successor-volunteer"))

        # 4. Quota exhaustion: ACTIVE -> YIELDED, with idempotent replay.
        yielded = command_handoff.yield_role(
            coord, from_agent="holder_alpha", role=COMMANDER,
            signal="quota_exhausted", evidence=_evidence("quota-exhausted"))
        checks["yielded_on_quota"] = yielded["state"] == command_handoff.YIELDED
        yield_again = command_handoff.yield_role(
            coord, from_agent="holder_alpha", role=COMMANDER,
            signal="quota_exhausted", evidence=_evidence("quota-exhausted"))
        checks["yield_idempotent"] = yield_again["idempotent"] is True

        # 5. Ineligible volunteer cannot take over (fail-closed).
        refused = False
        try:
            command_handoff.takeover(
                coord, to_agent="weak_beta", role=COMMANDER,
                evidence=_evidence("weak-takeover-attempt"))
        except ValueError:
            refused = True
        checks["weak_takeover_refused"] = refused

        # 6. Eligible successor takes over atomically.
        takeover = command_handoff.takeover(
            coord, to_agent="successor_gamma", role=COMMANDER,
            evidence=_evidence("successor-takeover"))
        checks["successor_active"] = takeover["state"] == command_handoff.ACTIVE
        checks["transition_recorded"] = takeover["transition"] == [
            command_handoff.YIELDED, command_handoff.TAKING_OVER, command_handoff.ACTIVE]

        # 7. Replay is idempotent: no second handoff record or event.
        replay = command_handoff.takeover(
            coord, to_agent="successor_gamma", role=COMMANDER,
            evidence=_evidence("successor-takeover"))
        checks["takeover_idempotent"] = replay["idempotent"] is True

        # 8. Persistent state + complete hash-linked evidence chain.
        report = command_handoff.status(coord)
        role = report["roles"][COMMANDER]
        checks["chain_valid"] = report["chain_valid"] is True
        checks["single_active_holder"] = role["holder"] == "successor_gamma"
        checks["one_handoff_record"] = len(role["handoffs"]) == 1
        checks["state_persisted"] = (coord / command_handoff.STATE_FILE).is_file()

        passed = all(checks.values())
        return {
            "schema": SCHEMA,
            "verdict": "PASS" if passed else "FAIL",
            "isolated_root": str(coord),
            "owned_temp_dir": owned,
            "checks": checks,
            "handoff_id": takeover["handoff_id"],
            "final_holder": role["holder"],
            "event_count": len(report["events"]),
            "chain_valid": report["chain_valid"],
            "note": ("Simulated fault injection with the real handoff state machine. "
                     "Not a real agent ACK, not a desktop delivery, not proof that any "
                     "vendor client accepted work. The isolated root is retained for "
                     "audit and can be replayed with an explicit root."),
        }
    except Exception as exc:  # a drill failure is a result, not a crash
        return {
            "schema": SCHEMA,
            "verdict": "FAIL",
            "isolated_root": str(coord),
            "owned_temp_dir": owned,
            "checks": checks,
            "error": f"{type(exc).__name__}: {exc}",
            "note": "Drill aborted; isolated root retained for post-mortem.",
        }


def main() -> int:
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
