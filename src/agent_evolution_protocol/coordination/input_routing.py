"""Decide which leg runs, and what happens when neither of them works.

The perception leg (``vision_grounding``) and the policy leg
(``policy_executor``) are not rivals and neither is the other's spare part:
they fail differently, so the routing order follows what each one can actually
prove.

* **UIA can prove it found *the* input.** ``input_found`` with the adapter not
  reporting ambiguity is an observation, whatever the candidate count.  When
  that exists, asking a vision model the same question produces a second,
  weaker answer about a question that was already settled -- so the model is
  skipped, not consulted.  Consulting it anyway is how a good deterministic
  answer gets outvoted by a guess.
* **UIA is silent when it finds nothing or finds three candidates.** That is
  where a vision proposal earns its place: it is admissible only if it clears
  the confidence and window tests, and even then it is a guess, so the step it
  drives must be verified before anything is sent.
* **Geometry is the last resort and is never allowed to look confident.** A
  configured region that is right today is exactly the thing that types into
  the wrong box after a UI change, so it always requires verification.
* **When none of the three applies, or the policy run left a write step
  unproven, the answer is defer.** Not "try anyway": a deferral costs a retry,
  a wrong send costs a message the operator cannot unsend.

Everything here is a decision record.  Nothing in this module touches a window,
types a character, or claims a delivery; the evidence ladder still owns that.
"""
from __future__ import annotations

from typing import Any

from agent_evolution_protocol.coordination import vision_grounding

LOCATE_SOURCES = ("uia", "vision", "geometry")

# UIA located the control; vision and geometry guessed at it.  The distinction
# is the whole reason this module exists, so it is data rather than a comment.
NEEDS_VERIFICATION = {"uia": False, "vision": True, "geometry": True}

# A read that cannot be verified is just a read.  A write that cannot be
# verified is an unproven change to a live desktop.
WRITE_OPERATIONS = {"click", "type", "keys"}


def _decision(source: str | None, *, reason: str, considered: list[dict[str, Any]],
              region: dict[str, int] | None = None, note: str = "",
              blocked_by: str | None = None) -> dict[str, Any]:
    return {
        "locate_source": source,
        "region": region,
        "reason": reason,
        "needs_verification": NEEDS_VERIFICATION.get(source) if source else None,
        "blocked_by": blocked_by,
        "note": note,
        "considered": considered,
    }


def candidate_count(observation: dict[str, Any]) -> int:
    """How many inputs UIA claims to have found.

    ``input_candidate_count`` is authoritative when present; the older
    ``input_found`` boolean is the fallback so a receipt from an older executor
    is read as "one, probably" rather than silently as zero.
    """
    count = observation.get("input_candidate_count")
    if isinstance(count, bool):
        return 1 if count else 0
    if isinstance(count, int):
        return max(0, count)
    return 1 if observation.get("input_found") else 0


def is_ambiguous(observation: dict[str, Any]) -> bool:
    """Whether UIA could not settle on one control.

    ``input_ambiguous`` is the adapter's own verdict and wins when present: it
    is entirely possible to find two candidate inputs and still know which one
    is the composer (the measured ChatGPT receipt reports
    ``input_candidate_count: 2`` with no ambiguity).  Treating a count of two as
    ambiguity would have thrown away a working path, so the count is only a
    fallback for receipts that predate the flag.
    """
    flag = observation.get("input_ambiguous")
    if isinstance(flag, bool):
        return flag
    return candidate_count(observation) > 1


def resolve_target(observation: dict[str, Any], *, window: vision_grounding.Region,
                   grounder: Any = None, reference: vision_grounding.Region | None = None,
                   min_confidence: float = vision_grounding.DEFAULT_MIN_CONFIDENCE,
                   target: str = "composer") -> dict[str, Any]:
    """Pick the cheapest leg that can actually locate ``target``.

    ``observation`` is a ``coord-uia observe`` receipt.  ``grounder`` is any
    object with ``ground(request) -> Proposal | None``; ``NullGrounder`` is the
    honest default because it has no opinion instead of a cheap one.
    """
    considered: list[dict[str, Any]] = []

    # Nothing downstream matters if the executor cannot type: UIPI refuses the
    # input, not the sight of the box.  Deciding this first stops the run from
    # burning a probe budget on a question whose answer is already "no".
    if observation.get("drivable") is False:
        return _decision(None, reason="EXECUTOR_TOO_LOW", considered=["drivability"],
                         blocked_by=observation.get("blocked_by"),
                         note="UIPI refuses input from a lower integrity level; "
                              "seeing the control would not let anything be typed")

    count = candidate_count(observation)
    ambiguous = is_ambiguous(observation)
    considered.append({"source": "uia", "candidates": count, "ambiguous": ambiguous,
                       "write_method": observation.get("write_method")})
    if observation.get("input_found") and not ambiguous:
        region = observation.get("input_region")
        return _decision(
            "uia", reason="", considered=considered,
            region=region if isinstance(region, dict) else None,
            note="UIA settled on one control, so no model is asked the same question")

    # Ambiguous or absent: this is the only window in which perception adds
    # information rather than noise.
    proposal = None
    if grounder is not None and window is None:
        # A 0-999 answer has no meaning without a size to multiply it by, so
        # asking would produce a number nobody could place on screen.
        considered.append({"source": "vision", "admissible": False,
                           "reason": "NO_WINDOW_GEOMETRY"})
    elif grounder is not None:
        try:
            proposal = grounder.ground({"target": target, "window": window,
                                        "image_data": observation.get("image_data", "")})
        except Exception as exc:  # noqa: BLE001 - a grounder failure is "no opinion"
            considered.append({"source": "vision", "admissible": False,
                               "reason": f"GROUNDER_FAILED:{type(exc).__name__}"})
            proposal = None
        else:
            ok, why = vision_grounding.admissible(proposal, window=window,
                                                  min_confidence=min_confidence)
            considered.append({"source": "vision", "admissible": ok, "reason": why})
            if ok:
                return _decision(
                    "vision", reason="", considered=considered,
                    region=proposal.region.as_dict(),
                    note=f"{count} UIA candidates, so the proposal was admissible; "
                         "it is still a guess and the step must be verified")

    if reference is not None:
        considered.append({"source": "geometry", "admissible": True,
                           "reason": "CONFIGURED_REGION"})
        return _decision(
            "geometry", reason="", considered=considered, region=reference.as_dict(),
            note="a configured region cannot tell when the layout moved, "
                 "so it always needs verification")

    return _decision(None, reason="NO_TARGET", considered=considered,
                     note="UIA was not decisive, no grounder was admissible, "
                          "and no region was configured")


def unproven_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Steps a policy run cannot account for.

    ``verified is False`` is an explicit failure.  ``verified is None`` means no
    verifier was installed, which for a read is fine and for a write is an
    assumption -- and an assumption about a click is what this module is for.
    """
    return [step for step in steps
            if step.get("verified") is False
            or (step.get("verified") is None and step.get("operation") in WRITE_OPERATIONS)]


def delivery_verdict(*, locate: dict[str, Any],
                     policy_result: dict[str, Any] | None = None) -> dict[str, Any]:
    """Should anything be sent?  Defaults to defer.

    The three ways to reach "send" are all evidence-shaped: the input was
    located by something that can prove it, every write step the policy took is
    accounted for, and the policy stopped because it finished rather than
    because it ran out of budget.
    """
    if not locate.get("locate_source"):
        return {"action": "defer", "state": "DEFERRED",
                "reason": locate.get("reason") or "NO_TARGET",
                "note": "nothing located the input, so nothing is typed"}

    if policy_result is not None:
        steps = policy_result.get("steps") or []
        unproven = unproven_steps(steps)
        if unproven:
            first = unproven[0]
            return {"action": "defer", "state": "DEFERRED", "reason": "STEP_NOT_VERIFIED",
                    "step": first.get("index"), "operation": first.get("operation"),
                    "note": "a write step is unaccounted for; deferring beats sending blind"}
        stopped = str(policy_result.get("stopped_because") or "")
        if stopped != "policy-finished":
            return {"action": "defer", "state": "DEFERRED", "reason": stopped or "UNKNOWN_STOP",
                    "note": "the policy did not run to completion"}

    if locate.get("needs_verification"):
        return {"action": "send-after-verification", "state": "READY_UNVERIFIED",
                "reason": f"LOCATED_BY_{str(locate['locate_source']).upper()}",
                "note": "confirm the input received the text before sending"}
    return {"action": "send", "state": "READY", "reason": "",
            "note": "the location is an observation, not a guess"}
