"""The CLI is the interface; the manual is generated from it, never typed by hand.

Borrowed from CLI-Anything's "skills are interfaces" rule, and aimed squarely at
a failure this repository has already had: a research doc told readers that
``coord-route`` emits ``schema=aep-next-action-v1`` and ``kind=observe``, while
the code emitted ``agent-evolution-next-action-v1`` with ``kind`` restricted to
``command|needs_input|file_only``.  Nobody noticed for two days, because prose
cannot fail.  The fix then was one hand-written test for one document; this
module makes the whole surface generated instead, so the next drift is a test
failure rather than a stale paragraph.

Three rules are load-bearing:

1. **The parser is the single source of truth.**  Nothing here restates a flag,
   a default or a choice by hand.  If ``cli.py`` grows an argument, the manifest
   grows it too on the next ``--write``, and the guard test fails until the
   committed copy is regenerated.
2. **Risk is declared, and unknown means mutating.**  A command that writes and
   is labelled ``read-only`` is the one mistake that matters here -- a caller
   that trusts the label will run it under a dry-run flag that does nothing.
   So the default is the safe direction, and the guard test only enforces that
   direction (never the flattering one).
3. **A digest covers the whole manifest.**  Two manifests can be compared
   without diffing 87 command rows, and a receipt can record which surface it
   was produced against.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any

SCHEMA = "aep-skill-manifest-v1"
MANIFEST_NAME = "skill-manifest.json"
SKILL_NAME = "SKILL.md"

READ_ONLY = "read-only"
MUTATING = "mutating"
IRREVERSIBLE = "irreversible"
RISK_ORDER = (READ_ONLY, MUTATING, IRREVERSIBLE)

#: Commands that only read, decide or report.  Membership is a *declaration*,
#: not something derived from the parser -- argparse has no notion of intent.
#: Deliberately short: a command wrongly listed here is the failure mode that
#: costs something, and a command wrongly left out only costs a label.
READ_ONLY_COMMANDS = frozenset({
    "coord-status", "coord-digest", "coord-delivery-status", "coord-delivery-audit",
    "coord-dependencies", "coord-dependency-audit", "coord-dependency-inventory",
    "coord-provider-catalog", "coord-process-probe", "coord-capability-plan",
    "coord-route", "coord-policy", "coord-vision", "coord-wheel-compare",
    "coord-mechanism-audit", "inspect", "kernel-status", "kernel-continuation-plan",
    "status", "doctor", "reveal",
})

#: Commands whose effect leaves this machine or destroys local state, so no
#: local command can take it back.  Sending is the canonical case: the other
#: agent already read it.
IRREVERSIBLE_COMMANDS = frozenset({
    "coord-send", "coord-wake", "coord-wake-dispatch", "coord-wake-cancel",
    "coord-commander", "coord-assign", "coord-chat-publish", "coord-transcript",
    "coord-battle-order", "coord-cdp", "coord-clawchat", "coord-uia",
    "coord-archive-stale", "federate", "commit",
})

#: The direction the guard test polices.  Every name here reaches another
#: process, another machine or a remote service; a ``read-only`` label on one of
#: them would let a caller believe a dry run happened.
NEVER_READ_ONLY = frozenset({
    "coord-send", "coord-wake", "coord-wake-dispatch", "coord-commander",
    "coord-assign", "coord-chat-publish", "coord-transcript", "coord-battle-order",
    "coord-cdp", "coord-clawchat", "coord-uia", "federate", "commit",
})

FAMILIES = (
    ("coord-", "协调面（coord-*）"),
    ("kernel-", "恢复内核（kernel-*）"),
    ("federate", "联邦入口（federate）"),
)


def _jsonable(value: Any) -> Any:
    """Anything a default can be, in a form ``json.dumps`` will accept.

    ``Path`` is the common case (``--root`` defaults to ``Path(".agent-evolution")``)
    and is also the reason not to just call ``str()`` on everything: a boolean
    default stringified to ``"False"`` reads as truthy to a careless consumer.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)


def risk_of(command: str) -> str:
    if command in READ_ONLY_COMMANDS:
        return READ_ONLY
    if command in IRREVERSIBLE_COMMANDS:
        return IRREVERSIBLE
    return MUTATING


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    """The one place that touches argparse internals, on purpose.

    ``argparse`` exposes no public API for enumerating subparsers, and the
    alternative -- restating all 87 commands in a table here -- is exactly the
    duplication this module exists to remove.  Keeping the reach in one function
    means an ``argparse`` change breaks one clearly-named thing, and
    ``test_skill_manifest`` asserts the count so it cannot break quietly.
    """
    for action in parser._actions:  # noqa: SLF001 - see docstring
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return action
    raise RuntimeError("this parser has no subcommands, so there is no surface to describe")


def describe_argument(action: argparse._StoreAction) -> dict[str, Any]:  # noqa: SLF001
    """One flag or positional, in the terms a caller actually needs."""
    flags = list(action.option_strings)
    if flags:
        kind = "flag" if isinstance(action, (argparse._StoreTrueAction,  # noqa: SLF001
                                            argparse._StoreFalseAction)) else "option"
    else:
        kind = "positional"
    required = True if kind == "positional" and action.nargs not in ("?", "*") else bool(
        getattr(action, "required", False))
    row: dict[str, Any] = {
        "flags": flags,
        "dest": action.dest,
        "kind": kind,
        "required": required,
    }
    if action.nargs not in (None, 1):
        row["nargs"] = action.nargs
    choices = getattr(action, "choices", None)
    if choices is not None:
        row["choices"] = sorted(str(item) for item in choices)
    if action.default is not None and action.default is not argparse.SUPPRESS:
        row["default"] = _jsonable(action.default)
    help_text = (action.help or "").strip()
    if help_text and help_text is not argparse.SUPPRESS:
        row["help"] = help_text
    return row


def _first_sentence(text: str) -> str:
    """One line of purpose, from prose that already exists.

    Most commands never got an argparse ``help=``, so the first draft of this
    manifest rendered 60-odd rows as ``—``: a table that describes a surface
    without saying what any of it is for.  The handler docstrings do say, and
    they are maintained because they are read -- so they are the source, and
    the fallback order is description, then docstring, then nothing.
    """
    collapsed = " ".join((text or "").split())
    if not collapsed:
        return ""
    for index, character in enumerate(collapsed):
        if character in "。.！!？?" and index + 1 < len(collapsed):
            return collapsed[: index + 1]
    return collapsed


def _purpose(parser: argparse.ArgumentParser) -> str:
    described = _first_sentence(parser.description or "")
    if described:
        return described
    handler = parser.get_default("func")
    return _first_sentence(getattr(handler, "__doc__", "") or "")


def describe_command(name: str, parser: argparse.ArgumentParser) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": name,
        "risk": risk_of(name),
        "arguments": [describe_argument(action) for action in parser._actions
                      if action.dest != "help"],
    }
    purpose = _purpose(parser)
    # Recorded either way.  First draft dropped the empty ones and rendered 64
    # rows as "—", which reads like a formatting artefact instead of what it is:
    # most of the surface has no description anywhere near its definition.
    row["documented"] = bool(purpose)
    if purpose:
        row["purpose"] = purpose
    sub = [action for action in parser._actions
           if isinstance(action, argparse._SubParsersAction)]  # noqa: SLF001
    if sub:
        # Only one command nests today; recording it keeps the manifest honest
        # instead of silently dropping the deeper surface.
        row["subcommands"] = sorted(sub[0].choices)
    return row


def build(parser: argparse.ArgumentParser | None = None) -> dict[str, Any]:
    """Describe the whole command surface from the parser that defines it."""
    if parser is None:
        from agent_evolution_protocol.cli import build_parser

        parser = build_parser()
    sub = _subparsers(parser)
    commands = [describe_command(name, child) for name, child in sorted(sub.choices.items())]
    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_from": "agent_evolution_protocol.cli:build_parser",
        "global_arguments": [describe_argument(action) for action in parser._actions
                             if action.dest not in ("help", "command")],
        "commands": commands,
        "count": len(commands),
        "by_risk": {level: sum(1 for row in commands if row["risk"] == level)
                    for level in RISK_ORDER},
        "documented": sum(1 for row in commands if row["documented"]),
        "undocumented": sum(1 for row in commands if not row["documented"]),
    }
    manifest["digest"] = digest(manifest)
    return manifest


def digest(manifest: dict[str, Any]) -> str:
    """A digest over the parts a caller would act on, not over the whole file.

    ``digest`` itself is excluded or the value could never be reproduced, and
    key order is fixed so two hosts agree.  ``ensure_ascii=False`` keeps the
    Chinese help text readable in the hashed payload, which matters when a
    mismatch has to be explained to a person.
    """
    payload = {key: value for key, value in manifest.items() if key != "digest"}
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + sha256(text.encode("utf-8")).hexdigest()


def _required_hint(row: dict[str, Any]) -> str:
    parts = []
    for argument in row["arguments"]:
        if not argument.get("required"):
            continue
        if argument["kind"] == "positional":
            parts.append(argument["dest"].upper())
        else:
            parts.append(argument["flags"][0])
    return " ".join(parts) or "—"


def _family_of(command: str) -> str:
    for prefix, label in FAMILIES:
        if command.startswith(prefix):
            return label
    return "工作区与回合（其他）"


def render_markdown(manifest: dict[str, Any]) -> str:
    """A human-readable surface, generated so it cannot describe a different one."""
    lines = [
        "# AEP 技能清单",
        "",
        "> **本文件由 `aep coord-skill-manifest write` 生成，请勿手改。**",
        "> 手改会让 `tests/test_skill_manifest.py` 失败——这是刻意的：",
        "> 上一版研究文档把 `schema=aep-next-action-v1` 写错了整整两天，因为散文不会报错。",
        "",
        f"- 命令总数：**{manifest['count']}**",
        "- 风险分布：" + "、".join(f"{level} {manifest['by_risk'][level]}"
                                   for level in RISK_ORDER),
        f"- 用途已记录：**{manifest['documented']}** / {manifest['count']}"
        f"（未记录 {manifest['undocumented']}，只允许减少）",
        f"- 清单摘要：`{manifest['digest']}`",
        "",
        "风险分级是**声明**而不是推导：未登记的命令一律按 `mutating` 处理。",
        "把会写的命令标成 `read-only` 是这里唯一有代价的错误，所以默认值朝安全方向倒。",
        "",
        "用途优先取 argparse 的 `help`，取不到就退到处理函数的 docstring 首句。",
        "标 `未记录` 的命令不是渲染问题——它在定义处就没有说明，`tests/test_skill_manifest.py`",
        "把未记录数量钉成棘轮，只许降不许升。",
        "",
    ]
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in manifest["commands"]:
        groups.setdefault(_family_of(row["name"]), []).append(row)
    for label, rows in groups.items():
        lines += [f"## {label}", "", "| 命令 | 风险 | 必填 | 用途 |", "| --- | --- | --- | --- |"]
        for row in rows:
            purpose = (row.get("purpose") or "").replace("|", "\\|").strip()
            if not purpose:
                purpose = "*未记录*"
            lines.append(f"| `{row['name']}` | {row['risk']} | "
                         f"`{_required_hint(row)}` | {purpose} |")
        lines.append("")
    lines += [
        "## 全局参数",
        "",
        "| 参数 | 默认 | 用途 |",
        "| --- | --- | --- |",
    ]
    for argument in manifest["global_arguments"]:
        flag = argument["flags"][0] if argument["flags"] else argument["dest"]
        default = argument.get("default")
        purpose = (argument.get("help") or "—").replace("|", "\\|")
        lines.append(f"| `{flag}` | `{default if default is not None else '—'}` | {purpose} |")
    lines.append("")
    return "\n".join(lines)


def paths(directory: str | Path) -> tuple[Path, Path]:
    base = Path(directory)
    return base / MANIFEST_NAME, base / SKILL_NAME


def write(directory: str | Path, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write both artefacts atomically and report what changed.

    ``os.replace`` rather than a plain write so a crash mid-write cannot leave a
    half-manifest that the guard test then reports as drift.
    """
    manifest = manifest or build()
    manifest_path, skill_path = paths(directory)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    for target, text in ((manifest_path, body), (skill_path, render_markdown(manifest))):
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
    return {"schema": "aep-skill-manifest-write-v1", "manifest": str(manifest_path),
            "skill": str(skill_path), "count": manifest["count"],
            "digest": manifest["digest"]}


def check(directory: str | Path, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compare what is committed against what the parser says today.

    ``missing``/``extra`` are reported by name rather than as a boolean, because
    "the surface drifted" is not actionable and "``coord-foo`` exists in code
    but not in the manifest" is.
    """
    manifest = manifest or build()
    manifest_path, skill_path = paths(directory)
    report: dict[str, Any] = {"schema": "aep-skill-manifest-check-v1",
                              "manifest": str(manifest_path), "skill": str(skill_path),
                              "in_sync": True, "reasons": []}
    if not manifest_path.is_file():
        report["in_sync"] = False
        report["reasons"].append("MANIFEST_MISSING")
        report["missing"] = sorted(row["name"] for row in manifest["commands"])
        return report
    try:
        recorded = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report["in_sync"] = False
        report["reasons"].append("MANIFEST_UNREADABLE")
        return report
    # Recompute from the file's own contents before comparing. Trusting the
    # stored digest was a real hole: a hand-edited manifest keeps the digest it
    # was written with, so the comparison below passed on a file that no longer
    # described anything. Measured 2026-11-02 -- the drift test caught it.
    recomputed = digest(recorded)
    if recorded.get("digest") != recomputed:
        report["in_sync"] = False
        report["reasons"].append("DIGEST_NOT_SELF_CONSISTENT")
    if recomputed != manifest["digest"]:
        report["in_sync"] = False
        report["reasons"].append("DIGEST_MISMATCH")
        report["recorded_digest"] = recomputed
        report["live_digest"] = manifest["digest"]
        old = {row["name"]: row for row in recorded.get("commands", [])}
        new = {row["name"]: row for row in manifest["commands"]}
        report["missing"] = sorted(set(new) - set(old))
        report["extra"] = sorted(set(old) - set(new))
        report["changed"] = sorted(name for name in set(old) & set(new) if old[name] != new[name])
    if not skill_path.is_file():
        report["in_sync"] = False
        report["reasons"].append("SKILL_MISSING")
    elif skill_path.read_text(encoding="utf-8") != render_markdown(manifest):
        report["in_sync"] = False
        report["reasons"].append("SKILL_STALE")
    return report
