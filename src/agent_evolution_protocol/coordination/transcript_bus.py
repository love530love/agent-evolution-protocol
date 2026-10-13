"""Shared transcript bus: one local timeline every agent can perceive.

A legion that cannot see each other converges on guesses. This module ingests
whatever a transport managed to read out of a client window (CDP DOM, UIA tree
or OCR), cleans it, keeps only the delta, and republishes it as a workspace-wide
timeline. Any agent can then read what the others said without scraping anyone.

It never types into a client and never wakes a model. It only records and serves.
"""
from __future__ import annotations

from datetime import datetime, timezone
import argparse
import difflib
import json
import os
from pathlib import Path
import re
from uuid import uuid4

SAFE_AGENT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
SAFE_EVENT = re.compile(r"^[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}$")
CJK = r"\u4e00-\u9fff\u3400-\u4dbf"

# Windows OCR inserts a space between every CJK glyph. Undo it, but only where
# both neighbours are CJK, so English and code keep their spacing. Never swallow
# the line break itself: a CJK line ending followed by a CJK line would fuse two
# conversation turns into one unreadable row.
_SPACED_CJK = re.compile(rf"([{CJK}])[^\S\r\n]+([{CJK}])")


def clean_ocr(text: str) -> str:
    if not text:
        return ""
    previous = None
    current = text
    for _ in range(3):
        current = _SPACED_CJK.sub(r"\1\2", current)
        if current == previous:
            break
        previous = current
    return current


def _normalise(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


# A UIA tree walk hands back each conversation bubble twice: once as Name and
# once as Legacy/Value. Both copies land on one line separated by " | ". The
# sidebar, tab strip and screen-reader hints arrive as pure noise. Keeping both
# would double every digest and drown the signal, so collapse and drop them.
_UIA_NOISE = (
    "跳转到用户消息", "置顶聊天", "归档聊天", "新聊天", "朗读",
    "在文件中搜索", "查看源代码", "切换文件树", "打开个人资料菜单",
    "关闭", "标签页", "侧边栏", "调整工作空间窗格大小",
    "复制表格", "展开表格", "查看已更改的文件", "查看变更",
    "再显示", "撤销", "编辑了文件", "运行了命令", "跳转到内容",
)


def clean_uia_capture(text: str, *, max_line_chars: int = 1200) -> list[str]:
    """Collapse the duplicated UIA capture into one readable timeline."""
    rows: list[str] = []
    for raw in clean_ocr(text or "").splitlines():
        line = _normalise(raw.replace("\ufffc", " "))
        if not line:
            continue
        if len(line) > max_line_chars:
            continue
        if any(token in line for token in _UIA_NOISE):
            continue
        if " | " in line:
            head, _, tail = line.partition(" | ")
            head, tail = head.strip(), tail.strip()
            if head == tail:
                line = head
            elif tail.startswith(head) and head:
                line = head
            elif head.startswith(tail) and tail:
                line = tail
        line = _normalise(line)
        if line:
            rows.append(line)
    # Collapse immediate repeats: the same bubble is often emitted twice.
    deduped: list[str] = []
    for row in rows:
        if deduped and deduped[-1] == row:
            continue
        deduped.append(row)
    return deduped


def _lines(text: str) -> list[str]:
    rows = [_normalise(line) for line in clean_ocr(text).splitlines()]
    return [row for row in rows if row]


def bus_root(coord_root: Path) -> Path:
    return Path(coord_root) / "transcript_bus"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with tmp.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _event_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:12]


def _snapshot_path(coord_root: Path, agent: str) -> Path:
    return bus_root(coord_root) / "snapshots" / f"{agent}.json"


def publish(coord_root: Path, *, agent: str, text: str, source: str = "uia",
            session_id: str = "", window_title: str = "", role: str = "unknown",
            direction: str = "unknown", clean: bool = True) -> dict:
    """Ingest a captured transcript and store only the new lines."""
    if not SAFE_AGENT.fullmatch(agent):
        raise ValueError("invalid agent")
    if role not in ("unknown", "assistant", "user", "system"):
        raise ValueError("invalid role")
    if direction not in ("unknown", "incoming", "outgoing"):
        raise ValueError("invalid direction")
    observed = clean_uia_capture(text) if clean else _lines(text)
    snap_path = _snapshot_path(coord_root, agent)
    previous: list[str] = []
    if snap_path.is_file():
        try:
            previous = list(json.loads(snap_path.read_text(encoding="utf-8")).get("lines", []))
        except (OSError, json.JSONDecodeError):
            previous = []

    if not previous:
        added = observed
        changed = bool(observed)
    else:
        matcher = difflib.SequenceMatcher(a=previous, b=observed, autojunk=False)
        added = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag in ("replace", "insert"):
                added.extend(observed[j1:j2])
        changed = bool(added) or len(observed) != len(previous)

    event = None
    if changed:
        event_id = _event_id()
        event = {
            "id": event_id,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "agent": agent, "source": source, "session_id": session_id,
            "role": role, "direction": direction,
            "window_title": window_title,
            "previous_line_count": len(previous),
            "current_line_count": len(observed),
            "added_lines": added[-400:],
            "visibility": "workspace",
        }
        _write_json(bus_root(coord_root) / "events" / f"{event_id}.json", event)
    _write_json(snap_path, {
        "agent": agent, "source": source, "session_id": session_id,
        "window_title": window_title, "lines": observed[-2000:],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    return {
        "agent": agent, "source": source, "changed": changed,
        "added_line_count": len(added) if changed else 0,
        "observed_line_count": len(observed),
        "event": event,
        "note": "Published locally. Absence of new lines means 'nothing new was readable', not 'the agent is idle'.",
    }


def read_events(coord_root: Path, *, after: str = "", agent: str = "",
                limit: int = 100) -> list[dict]:
    if after and not SAFE_EVENT.fullmatch(after):
        raise ValueError("invalid cursor")
    if not 1 <= limit <= 500:
        raise ValueError("limit must be 1..500")
    folder = bus_root(coord_root) / "events"
    if not folder.exists():
        return []
    rows = []
    for path in sorted(folder.glob("*.json")):
        if path.stem <= after or not SAFE_EVENT.fullmatch(path.stem):
            continue
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if agent and item.get("agent") != agent:
            continue
        rows.append(item)
        if len(rows) >= limit:
            break
    return rows


def snapshot(coord_root: Path, agent: str) -> list[str]:
    if not SAFE_AGENT.fullmatch(agent):
        raise ValueError("invalid agent")
    path = _snapshot_path(coord_root, agent)
    if not path.is_file():
        return []
    try:
        return list(json.loads(path.read_text(encoding="utf-8")).get("lines", []))
    except (OSError, json.JSONDecodeError):
        return []


def awareness(root: Path, *, agents: list[str] | None = None, tail_lines: int = 3) -> dict:
    """What each client's window last showed, and who has never been captured."""
    names = [name for name in (agents or []) if SAFE_AGENT.fullmatch(name)]
    if not names:
        folder = bus_root(root) / "snapshots"
        names = sorted(path.stem for path in folder.glob("*.json")) if folder.exists() else []
    rows = []
    for name in names:
        lines = snapshot(root, name)
        path = _snapshot_path(root, name)
        updated = ""
        if path.is_file():
            try:
                updated = str(json.loads(path.read_text(encoding="utf-8")).get("updated_at", ""))
            except (OSError, json.JSONDecodeError):
                updated = ""
        rows.append({"agent": name, "line_count": len(lines), "updated_at": updated,
                     "tail": lines[-tail_lines:] if lines else []})
    return {
        "agents": rows,
        "never_captured": [row["agent"] for row in rows if row["line_count"] == 0],
        "note": "Zero lines means the transport could not read that window yet; it is a transport gap, not proof of idleness.",
    }


def digest(root: Path, *, since_events: int = 40, per_agent: int = 6) -> dict:
    events = read_events(root, limit=500)[-since_events:]
    grouped: dict[str, list[str]] = {}
    for item in events:
        grouped.setdefault(str(item.get("agent", "?")), [])
        for line in item.get("added_lines", []):
            grouped[str(item.get("agent", "?"))].append(line)
    actors = [{"agent": name, "latest": lines[-per_agent:]} for name, lines in sorted(grouped.items())]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "event_count": len(events), "actors": actors,
        "note": "Bus content is whatever transports could read. A silent actor here is a coverage gap to fix.",
    }


def _cmd_publish(args: argparse.Namespace) -> int:
    source = "uia"
    text = ""
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8", errors="replace")
        source = args.source
    else:
        text = args.text or ""
    result = publish(Path(args.root), agent=args.agent, text=text, source=source,
                     session_id=args.session_id, window_title=args.window_title,
                     role=args.role, direction=args.direction,
                     clean=not args.no_clean)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_digest(args: argparse.Namespace) -> int:
    result = digest(Path(args.root), since_events=args.since_events, per_agent=args.per_agent)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_awareness(args: argparse.Namespace) -> int:
    names = [name for name in (args.agent or "").split(",") if name.strip()]
    result = awareness(Path(args.root), agents=names or None, tail_lines=args.tail_lines)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_tail(args: argparse.Namespace) -> int:
    lines = snapshot(Path(args.root), args.agent)
    print("\n".join(lines[-args.lines:]))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).resolve().parents[2] / "coordination",
                        help="coordination root")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("publish", help="ingest a captured transcript")
    p.add_argument("--agent", required=True)
    p.add_argument("--file", help="capture file (uia/cdp/ocr export)")
    p.add_argument("--text", help="inline text instead of --file")
    p.add_argument("--source", default="uia", choices=["uia", "cdp", "ocr", "manual", "native"])
    p.add_argument("--session-id", default="")
    p.add_argument("--window-title", default="")
    p.add_argument("--role", default="unknown", choices=["unknown", "assistant", "user", "system"])
    p.add_argument("--direction", default="unknown", choices=["unknown", "incoming", "outgoing"])
    p.add_argument("--no-clean", action="store_true", help="keep raw duplicated UIA lines")
    p.set_defaults(func=_cmd_publish)

    d = sub.add_parser("digest", help="what everyone said, newest last")
    d.add_argument("--since-events", type=int, default=40)
    d.add_argument("--per-agent", type=int, default=6)
    d.set_defaults(func=_cmd_digest)

    a = sub.add_parser("awareness", help="who has been captured and who is still blind")
    a.add_argument("--agent", default="", help="comma separated, default all known")
    a.add_argument("--tail-lines", type=int, default=3)
    a.set_defaults(func=_cmd_awareness)

    t = sub.add_parser("tail", help="last N lines of one agent's transcript")
    t.add_argument("--agent", required=True)
    t.add_argument("--lines", type=int, default=40)
    t.set_defaults(func=_cmd_tail)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
