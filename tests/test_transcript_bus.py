from __future__ import annotations

import json
from pathlib import Path

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
