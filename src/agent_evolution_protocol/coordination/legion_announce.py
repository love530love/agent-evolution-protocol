"""One-shot, legion-wide announcement with a fixed room and an honest receipt.

This module exists because three concrete traps were measured in this workspace
on 2026-11-05 while announcing a newly joined agent:

1. ``coord-chat-publish`` defaults to ``room=workspace``.  Only an explicit
   ``--room agent-legion`` lands in the shared all-agent timeline (the room
   already used by ``team_dispatch`` and ``presence``).
2. ``coord-chat-read --limit N`` walks FORWARD from the agent's cursor, so it
   returns the OLDEST unread events.  A just-published announcement looks as
   though it was never written.
3. ``command_memory.announce()`` hardcodes both its text and ``bridge.AGENTS``
   (seven names, no new agents), so it cannot carry a custom notice.

The capability fixes the room, states the read trap in the receipt, and never
claims delivery.  It writes **one** journal event at most.  It deliberately
does NOT write mailbox messages: ``coord-send`` already owns that format, and
copying it here would create a second standard.  Instead the plan lists the
existing ``coord-send --mailbox-only`` commands an operator may run.

No model, no socket, no process, no loop, no desktop input, no system task.
Read-only by default; ``--apply`` is the only write path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from . import conversation_journal, presence

LEGION_ROOM = "agent-legion"
SCHEMA = "aep-legion-announce-v1"
NOTE = ("One-shot announcement into the shared agent-legion timeline. "
        "DEPOSITED is not delivery; a wake or an ACK needs its own evidence.")
READ_GUIDANCE = (
    "coord-chat-read --limit N 按游标正序翻页（取 after 之后最早 N 条），不是取最新 N 条；"
    "刚发的通报通常不在前几条。验证请用 "
    "conversation_journal.read_events(root, after='<当日零时事件 id>', limit=500) "
    "或直接读 private_conversations/events/<event_id>.json。"
)
ANNOUNCE_TRAP = (
    "command_memory.announce() 的正文与 bridge.AGENTS 名单都是硬编码（7 个、不含新 Agent），"
    "不能发自定义通报；bridge.agent() 白名单还会拒新名字。"
    "本模块用 conversation_journal.append_event，不复制消息格式。"
)
MAILBOX_NOTE = ("COORD_ROOT / SOURCE / NOTICE_FILE 是大写占位符，执行前替换。"
                "发起者自己已自动排除（coord-send 会拒 sender==receiver）。"
                "--mailbox-only 保证不触发桌面打字；信箱落盘仍不等于已读或已 ACK。")


def _validate(text: str, source: str) -> None:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("ANNOUNCEMENT_TEXT_EMPTY")
    if not isinstance(source, str) or not presence.NAME.fullmatch(source):
        raise ValueError("INVALID_ANNOUNCER_NAME")


def plan(root, *, text: str, source: str, mailbox_targets=None) -> dict:
    """Read-only preview: what would be written and where."""
    _validate(text, source)
    root = Path(root)
    if mailbox_targets is None:
        folder = root / "messages"
        targets = sorted(p.name for p in folder.glob("*")
                         if p.is_dir() and p.name != source
                         and presence.NAME.fullmatch(p.name)) \
            if folder.is_dir() else []
    else:
        targets = sorted({str(name) for name in mailbox_targets} - {source})
    commands = [
        "aep --coord-root COORD_ROOT coord-send --from SOURCE --to " + target +
        " --topic legion-announcement --kind context --mailbox-only --text-file NOTICE_FILE"
        for target in targets
    ]
    return {"schema": SCHEMA, "room": LEGION_ROOM, "source": source,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "journal_path": "private_conversations/events/<event_id>.json",
            "mailbox_targets": targets, "mailbox_commands": commands,
            "mailbox_command_note": MAILBOX_NOTE, "note": NOTE}


def announce(root, *, text: str, source: str, apply: bool = False,
             mailbox_targets=None) -> dict:
    """Publish one announcement, or report exactly what would be published."""
    _validate(text, source)
    root = Path(root)
    preview = plan(root, text=text, source=source, mailbox_targets=mailbox_targets)
    report = {"schema": SCHEMA, "root": str(root), "room": LEGION_ROOM,
              "source": source, "applied": False, "plan": preview,
              "event_id": "", "writes_performed": 0,
              "verdict": "NOT_DEPOSITED", "delivered": False, "acknowledged": False,
              "mailbox_sync": "not_performed", "read_guidance": READ_GUIDANCE,
              "announce_trap": ANNOUNCE_TRAP,
              "models_loaded": 0, "processes_started": 0, "ui_touched": False,
              "deployment_performed": False, "system_tasks_registered": False}
    if not apply:
        return report
    event = conversation_journal.append_event(
        root, room=LEGION_ROOM, actor=source, source=source,
        kind="message", text=text)
    report.update(applied=True, event_id=event["id"], event=event,
                  writes_performed=1, verdict="DEPOSITED")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="One-shot legion-wide announcement")
    parser.add_argument("--coord-root", type=Path, default=Path.cwd() / "coordination")
    parser.add_argument("--source", default="workbuddyai")
    body = parser.add_mutually_exclusive_group(required=True)
    body.add_argument("--text")
    body.add_argument("--text-file", type=Path)
    parser.add_argument("--apply", action="store_true",
                        help="actually append one event (default: read-only preview)")
    args = parser.parse_args(argv)
    text = args.text if args.text is not None else args.text_file.read_text(encoding="utf-8")
    report = announce(args.coord_root, text=text, source=args.source, apply=args.apply)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
