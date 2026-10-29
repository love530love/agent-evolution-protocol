import json
import os
from pathlib import Path

from agent_evolution_protocol.coordination.ui_fallback import PowerShellUiaDriver

ROOT = Path(__file__).resolve().parents[1]


def test_codebuddy_profile_uses_observed_send_button_geometry():
    profile = json.loads(
        (ROOT / "coordination" / "ui_profiles" / "codebuddy_cn.json").read_text(
            encoding="utf-8"
        )
    )
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
