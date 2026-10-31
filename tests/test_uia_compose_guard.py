import json
import os
from pathlib import Path

import pytest

from agent_evolution_protocol.coordination.ui_fallback import PowerShellUiaDriver

ROOT = Path(__file__).resolve().parents[1]


def _live_profile(name):
    """Return a machine-local UI profile, or skip.

    ``coordination/`` is a live workspace directory and is not in the
    repository.  These assertions record how one machine was calibrated; on a
    clean checkout there is no calibration to check, and failing the job for
    that would say nothing about the code.
    """
    path = ROOT / "coordination" / "ui_profiles" / f"{name}.json"
    if not path.is_file():
        pytest.skip(f"{path.name} is a live workspace profile, absent on a clean checkout")
    return json.loads(path.read_text(encoding="utf-8"))


def test_codebuddy_profile_uses_observed_send_button_geometry():
    profile = _live_profile("codebuddy_cn")
    assert profile["send_button_x_ratio"] == 0.59
    assert profile["send_button_y_ratio"] == 0.945
    assert profile["send_button_color"] == "purple"
    assert profile["scroll_composer"] is True
    assert profile["maximize_window"] is True


def test_profile_visual_recovery_flags_are_opt_in_and_isolated():
    profile = {"scroll_composer": True, "maximize_window": True}
    before_scroll = os.environ.get("AEP_SCROLL_COMPOSER")
    before_maximize = os.environ.get("AEP_MAXIMIZE_WINDOW")
    env = PowerShellUiaDriver._ui_env(profile)
    assert env["AEP_SCROLL_COMPOSER"] == "1"
    assert env["AEP_MAXIMIZE_WINDOW"] == "1"
    env["AEP_SEND_BUTTON_COLOR"] = "1"
    # the profile flag is applied by the real loader path
    assert PowerShellUiaDriver._ui_env({"send_button_color": "purple"})["AEP_SEND_BUTTON_COLOR"] == "1"
    assert os.environ.get("AEP_SCROLL_COMPOSER") == before_scroll
    assert os.environ.get("AEP_MAXIMIZE_WINDOW") == before_maximize


def test_ocr_compose_does_not_promote_paste_to_delivery_without_clearance():
    source = (
        ROOT
        / "src"
        / "agent_evolution_protocol"
        / "coordination"
        / "wake_adapters"
        / "uia_session.ps1"
    ).read_text(encoding="utf-8")
    assert "$composerCleared = $false" in source
    assert "$eventInComposer = $false" in source
    assert "Find-AepPurpleControl" in source
    assert "send_button_method = $sendButtonMethod" in source
    assert "$delivered = [bool]($visible -and $composerCleared -and -not $eventInComposer)" in source
    assert "composer_cleared  = $composerCleared" in source
    assert "event_in_composer = $eventInComposer" in source


def test_qoder_profile_uses_sticky_composer_and_activity_gate():
    profile = _live_profile("qoder")
    assert profile["strict_composer_hint"] is True
    assert "长按鼠标语音输入文字" in profile["composer_hints"]
    assert profile["require_activity_ready"] is True
    assert profile["activity_ocr"] is True
    assert profile["send_button_color"] == "green"


def _capture_deliver_args(profile, monkeypatch):
    """Run deliver() without PowerShell and return the argv it would pass."""
    driver = PowerShellUiaDriver.__new__(PowerShellUiaDriver)
    driver.pwsh = "pwsh"
    seen = {}

    def fake_run(script, args, timeout=45, env=None):
        seen["args"] = list(args)
        return {}

    monkeypatch.setattr(driver, "_run", fake_run)
    driver.deliver(profile, "ping AEP-TEST-1", "AEP-TEST-1")
    return seen["args"]


BASE_PROFILE = {"agent": "probe", "process": "probe", "title": "probe",
                "exe": "probe.exe", "transport": "uia"}


def test_clipboard_paste_is_opt_in_and_reaches_the_adapter(monkeypatch):
    # The adapter refuses to paste into a client it cannot read unless this is
    # passed, and Python never passed it: an opaque client reached
    # NO_INPUT_CONTROL and had no way out.  It stays opt-in because the paste
    # lands wherever activation put focus.
    assert "-AllowClipboardPaste" not in _capture_deliver_args(dict(BASE_PROFILE), monkeypatch)
    allowed = _capture_deliver_args({**BASE_PROFILE, "allow_clipboard_paste": True}, monkeypatch)
    assert "-AllowClipboardPaste" in allowed


def test_the_adapter_still_refuses_blind_paste_by_default():
    source = (ROOT / "src" / "agent_evolution_protocol" / "coordination"
              / "wake_adapters" / "uia_session.ps1").read_text(encoding="utf-8")
    assert "if (-not $selection -and -not $AllowClipboardPaste) {" in source
    assert "NO_INPUT_CONTROL: no enabled editable control found" in source


def test_every_deliver_early_return_says_whether_an_input_was_found():
    # The evidence ladder is only machine-checkable if the failing branches
    # answer the same question as the succeeding one.  These three used to
    # report a status and nothing about input_found.  Scoped to the deliver
    # section: the compose path has its own receipts with their own rules.
    source = (ROOT / "src" / "agent_evolution_protocol" / "coordination"
              / "wake_adapters" / "uia_session.ps1").read_text(encoding="utf-8")
    deliver_start = source.index("if (-not $selection -and -not $AllowClipboardPaste) {")
    deliver = source[deliver_start:]
    for status in ("urgent-interject-unavailable", "input-not-confirmed",
                   "send-control-not-found"):
        start = deliver.index(f"'{status}'")
        assert "input_found" in deliver[start:start + 700], f"{status} does not report input_found"
