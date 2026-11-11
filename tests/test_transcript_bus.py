from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from agent_evolution_protocol.coordination import transcript_bus


def test_clean_uia_capture_collapses_duplicated_bubbles():
    raw = "你说：\r\n所以要升级协作机制 | 所以要升级协作机制\r\n21:58 | 21:58\r\n"
    rows = transcript_bus.clean_uia_capture(raw)
    assert rows == ["你说：", "所以要升级协作机制", "21:58"]


def test_clean_uia_capture_drops_screen_reader_noise():
    raw = "跳转到用户消息 12\r\n置顶聊天 归档聊天\r\n朗读\r\n真正的一句话\r\n"
    rows = transcript_bus.clean_uia_capture(raw)
    assert rows == ["真正的一句话"]


def test_clean_uia_capture_removes_adjacent_repeats():
    raw = "同一行\r\n同一行\r\n下一行\r\n"
    assert transcript_bus.clean_uia_capture(raw) == ["同一行", "下一行"]


def test_publish_stores_only_the_delta(tmp_path: Path):
    root = tmp_path / "coordination"
    first = transcript_bus.publish(root, agent="alpha", text="line one\nline two")
    assert first["changed"] is True
    assert first["added_line_count"] == 2

    second = transcript_bus.publish(root, agent="alpha", text="line one\nline two\nline three")
    assert second["changed"] is True
    assert second["added_line_count"] == 1

    third = transcript_bus.publish(root, agent="alpha", text="line one\nline two\nline three")
    assert third["changed"] is False
    assert third["added_line_count"] == 0


def test_published_events_are_readable_by_everyone(tmp_path: Path):
    root = tmp_path / "coordination"
    transcript_bus.publish(root, agent="alpha", text="alpha said something")
    transcript_bus.publish(root, agent="beta", text="beta answered")
    events = transcript_bus.read_events(root, limit=10)
    assert {item["agent"] for item in events} == {"alpha", "beta"}
    assert all(item["visibility"] == "workspace" for item in events)


def test_awareness_marks_never_captured(tmp_path: Path):
    root = tmp_path / "coordination"
    transcript_bus.publish(root, agent="alpha", text="hello")
    report = transcript_bus.awareness(root, agents=["alpha", "ghost"])
    assert report["never_captured"] == ["ghost"]


def test_invalid_agent_name_rejected(tmp_path: Path):
    try:
        transcript_bus.publish(tmp_path, agent="../escape", text="x")
    except ValueError:
        return
    raise AssertionError("invalid agent name must be rejected")


def test_snapshot_roundtrip(tmp_path: Path):
    root = tmp_path / "coordination"
    transcript_bus.publish(root, agent="alpha", text="kept line")
    assert transcript_bus.snapshot(root, "alpha") == ["kept line"]
    assert transcript_bus.snapshot(root, "nobody") == []


def test_patrol_state_reports_blind_clients(tmp_path: Path):
    from agent_evolution_protocol.coordination import transcript_patrol

    root = tmp_path / "coordination"
    cfg = transcript_patrol.config_path(root)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"clients": [{"agent": "alpha", "process": "NoSuchProcess",
                                            "title": "NoSuchWindow"}]}),
                   encoding="utf-8")
    result = transcript_patrol.run(root)
    assert result["blind"] == ["alpha"]
    assert result["clients"][0]["captured"] is False


def test_patrol_is_background_only_and_never_uses_the_foreground_capture_mode():
    from agent_evolution_protocol.coordination import transcript_patrol

    source = Path(transcript_patrol.__file__).read_text(encoding="utf-8")
    assert '"-Mode", "observe"' in source
    assert '"-Mode", "capture"' not in source
    assert "never types, sends, launches, focuses" in source


def test_patrol_init_keeps_updater_identity_and_observation_policy(tmp_path: Path):
    from agent_evolution_protocol.coordination import transcript_patrol

    root = tmp_path / "coordination"
    profiles = root / "ui_profiles"
    profiles.mkdir(parents=True)
    profile = {
        "agent": "qoder",
        "process": "Qoder",
        "title": "Qoder",
        "transport": "uia",
        "exe": r"D:\\Programs\\Qoder\\Qoder.exe",
        "allowed_exe_roots": [r"D:\\Programs\\Qoder\\.qoder-versions"],
        "allow_unknown_path": True,
        "activity_ocr": True,
        "ocr_language": "zh-Hans-CN",
        "session_marker": "赛事夺冠执行",
        "send_mode": "button",
    }
    (profiles / "qoder.json").write_text(json.dumps(profile, ensure_ascii=False),
                                         encoding="utf-8")

    client = transcript_patrol.default_config(root)["clients"][0]
    assert client == {
        "agent": "qoder",
        "process": "Qoder",
        "title": "Qoder",
        "transport": "uia",
        "exe": r"D:\\Programs\\Qoder\\Qoder.exe",
        "allowed_exe_roots": [r"D:\\Programs\\Qoder\\.qoder-versions"],
        "allow_unknown_path": True,
        "activity_ocr": True,
        "ocr_language": "zh-Hans-CN",
        "session_marker": "赛事夺冠执行",
    }


def test_patrol_capture_forwards_updater_roots_through_observe_adapter(
        tmp_path: Path, monkeypatch):
    from agent_evolution_protocol.coordination import transcript_patrol

    out = tmp_path / "transcript.txt"
    received: dict[str, object] = {}

    def fake_run(argv, *, timeout, **_kwargs):
        received["argv"] = argv
        received["timeout"] = timeout
        out.write_text("observed conversation", encoding="utf-8")
        return SimpleNamespace(stdout='{"status":"observed"}\n', stderr="", returncode=0)

    monkeypatch.setattr(transcript_patrol.shutil, "which", lambda value: "pwsh.exe")
    monkeypatch.setattr(transcript_patrol.uia_argv, "run", fake_run)
    client = {
        "agent": "qoder",
        "process": "Qoder",
        "title": "Qoder",
        "exe": r"D:\\Programs\\Qoder\\Qoder.exe",
        "allowed_exe_roots": [r"D:\\Programs\\Qoder\\.qoder-versions"],
        "allow_unknown_path": True,
        "activity_ocr": True,
        "ocr_language": "zh-Hans-CN",
        "session_marker": "赛事夺冠执行",
    }

    result = transcript_patrol.capture(client, out)
    argv = received["argv"]
    assert result["ok"] is True
    assert argv[argv.index("-Mode") + 1] == "observe"
    assert argv[argv.index("-ExpectedPath") + 1] == client["exe"]
    assert argv[argv.index("-AllowedExeRoots") + 1] == client["allowed_exe_roots"][0]
    assert "-AllowUnknownPath" in argv
    assert "-OcrVerify" in argv
    assert argv[argv.index("-OcrLanguage") + 1] == "zh-Hans-CN"
    assert argv[argv.index("-SessionMarker") + 1] == "赛事夺冠执行"


def test_patrol_config_rejects_non_list_updater_roots(tmp_path: Path):
    from agent_evolution_protocol.coordination import transcript_patrol

    root = tmp_path / "coordination"
    cfg = transcript_patrol.config_path(root)
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"clients": [{"agent": "qoder", "process": "Qoder",
                                             "allowed_exe_roots": r"D:\\Programs\\Qoder"}]}),
                   encoding="utf-8")
    try:
        transcript_patrol.load_config(root)
    except ValueError as exc:
        assert "allowed_exe_roots" in str(exc)
        return
    raise AssertionError("non-list updater roots must be rejected")


def test_patrol_config_rejects_a_whole_volume_as_an_updater_boundary(tmp_path: Path):
    from agent_evolution_protocol.coordination import transcript_patrol

    root = tmp_path / "coordination"
    cfg = transcript_patrol.config_path(root)
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"clients": [{"agent": "qoder", "process": "Qoder",
                                             "allowed_exe_roots": ["D:\\"]}]}),
                   encoding="utf-8")
    try:
        transcript_patrol.load_config(root)
    except ValueError as exc:
        assert "filesystem root" in str(exc)
        return
    raise AssertionError("a patrol must never trust a whole executable volume")
