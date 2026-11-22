"""Optional local-model brief for a relay checkpoint.

The model is a *reader*, not an authority.  It receives a bounded checkpoint
and may return a short summary, a next-step restatement and risk notes.  It
never changes role state, never chooses a successor and never authorises an
upload.  A missing server or malformed output is an abstention, not a reason
to block the deterministic relay path.

The default adapter speaks to an already-running LM Studio server.  It does
not start a server, load a model, or keep a process resident.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import urllib.request

DEFAULT_ENDPOINT = "http://127.0.0.1:1234"
DEFAULT_MODEL = "qwen3-0.6b"
MODEL_FILE = Path(".models") / "Qwen3-0.6B" / "model.safetensors"

SCHEMA = "aep-relay-local-brief-v1"
NOTE = (
    "Local-model output is advisory only.  It is not an ACK, not a quota "
    "proof and not permission to change a role or execute a command."
)

PROMPT = """你是多 agent 协作项目的交接记录员。你会收到一份 JSON 检查点，包含
next_step、completed、artifacts、pending_operations、unknowns。

只做三件事：用简短中文概括当前状态、复核下一步、列出最多 3 个风险或未知项。

严格要求：
- 只输出 JSON，不要解释。格式：
  {"summary": "<概括>", "next_step": "<下一步>", "risks": ["<风险>", "..."]}
- 如果信息不足，输出 {"unknown": true}。不要猜。
- 不要输出命令、路径、脚本、事件 ID、授权结论、角色归属或执行指令。
- 你的输出只是建议文字，不是事实判定，也不会被自动执行。
"""


def model_files_present(root=None) -> bool:
    base = Path(root) if root is not None else Path.cwd()
    return (base / MODEL_FILE).is_file()


def _clean_text(value, label, maximum):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label)
    text = " ".join(value.split())
    if len(text) > maximum:
        text = text[:maximum]
    return text


def parse_brief(raw: str) -> dict | None:
    """Validate a model reply; malformed or abstaining output becomes None."""
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict) or parsed.get("unknown") is True:
        return None
    try:
        summary = _clean_text(parsed.get("summary"), "summary", 600)
        next_step = _clean_text(parsed.get("next_step"), "next_step", 600)
        risks = parsed.get("risks", [])
        if risks is None:
            risks = []
        if not isinstance(risks, list) or len(risks) > 3:
            return None
        cleaned_risks = []
        for item in risks:
            cleaned_risks.append(_clean_text(item, "risk", 300))
    except (TypeError, ValueError):
        return None
    return {"summary": summary, "next_step": next_step, "risks": cleaned_risks}


def _checkpoint_text(checkpoint: dict) -> str:
    if not isinstance(checkpoint, dict):
        raise ValueError("checkpoint must be an object")
    material = {
        "next_step": checkpoint.get("next_step"),
        "completed": checkpoint.get("completed", []),
        "artifacts": checkpoint.get("artifacts", []),
        "pending_operations": checkpoint.get("pending_operations", []),
        "unknowns": checkpoint.get("unknowns", []),
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True)
    if len(encoded) > 12000:
        raise ValueError("checkpoint is too large for a local brief")
    return encoded


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def lmstudio_ask(*, endpoint: str = "", model: str = "", timeout: float = 10.0):
    endpoint = (endpoint or os.environ.get("AEP_LMSTUDIO_ENDPOINT",
                                           DEFAULT_ENDPOINT)).rstrip("/")
    model = model or os.environ.get("AEP_RELAY_MODEL", DEFAULT_MODEL)
    url = f"{endpoint}/v1/chat/completions"

    def ask(text: str) -> str:
        body = _post_json(url, {
            "model": model,
            "messages": [
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": text},
            ],
            "stream": False,
            "temperature": 0,
            "max_tokens": 320,
            "response_format": {"type": "json_object"},
        }, timeout)
        choices = body.get("choices") or []
        return ((choices[0].get("message") or {}).get("content") or "") if choices else ""

    return ask


def local_brief(checkpoint: dict, *, ask=None, root=None, endpoint: str = "",
                model: str = "", timeout: float = 10.0) -> dict:
    """Return an advisory brief or an explicit abstention; never writes state."""
    text = _checkpoint_text(checkpoint)
    selected_model = model or os.environ.get("AEP_RELAY_MODEL", DEFAULT_MODEL)
    selected_endpoint = (endpoint or os.environ.get("AEP_LMSTUDIO_ENDPOINT",
                                                   DEFAULT_ENDPOINT)).rstrip("/")
    if ask is None:
        if not model_files_present(root):
            return {
                "schema": SCHEMA, "state": "UNAVAILABLE",
                "reason": "MODEL_FILES_MISSING", "advisory": None,
                "model": selected_model, "endpoint": selected_endpoint,
                "note": NOTE,
            }
        ask = lmstudio_ask(endpoint=selected_endpoint, model=selected_model,
                           timeout=timeout)
    try:
        raw = ask(text)
    except Exception as exc:
        return {
            "schema": SCHEMA, "state": "UNAVAILABLE",
            "reason": f"{type(exc).__name__}: {exc}"[:300], "advisory": None,
            "model": selected_model, "endpoint": selected_endpoint,
            "note": NOTE,
        }
    advisory = parse_brief(raw)
    if advisory is None:
        return {
            "schema": SCHEMA, "state": "ABSTAINED", "reason": "INVALID_OR_UNKNOWN",
            "advisory": None, "model": selected_model,
            "endpoint": selected_endpoint, "note": NOTE,
        }
    return {
        "schema": SCHEMA, "state": "ADVISORY", "reason": "",
        "advisory": advisory, "model": selected_model,
        "endpoint": selected_endpoint, "note": NOTE,
    }