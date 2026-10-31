import contextlib
import io
import json
from pathlib import Path

import pytest

from agent_evolution_protocol import cli
from agent_evolution_protocol.coordination import desktop_providers
from agent_evolution_protocol.coordination import policy_executor
from agent_evolution_protocol.coordination.policy_executor import (
    PolicyExecutor,
    PolicyRefused,
    ScriptedPolicy,
    UnsupportedAction,
    normalise,
)
from agent_evolution_protocol.coordination.vision_grounding import Region


WINDOW = Region(x=0, y=0, width=1920, height=1080)


class TestTheActionVocabularyIsAepsNotTheModels:
    """A model primitive with no operation behind it is refused, never degraded."""

    def test_a_double_click_is_refused_instead_of_becoming_a_single_click(self):
        # Degrading a double click into a single click is a wrong action that
        # looks like a successful one, which is worse than a refusal.
        with pytest.raises(UnsupportedAction) as caught:
            normalise({"name": "double_click", "x": 100, "y": 100}, window=WINDOW)
        assert caught.value.action == "double_click"
        assert "single click" in caught.value.reason

    def test_every_primitive_without_a_binding_says_why(self):
        for name in policy_executor.UNSUPPORTED:
            with pytest.raises(UnsupportedAction) as caught:
                normalise({"name": name}, window=WINDOW)
            assert caught.value.reason, f"{name} was refused without a reason"

    def test_an_unknown_primitive_is_refused(self):
        with pytest.raises(UnsupportedAction, match="not in the AEP operation vocabulary"):
            normalise({"name": "teleport"}, window=WINDOW)

    def test_every_supported_operation_really_exists(self):
        # The two lists drifting apart would silently widen what a model may do.
        for operation in policy_executor.SUPPORTED.values():
            assert operation in desktop_providers.OPERATIONS

    def test_an_action_without_a_name_is_refused(self):
        with pytest.raises(PolicyRefused, match="needs a name"):
            normalise({"x": 1, "y": 1}, window=WINDOW)
        with pytest.raises(PolicyRefused, match="must be an object"):
            normalise("click", window=WINDOW)


class TestNoShell:
    """The training recipe's bash channel is a guest-side convenience; on a live
    host it is a privilege escalation with a friendly name."""

    @pytest.mark.parametrize("name", ["bash", "shell", "exec", "run_command", "python", "terminal"])
    def test_shell_shaped_actions_are_refused(self, name):
        with pytest.raises(PolicyRefused, match="shell actions are never executed"):
            normalise({"name": name, "command": "whoami"}, window=WINDOW)

    def test_a_shell_word_anywhere_in_the_name_is_enough(self):
        with pytest.raises(PolicyRefused, match="shell actions are never executed"):
            normalise({"name": "bash_exec_wrapper"}, window=WINDOW)


class TestCoordinateHandling:
    def test_the_grid_is_converted_to_window_pixels(self):
        instruction = normalise({"name": "click", "x": 500, "y": 500}, window=WINDOW)
        assert instruction["operation"] == "click"
        assert 900 <= instruction["arguments"]["x"] <= 1000
        assert 500 <= instruction["arguments"]["y"] <= 580

    def test_a_coordinate_past_the_grid_is_refused_rather_than_clamped(self):
        with pytest.raises(PolicyRefused, match=r"within \[0, 999\]"):
            normalise({"name": "click", "x": 1200, "y": 10}, window=WINDOW)

    def test_a_non_numeric_coordinate_is_refused(self):
        with pytest.raises(PolicyRefused, match="numeric x and y"):
            normalise({"name": "click", "x": "middle", "y": 10}, window=WINDOW)

    def test_a_click_that_lands_outside_the_window_is_refused(self):
        # The grid maps inside the window by construction, so this guards the
        # conversion itself: a rounding slip must not become a stray click.
        with pytest.raises(PolicyRefused, match="outside the window"):
            normalise({"name": "click", "x": 999, "y": 999},
                      window=Region(x=0, y=0, width=10, height=10))


class TestArgumentShapes:
    def test_typing_needs_text(self):
        with pytest.raises(PolicyRefused, match="non-empty text"):
            normalise({"name": "type", "text": "   "}, window=WINDOW)
        assert normalise({"name": "type", "text": "hello"}, window=WINDOW)["arguments"] == {"text": "hello"}

    def test_a_keypress_accepts_either_spelling(self):
        assert normalise({"name": "keypress", "keys": "enter"}, window=WINDOW)["arguments"] == {"keys": "enter"}
        assert normalise({"name": "keypress", "key": "enter"}, window=WINDOW)["arguments"] == {"keys": "enter"}

    def test_a_wait_outside_the_sane_range_is_refused(self):
        with pytest.raises(PolicyRefused, match="between 0 and 30"):
            normalise({"name": "wait", "seconds": 120}, window=WINDOW)
        assert normalise({"name": "wait"}, window=WINDOW)["arguments"] == {"seconds": 0.5}

    def test_terminate_is_a_control_signal_not_an_operation(self):
        assert normalise({"name": "terminate"}, window=WINDOW) == {
            "control": "policy-terminated", "action": "terminate"}


class Recorder:
    def __init__(self, *, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def __call__(self, operation, arguments):
        self.calls.append((operation, arguments))
        if operation == self.fail_on:
            raise OSError("the driver went away")
        return {"ok": True}


class TestTheExecutorNeverDecidesAStepWorked:
    def test_an_unverified_step_stops_the_run(self):
        policy = ScriptedPolicy([{"name": "click", "x": 500, "y": 900},
                                 {"name": "type", "text": "hello"}])
        executor = PolicyExecutor(Recorder(), window=WINDOW, verify=lambda step: False)
        result = executor.run(policy)
        assert result["state"] == "STOPPED"
        assert result["stopped_because"] == "step-not-verified"
        assert len(result["steps"]) == 1
        assert policy.seen == 1  # the second action was never even proposed

    def test_a_verified_run_completes(self):
        policy = ScriptedPolicy([{"name": "click", "x": 500, "y": 900},
                                 {"name": "type", "text": "hello"}])
        executor = PolicyExecutor(Recorder(), window=WINDOW, verify=lambda step: True)
        result = executor.run(policy)
        assert result["state"] == "COMPLETED"
        assert result["stopped_because"] == "policy-finished"
        assert [step["verified"] for step in result["steps"]] == [True, True]

    def test_without_a_verifier_a_step_is_unproven_not_passed(self):
        executor = PolicyExecutor(Recorder(), window=WINDOW)
        result = executor.run(ScriptedPolicy([{"name": "click", "x": 500, "y": 900}]))
        assert result["steps"][0]["verified"] is None
        assert result["note"].startswith("the executor never decides")

    def test_a_driver_failure_ends_the_run_with_that_reason(self):
        executor = PolicyExecutor(Recorder(fail_on="click"), window=WINDOW,
                                  verify=lambda step: True)
        result = executor.run(ScriptedPolicy([{"name": "click", "x": 500, "y": 900},
                                              {"name": "type", "text": "x"}]))
        assert result["stopped_because"] == "DRIVER_FAILED"
        assert result["steps"][0]["verified"] is False
        assert "OSError" in result["steps"][0]["note"]

    def test_the_step_budget_is_a_ceiling_not_a_target(self):
        plan = [{"name": "wait", "seconds": 0.1}] * 5
        executor = PolicyExecutor(Recorder(), window=WINDOW, verify=lambda step: True,
                                  max_steps=3)
        result = executor.run(ScriptedPolicy(plan))
        assert len(result["steps"]) == 3
        assert result["stopped_because"] == "step-budget-exhausted"

    def test_an_operation_outside_the_allowlist_is_refused(self):
        executor = PolicyExecutor(Recorder(), window=WINDOW, verify=lambda step: True,
                                  allowed={"type"})
        result = executor.run(ScriptedPolicy([{"name": "click", "x": 500, "y": 900}]))
        assert result["stopped_because"] == "OPERATION_NOT_ALLOWED:click"
        assert result["steps"][0]["note"] == "not in the executor allowlist"

    def test_a_shell_action_stops_the_run_before_anything_executes(self):
        recorder = Recorder()
        executor = PolicyExecutor(recorder, window=WINDOW, verify=lambda step: True)
        result = executor.run(ScriptedPolicy([{"name": "bash", "command": "whoami"}]))
        assert result["stopped_because"].startswith("REFUSED:")
        assert recorder.calls == []
        assert result["state"] == "STOPPED"

    def test_an_unsupported_action_is_named_in_the_stop_reason(self):
        executor = PolicyExecutor(Recorder(), window=WINDOW, verify=lambda step: True)
        result = executor.run(ScriptedPolicy([{"name": "scroll", "direction": "down"}]))
        assert result["stopped_because"] == "UNSUPPORTED_ACTION:scroll"

    def test_terminate_ends_the_run_without_a_refusal(self):
        recorder = Recorder()
        executor = PolicyExecutor(recorder, window=WINDOW, verify=lambda step: True)
        result = executor.run(ScriptedPolicy([{"name": "terminate"}]))
        assert result["stopped_because"] == "policy-terminated"
        assert recorder.calls == []
        assert result["steps"] == []

    def test_max_steps_must_be_positive(self):
        with pytest.raises(ValueError, match="positive"):
            PolicyExecutor(Recorder(), window=WINDOW, max_steps=0)


def _run(argv):
    args = cli.build_parser().parse_args(argv)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        args.func(args)
    return json.loads(buffer.getvalue())


def test_the_dry_run_executes_nothing_and_says_so(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps([{"name": "click", "x": 500, "y": 900},
                                {"name": "type", "text": "hello"},
                                {"name": "keypress", "keys": "enter"}]), encoding="utf-8")
    payload = _run(["coord-policy", "validate", "--plan", str(plan), "--window", "1920x1080"])
    assert payload["dry_run"] is True
    assert payload["stopped_because"] == "policy-finished"
    assert [step["operation"] for step in payload["steps"]] == ["click", "type", "keys"]
    assert all(step["result"] == {"dry_run": True, "operation": step["operation"]}
               for step in payload["steps"])
    assert payload["note"] == "no desktop action was executed; this only exercises the guards"


def test_the_dry_run_catches_a_shell_action_before_a_live_session_does(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"actions": [{"name": "click", "x": 10, "y": 10},
                                            {"name": "bash", "command": "rm -rf /"}]}),
                    encoding="utf-8")
    payload = _run(["coord-policy", "validate", "--plan", str(plan), "--window", "1920x1080"])
    assert payload["stopped_because"].startswith("REFUSED:")
    # The refusal is recorded as a step with no operation, so the trace shows
    # what was refused rather than just ending early.
    assert [step["operation"] for step in payload["steps"]] == ["click", ""]
    assert "shell actions are never executed" in payload["steps"][-1]["note"]


def test_an_unreadable_plan_is_a_protocol_error(tmp_path):
    bad = tmp_path / "plan.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(cli.ProtocolError, match="cannot read plan"):
        _run(["coord-policy", "validate", "--plan", str(bad), "--window", "1920x1080"])


def test_a_plan_that_is_not_a_list_is_a_protocol_error(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"steps": [{"name": "click"}]}), encoding="utf-8")
    # An unrecognised key must not read as "this plan has nothing to do":
    # silently running zero steps looks exactly like a clean validation.
    with pytest.raises(cli.ProtocolError, match="'actions' or 'plan'"):
        _run(["coord-policy", "validate", "--plan", str(plan), "--window", "1920x1080"])
    plan.write_text(json.dumps({"actions": "click"}), encoding="utf-8")
    with pytest.raises(cli.ProtocolError, match="must be a list"):
        _run(["coord-policy", "validate", "--plan", str(plan), "--window", "1920x1080"])
