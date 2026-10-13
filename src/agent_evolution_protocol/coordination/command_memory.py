"""Local command pitfall memory: classify, deduplicate, suggest, verify.

No model calls or automatic command retries. Journal excerpts are leads, not
proof. A recipe is promoted only with an explicit successful run and evidence.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from uuid import uuid4

from . import bridge, conversation_journal

RULES = {
    "long_path": {
        "patterns": [r"WinError 206", r"ERROR_FILENAME_EXCED_RANGE", r"Filename too long", r"path too long", r"路径过长", r"MAX_PATH"],
        "title": "Windows 长路径优先用 subst 缩短编译根路径",
        "guidance": "先确认目标目录和空闲盘符；用 subst 把构建根目录映射成如 R:\\，在短盘符下构建。不要覆盖现有盘符；完成后仅解除自己创建且目标仍匹配的映射。",
        "example": "& '.\\scripts\\short-path.ps1' -Action map -Directory 'K:\\very\\long\\project\\path' -Drive R; Set-Location 'R:\\'",
    },
    "shell_version": {
        "patterns": [r"powershell\.exe.+(?:failed|not found|error|失败)", r"Windows PowerShell 5\.1.+(?:incompatible|不兼容|失败)", r"pwsh(?:\.exe)? not found"],
        "title": "默认使用 PowerShell 7，隔离确证的 5.1 兼容例外",
        "guidance": "在 Windows 使用 pwsh.exe。仅当某 API 在 PS7 不可用且 PS5.1 已实测可用时，将该调用隔离在 5.1 辅助进程。",
        "example": "$pwsh = (Get-Command pwsh.exe -ErrorAction Stop).Source; & $pwsh -NoProfile -File '.\\scripts\\task.ps1'",
    },
    "encoding": {
        "patterns": [r"UnicodeDecodeError", r"UnicodeEncodeError", r"invalid (?:utf-?8|encoding)", r"锟斤拷", r"ï»¿"],
        "title": "显式约定 UTF-8，不靠终端默认编码",
        "guidance": "PowerShell 7 的文件输入输出显式指定 UTF8；Python 读取文件指定 encoding='utf-8'，捕获原生进程输出时允许替换坏字节并保留原始日志。",
        "example": "Get-Content -LiteralPath 'K:\\path with spaces\\input.json' -Raw -Encoding UTF8 | ConvertFrom-Json -DateKind String",
    },
    "quoting": {
        "patterns": [r"ParserError", r"Unexpected token", r"TerminatorExpected", r"MissingEndQuote", r"unterminated string", r"转义", r"引号"],
        "title": "路径作为独立参数传递，不拼接一整条命令字符串",
        "guidance": "PowerShell 路径用单引号或变量承载；调用脚本用 & 和参数数组。不要把未经验证的文本交给 Invoke-Expression 或 cmd /c 再解析一遍。",
        "example": "$script = 'K:\\project with spaces\\scripts\\run.ps1'; & pwsh.exe -NoProfile -File $script -InputPath 'K:\\data\\a b.json'",
    },
    "command_not_found": {
        "patterns": [r"CommandNotFoundException", r"is not recognized as (?:the name|an internal)", r"The term .+ is not recognized", r"Get-Command.+cannot find"],
        "title": "先定位可执行文件和环境，再运行",
        "guidance": "用 Get-Command <name> 验证可执行文件。缺失时报告环境阻塞，不要盲目改用同名旧版或另一 shell。",
        "example": "$tool = (Get-Command pwsh.exe -ErrorAction Stop).Source; & $tool -NoProfile -File '.\\scripts\\task.ps1'",
    },
    "parameter_binding": {
        "patterns": [r"ParameterBindingException", r"A parameter cannot be found", r"Cannot bind parameter", r"ambiguous parameter"],
        "title": "确认当前版本的参数签名",
        "guidance": "先检查 Get-Command <cmd> -Syntax 或 Get-Help <cmd> -Full；PS5.1 与 PS7 参数不保证相同。",
        "example": "Get-Command ConvertFrom-Json -Syntax; $item = Get-Content -LiteralPath '.\\state.json' -Raw -Encoding UTF8 | ConvertFrom-Json -DateKind String",
    },
    "path_or_cwd": {
        "patterns": [r"FileNotFoundError", r"Cannot find path", r"No such file or directory", r"The system cannot find the path", r"找不到路径"],
        "title": "核对工作目录与目标绝对路径",
        "guidance": "先用 Get-Location、Resolve-Path -LiteralPath、Test-Path -LiteralPath 核实；不把相对路径解析失败归咎于代码。",
        "example": "$target = 'K:\\project\\file.txt'; if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw 'Missing input' }; Resolve-Path -LiteralPath $target",
    },
    "permission": {
        "patterns": [r"AccessDenied", r"PermissionError", r"UnauthorizedAccessException", r"access is denied", r"拒绝访问"],
        "title": "区分权限、锁占用与路径错误",
        "guidance": "记录目标绝对路径、进程身份和失败操作；不要自动提权、放宽 ACL 或改写其他用户文件。",
        "example": "$target = 'K:\\project\\file.txt'; Get-Item -LiteralPath $target | Select-Object FullName,Attributes; whoami",
    },
}
ORDER = ("long_path", "encoding", "parameter_binding", "command_not_found", "path_or_cwd",
         "permission", "quoting", "shell_version")
MAX_EXCERPT = 4000


def root_dir(coord_root: Path) -> Path:
    return Path(coord_root) / "command_memory"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def classify(text: str) -> str:
    for key in ORDER:
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in RULES[key]["patterns"]):
            return key
    return "unknown"


def signature(text: str, category: str) -> str:
    line = next((row.strip() for row in text.splitlines() if row.strip()), "")[:500]
    line = re.sub(r"[A-Za-z]:[/\\][^\s\"']+", "<path>", line)
    line = re.sub(r"0x[0-9a-f]+|\b\d+\b", "#", line, flags=re.IGNORECASE)
    line = re.sub(r"\s+", " ", line).lower()
    return hashlib.sha256((category + "\n" + line).encode("utf-8")).hexdigest()[:20]


def record(coord_root: Path, *, agent: str, command: str, output: str,
           exit_code: int | None, source: str, evidence: str = "",
           category_hint: str = "") -> dict:
    if not agent.strip() or not source.strip() or not (command.strip() or output.strip()):
        raise ValueError("agent, source and command/output required")
    if exit_code is not None and not isinstance(exit_code, int):
        raise ValueError("exit code must be integer or unknown")
    if category_hint and category_hint not in RULES:
        raise ValueError("unknown category hint")
    category = category_hint or classify(output or command)
    outcome = "reported" if exit_code is None else "observed_success" if exit_code == 0 else "observed_failure"
    sig = signature(output or command, category)
    at = datetime.now(timezone.utc)
    item = {"id": at.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:12],
            "at": at.isoformat(), "agent": agent, "source": source, "command": command[:2000],
            "output_excerpt": output[-MAX_EXCERPT:], "exit_code": exit_code,
            "outcome": outcome, "category": category, "signature": sig,
            "evidence": evidence,
            "trust": "chat_report_only" if exit_code is None else "process_exit_observed_not_semantic_success"}
    path = root_dir(coord_root) / "observations" / f"{item['id']}.json"
    _atomic(path, item)
    return item


def ingest_journal(coord_root: Path, *, journal_root: Path | None = None,
                   limit: int = 500) -> dict:
    memory = root_dir(coord_root)
    journal_root = Path(journal_root) if journal_root is not None else Path(coord_root)
    source_id = hashlib.sha256(str(journal_root.resolve()).lower().encode("utf-8")).hexdigest()[:12]
    cursor_path = memory / f"journal_cursor-{source_id}.json"
    cursor = str(_read(cursor_path).get("through", ""))
    events = conversation_journal.read_events(journal_root, after=cursor, limit=limit)
    learned = []
    for event in events:
        text = str(event.get("text", ""))
        category = classify(text)
        looks_like_failure = bool(re.search(
            r"\b(?:error|exception|failed|failure|not found|not recognized)\b|"
            r"(?:ParserError|UnicodeDecodeError|ParameterBindingException|WinError)"
            r"|失败|报错|无法|找不到|过长",
            text, re.IGNORECASE))
        if category != "unknown" and looks_like_failure and event.get("kind") in {"message", "result", "warning", "status"}:
            learned.append(record(coord_root, agent=str(event.get("actor") or "unknown"),
                                  command="", output=text, exit_code=None,
                                  source="conversation_journal", evidence=event["id"]))
    if events:
        _atomic(cursor_path, {"through": events[-1]["id"], "updated_at": _now()})
    return {"scanned": len(events), "learned": len(learned), "through": events[-1]["id"] if events else cursor}


def _observations(coord_root: Path) -> list[dict]:
    return [_read(path) for path in sorted((root_dir(coord_root) / "observations").glob("*.json"))]


def suggest(coord_root: Path, *, text: str, limit: int = 5) -> dict:
    category = classify(text)
    counts: dict[str, int] = {}
    for row in _observations(coord_root):
        if row.get("outcome") != "observed_success":
            counts[row.get("category", "unknown")] = counts.get(row.get("category", "unknown"), 0) + 1
    ranked = ([category] if category != "unknown" else []) + [key for key, _ in sorted(
        counts.items(), key=lambda pair: (-pair[1], pair[0])) if key != category]
    ranked += [key for key in ORDER if key not in ranked]
    cards = [{"category": key, **RULES[key], "observed_pitfalls": counts.get(key, 0),
              "confidence": "curated_guidance_not_environment_validation"}
             for key in ranked if key in RULES][:limit]
    recipes = []
    for path in sorted((root_dir(coord_root) / "recipes").glob("*.json")):
        row = _read(path)
        if row.get("category") == category or not text.strip():
            recipes.append(row)
    return {"matched_category": category, "cards": cards, "verified_recipes": recipes[:limit],
            "note": "Reported errors are leads; exit 0 alone is not proof the task was correct."}


def promote(coord_root: Path, *, observation_id: str, title: str, why: str,
            evidence_file: Path, reviewer: str) -> dict:
    observation = _read(root_dir(coord_root) / "observations" / f"{observation_id}.json")
    if observation.get("outcome") != "observed_success":
        raise ValueError("only an observed successful command can become a recipe")
    if not title.strip() or not why.strip() or not reviewer.strip() or evidence_file is None or not evidence_file.is_file():
        raise ValueError("title, explanation, reviewer and existing evidence file required")
    evidence_hash = hashlib.sha256(evidence_file.read_bytes()).hexdigest()
    recipe = {"id": observation_id, "category": observation["category"],
              "title": title, "command": observation["command"], "why": why,
              "source_observation": observation_id, "evidence_file": str(evidence_file.resolve()),
              "evidence_sha256": evidence_hash, "reviewer": reviewer,
              "promoted_at": _now(), "scope": "validated_here_not_guaranteed_on_other_hosts"}
    _atomic(root_dir(coord_root) / "recipes" / f"{observation_id}.json", recipe)
    return recipe


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def run(coord_root: Path, *, agent: str, argv: list[str], timeout: int = 120,
        category_hint: str = "") -> dict:
    if not argv or not 1 <= timeout <= 3600:
        raise ValueError("nonempty argv and timeout 1..3600 seconds required")
    if not agent.strip():
        raise ValueError("agent required")
    try:
        completed = subprocess.run(argv, shell=False, capture_output=True, timeout=timeout, check=False)
        out = _decode(completed.stdout)
        err = _decode(completed.stderr)
        code = completed.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        out = ""
        err = str(exc)
        code = 124 if isinstance(exc, subprocess.TimeoutExpired) else 127
    observation = record(coord_root, agent=agent, command=json.dumps(argv, ensure_ascii=False),
                         output=err or out, exit_code=code, source="command_wrapper",
                         category_hint=category_hint)
    return {"exit_code": code, "stdout": out[-MAX_EXCERPT:], "stderr": err[-MAX_EXCERPT:],
            "observation": observation, "suggestion": suggest(coord_root, text=err or out, limit=2)}


def digest(coord_root: Path) -> dict:
    groups: dict[str, dict] = {}
    for item in _observations(coord_root):
        key = item.get("category", "unknown")
        row = groups.setdefault(key, {"category": key, "reported": 0,
                                      "observed_failure": 0, "observed_success": 0,
                                      "signatures": {}})
        outcome = item.get("outcome", "reported")
        if outcome in row:
            row[outcome] += 1
        sig = item.get("signature", "")
        if sig:
            row["signatures"][sig] = row["signatures"].get(sig, 0) + 1
    summary = []
    for row in groups.values():
        row["top_signatures"] = sorted(row.pop("signatures").items(),
                                       key=lambda pair: (-pair[1], pair[0]))[:5]
        summary.append(row)
    summary.sort(key=lambda row: (-(row["reported"] + row["observed_failure"]), row["category"]))
    return {"groups": summary, "note": "Counts are evidence volume, not independently validated root causes."}


def announce(coord_root: Path, bridge_root: Path, *, repo_root: Path) -> dict:
    docs = [repo_root / "docs" / "coordination" / name for name in
            ("COMMAND_MEMORY.md", "WINDOWS_BUILD_KNOWLEDGE.md", "BROWSER_CAPABILITY_ROUTING.md")]
    if not all(path.is_file() for path in docs):
        raise ValueError("knowledge documents missing")
    digest_value = hashlib.sha256(b"".join(path.read_bytes() for path in docs)).hexdigest()[:12]
    event_id = f"WIN-KNOW-{digest_value}"
    body = ("Windows 本机经验入口已更新。执行命令前先查命令记忆；长路径用受控 subst 短盘符；"
            "浏览器优先本 Agent 已验收的内置能力，缺能力再用 AI Browser Bridge。\n"
            + "\n".join(str(path.resolve()) for path in docs)
            + "\n请按任务读取适用文档；历史案例不是当前环境的自动授权或成功证明。")
    sent = []
    for agent in bridge.AGENTS:
        path = Path(bridge_root) / "messages" / agent / f"{event_id}.json"
        if path.exists():
            continue
        bridge.write(path, {"id": event_id, "at": _now(), "sender": "command_memory",
                            "to": agent, "topic": "windows-host-knowledge", "kind": "context",
                            "text": body})
        sent.append(agent)
    marker = root_dir(coord_root) / "announcements" / f"{event_id}.json"
    if not marker.exists():
        _atomic(marker, {"id": event_id, "at": _now(), "recipients": list(bridge.AGENTS),
                         "documents": [str(path.resolve()) for path in docs]})
    return {"event_id": event_id, "new_mailboxes": sent, "note": "Mailbox delivery is not read or ACK."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("record", "ingest-journal", "suggest", "promote", "run", "status", "digest", "announce"))
    parser.add_argument("--root", type=Path, default=Path.cwd() / "coordination")
    parser.add_argument("--journal-root", type=Path)
    parser.add_argument("--bridge-root", type=Path, default=bridge.ROOT)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--agent", default="")
    parser.add_argument("--command", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--exit-code", type=int)
    parser.add_argument("--source", default="agent-report")
    parser.add_argument("--evidence", default="")
    parser.add_argument("--text", default="")
    parser.add_argument("--observation-id", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--why", default="")
    parser.add_argument("--evidence-file", type=Path)
    parser.add_argument("--reviewer", default="")
    parser.add_argument("--category", default="")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.action == "record":
            result = record(args.root, agent=args.agent, command=args.command,
                            output=args.output, exit_code=args.exit_code,
                            source=args.source, evidence=args.evidence,
                            category_hint=args.category)
        elif args.action == "ingest-journal":
            result = ingest_journal(args.root, journal_root=args.journal_root)
        elif args.action == "suggest":
            result = suggest(args.root, text=args.text)
        elif args.action == "promote":
            result = promote(args.root, observation_id=args.observation_id,
                             title=args.title, why=args.why, evidence_file=args.evidence_file,
                             reviewer=args.reviewer)
        elif args.action == "run":
            argv = args.argv[1:] if args.argv and args.argv[0] == "--" else args.argv
            result = run(args.root, agent=args.agent, argv=argv, timeout=args.timeout,
                         category_hint=args.category)
        elif args.action == "digest":
            result = digest(args.root)
        elif args.action == "announce":
            result = announce(args.root, args.bridge_root, repo_root=args.repo_root)
        else:
            observations = _observations(args.root)
            result = {"observations": len(observations),
                      "reported": sum(row.get("outcome") == "reported" for row in observations),
                      "recipes": len(list((root_dir(args.root) / "recipes").glob("*.json")))}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
