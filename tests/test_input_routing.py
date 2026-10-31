import contextlib
import io
import json
from pathlib import Path

import pytest

from agent_evolution_protocol import cli
from agent_evolution_protocol.coordination import input_routing
from agent_evolution_protocol.coordination.input_routing import (
    candidate_count,
    delivery_verdict,
    resolve_target,
    unproven_steps,
)
from agent_evolution_protocol.coordination.vision_grounding import (
    NullGrounder,
    Proposal,
    Region,
    StaticGrounder,
)


WINDOW = Region(x=0, y=0, width=1920, height=1080)
COMPOSER = Region(x=700, y=960, width=500, height=60)


def _proposal(confidence=0.9, region=COMPOSER):
    return Proposal(target="composer", region=region, confidence=confidence, source="static")


def _observe(**overrides):
    base = {"drivable": True, "input_found": True, "input_candidate_count": 1,
            "write_method": "uia-valuepattern"}
    base.update(overrides)
    return base


class TestUiaComesFirstWhenItIsDecisive:
    """UIA can prove it found *the* input; a model can only guess at it."""

    def test_one_candidate_is_taken_and_the_model_is_not_asked(self):
        grounder = StaticGrounder({"composer": _proposal()})
        decision = resolve_target(_observe(), window=WINDOW, grounder=grounder)
        assert decision["locate_source"] == "uia"
        assert decision["needs_verification"] is False
        assert grounder.calls == 0, "a settled question must not be put to a model"

    def test_the_reason_says_why_no_model_was_consulted(self):
        decision = resolve_target(_observe(), window=WINDOW, grounder=StaticGrounder({}))
        assert "exactly one candidate" in decision["note"]

    def test_an_older_receipt_without_a_count_still_counts_as_one(self):
        decision = resolve_target({"input_found": True}, window=WINDOW)
        assert decision["locate_source"] == "uia"

    def test_the_uia_region_is_passed_through_when_the_receipt_has_one(self):
        region = {"x": 700, "y": 960, "width": 500, "height": 60}
        decision = resolve_target(_observe(input_region=region), window=WINDOW)
        assert decision["region"] == region

    def test_a_receipt_without_a_region_reports_none_rather_than_inventing_one(self):
        decision = resolve_target(_observe(), window=WINDOW)
        assert decision["region"] is None
        assert decision["locate_source"] == "uia"


class TestVisionIsTheFallbackOnlyWhenUiaIsSilent:
    def test_three_candidates_are_ambiguous_so_the_model_is_asked(self):
        grounder = StaticGrounder({"composer": _proposal()})
        decision = resolve_target(_observe(input_candidate_count=3), window=WINDOW,
                                  grounder=grounder)
        assert grounder.calls == 1
        assert decision["locate_source"] == "vision"
        assert decision["needs_verification"] is True

    def test_nothing_found_also_asks_the_model(self):
        grounder = StaticGrounder({"composer": _proposal()})
        decision = resolve_target(_observe(input_found=False, input_candidate_count=0),
                                  window=WINDOW, grounder=grounder)
        assert decision["locate_source"] == "vision"

    def test_a_low_confidence_proposal_is_not_adopted(self):
        grounder = StaticGrounder({"composer": _proposal(confidence=0.2)})
        decision = resolve_target(_observe(input_candidate_count=2), window=WINDOW,
                                  grounder=grounder, reference=COMPOSER)
        assert decision["locate_source"] == "geometry"
        reasons = [row.get("reason") for row in decision["considered"] if row["source"] == "vision"]
        assert reasons == ["LOW_CONFIDENCE"]

    def test_a_proposal_outside_the_window_is_not_adopted(self):
        grounder = StaticGrounder({"composer": _proposal(region=Region(1800, 100, 400, 60))})
        decision = resolve_target(_observe(input_candidate_count=2), window=WINDOW,
                                  grounder=grounder)
        assert decision["locate_source"] is None
        assert decision["reason"] == "NO_TARGET"

    def test_a_grounder_that_raises_is_no_opinion_not_a_crash(self):
        class Broken:
            name = "broken"

            def ground(self, request):
                raise RuntimeError("service is down")

        decision = resolve_target(_observe(input_candidate_count=2), window=WINDOW,
                                  grounder=Broken(), reference=COMPOSER)
        assert decision["locate_source"] == "geometry"
        assert decision["considered"][-2] == {"source": "vision", "admissible": False,
                                              "reason": "GROUNDER_FAILED:RuntimeError"}


class TestGeometryIsTheLastResortAndNeverLooksConfident:
    def test_a_configured_region_is_used_when_both_legs_fail(self):
        decision = resolve_target(_observe(input_candidate_count=2), window=WINDOW,
                                  grounder=NullGrounder(), reference=COMPOSER)
        assert decision["locate_source"] == "geometry"
        assert decision["needs_verification"] is True
        assert "layout moved" in decision["note"]


class TestNothingLocatedMeansDefer:
    def test_no_leg_works(self):
        decision = resolve_target(_observe(input_candidate_count=2), window=WINDOW,
                                  grounder=NullGrounder())
        assert decision["locate_source"] is None
        assert decision["reason"] == "NO_TARGET"

    def test_an_undrivable_executor_is_decided_before_anything_else(self):
        # UIPI refuses the input, not the sight of the box, so asking a model
        # where the box is would answer a question whose answer cannot be used.
        grounder = StaticGrounder({"composer": _proposal()})
        decision = resolve_target(_observe(drivable=False, blocked_by="UIPI"), window=WINDOW,
                                  grounder=grounder, reference=COMPOSER)
        assert decision["reason"] == "EXECUTOR_TOO_LOW"
        assert decision["blocked_by"] == "UIPI"
        assert grounder.calls == 0

    def test_an_ambiguous_count_is_reported_as_a_count(self):
        assert candidate_count({"input_candidate_count": 3}) == 3
        assert candidate_count({"input_found": True}) == 1
        assert candidate_count({"input_found": False}) == 0
        assert candidate_count({"input_candidate_count": True}) == 1
        assert candidate_count({"input_candidate_count": -4}) == 0


class TestTheSendVerdictDefaultsToDefer:
    def _locate(self, **overrides):
        decision = resolve_target(_observe(**overrides), window=WINDOW)
        return decision

    def test_a_decisive_uia_hit_with_a_clean_run_sends(self):
        verdict = delivery_verdict(
            locate=self._locate(),
            policy_result={"stopped_because": "policy-finished",
                           "steps": [{"operation": "type", "verified": True}]})
        assert verdict["action"] == "send"
        assert verdict["state"] == "READY"

    def test_a_vision_hit_must_be_verified_before_sending(self):
        locate = resolve_target(_observe(input_candidate_count=2), window=WINDOW,
                                grounder=StaticGrounder({"composer": _proposal()}))
        verdict = delivery_verdict(locate=locate)
        assert verdict["action"] == "send-after-verification"
        assert verdict["state"] == "READY_UNVERIFIED"

    def test_nothing_located_defers(self):
        locate = resolve_target(_observe(input_candidate_count=2), window=WINDOW)
        verdict = delivery_verdict(locate=locate)
        assert verdict["action"] == "defer"
        assert verdict["reason"] == "NO_TARGET"

    def test_an_undrivable_executor_defers_with_its_own_reason(self):
        locate = resolve_target(_observe(drivable=False), window=WINDOW)
        verdict = delivery_verdict(locate=locate)
        assert (verdict["action"], verdict["reason"]) == ("defer", "EXECUTOR_TOO_LOW")

    def test_an_unverified_write_step_defers_even_after_a_clean_locate(self):
        verdict = delivery_verdict(
            locate=self._locate(),
            policy_result={"stopped_because": "policy-finished",
                           "steps": [{"index": 1, "operation": "type", "verified": False}]})
        assert verdict["action"] == "defer"
        assert verdict["reason"] == "STEP_NOT_VERIFIED"
        assert verdict["step"] == 1

    def test_a_write_step_with_no_verifier_counts_as_unproven(self):
        # "No verifier installed" cannot mean "assume the click landed".
        verdict = delivery_verdict(
            locate=self._locate(),
            policy_result={"stopped_because": "policy-finished",
                           "steps": [{"index": 0, "operation": "click", "verified": None}]})
        assert verdict["action"] == "defer"
        assert verdict["reason"] == "STEP_NOT_VERIFIED"

    def test_a_read_with_no_verifier_is_not_a_reason_to_defer(self):
        verdict = delivery_verdict(
            locate=self._locate(),
            policy_result={"stopped_because": "policy-finished",
                           "steps": [{"index": 0, "operation": "screenshot", "verified": None}]})
        assert verdict["action"] == "send"

    def test_a_policy_that_stopped_early_defers(self):
        verdict = delivery_verdict(
            locate=self._locate(),
            policy_result={"stopped_because": "step-budget-exhausted",
                           "steps": [{"index": 0, "operation": "type", "verified": True}]})
        assert verdict["action"] == "defer"
        assert verdict["reason"] == "step-budget-exhausted"

    def test_unproven_steps_are_named(self):
        steps = [{"operation": "type", "verified": False},
                 {"operation": "click", "verified": None},
                 {"operation": "screenshot", "verified": None},
                 {"operation": "keys", "verified": True}]
        assert [step["operation"] for step in unproven_steps(steps)] == ["type", "click"]


def _run(argv):
    args = cli.build_parser().parse_args(argv)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        args.func(args)
    return json.loads(buffer.getvalue())


def test_the_command_replays_a_decision_from_a_recorded_receipt(tmp_path):
    receipt = tmp_path / "observe.json"
    receipt.write_text(json.dumps(_observe()), encoding="utf-8")
    payload = _run(["coord-route", "--observation", str(receipt), "--window", "1920x1080"])
    assert payload["decision"]["locate_source"] == "uia"
    assert "verdict" not in payload  # no policy result, no send verdict claimed


def test_the_command_includes_the_verdict_when_a_policy_result_is_given(tmp_path):
    receipt = tmp_path / "observe.json"
    receipt.write_text(json.dumps(_observe()), encoding="utf-8")
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"stopped_because": "policy-finished",
                                  "steps": [{"index": 0, "operation": "type",
                                             "verified": True}]}), encoding="utf-8")
    payload = _run(["coord-route", "--observation", str(receipt), "--window", "1920x1080",
                    "--policy-result", str(policy)])
    assert payload["verdict"]["action"] == "send"


def test_the_command_refuses_a_receipt_that_is_not_an_object(tmp_path):
    receipt = tmp_path / "observe.json"
    receipt.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(cli.ProtocolError, match="must be a JSON object"):
        _run(["coord-route", "--observation", str(receipt), "--window", "1920x1080"])
    with pytest.raises(cli.ProtocolError, match="cannot read observation"):
        _run(["coord-route", "--observation", str(tmp_path / "missing.json"),
              "--window", "1920x1080"])


def test_a_dry_run_cannot_be_mistaken_for_a_green_light(tmp_path):
    # The dry run installs no verifier, so every write step comes back
    # unproven.  Chaining it into the router therefore yields a deferral, which
    # is the point: a validation pass is not a send authorisation.
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps([{"name": "click", "x": 500, "y": 900},
                                {"name": "type", "text": "hello"}]), encoding="utf-8")
    dry = _run(["coord-policy", "validate", "--plan", str(plan), "--window", "1920x1080"])
    assert dry["stopped_because"] == "policy-finished"
    assert [step["verified"] for step in dry["steps"]] == [None, None]

    receipt = tmp_path / "observe.json"
    receipt.write_text(json.dumps(_observe()), encoding="utf-8")
    result = tmp_path / "policy.json"
    result.write_text(json.dumps(dry), encoding="utf-8")
    payload = _run(["coord-route", "--observation", str(receipt), "--window", "1920x1080",
                    "--policy-result", str(result)])
    assert payload["decision"]["locate_source"] == "uia"
    assert payload["verdict"]["action"] == "defer"
    assert payload["verdict"]["reason"] == "STEP_NOT_VERIFIED"


def test_the_routing_order_is_declared_not_implied():
    # If someone reorders the legs, this is the line that has to change with it.
    assert input_routing.LOCATE_SOURCES == ("uia", "vision", "geometry")
    assert input_routing.NEEDS_VERIFICATION == {"uia": False, "vision": True,
                                               "geometry": True}
