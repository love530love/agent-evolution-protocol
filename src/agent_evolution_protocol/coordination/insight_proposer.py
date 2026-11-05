"""Optional local-model advisory for `insight_miner`.

Kept out of the miner: the fact layer has to stay deterministic and socket-free.
This module talks to a local model over LM Studio's OpenAI-compatible endpoint
(default qwen3-0.6b) or Ollama's /api/chat (default qwen3:4b), and may only emit
an advisory note — a title, a hypothesis, and up to five suggested checks. It
never sees evidence file contents, never decides severity, and never produces
commands, paths, or event identities. `insight_miner.validate_advisory()` owns
the allowlist and runs again on the caller side, so this module is not trusted.

Abstention is a real answer. A dead model costs the note, not the finding: the
miner keeps its rule-only results. Nothing here is imported by the default path.
"""
from __future__ import annotations

import json
import os
import urllib.request

from .insight_miner import validate_advisory

DEFAULT_MODEL = "qwen3:4b"
DEFAULT_ENDPOINT = "http://127.0.0.1:11434"
LMSTUDIO_MODEL = "qwen3-0.6b"
LMSTUDIO_ENDPOINT = "http://127.0.0.1:1234"
BACKENDS = ("ollama", "lmstudio")

PROMPT = """你是软件项目的问题线索整理助手。你会收到一条由纯规则从日志里统计出的线索，
包含 rule（规则名）、count（命中条数）、scanned（扫描条数）、summary（规则生成的描述）。

只做三件事：给线索起一个简短中文标题、提出一个可能原因的假设、列出最多 5 条人工检查方向。

严格要求：
- 只输出 JSON，不要解释。格式：
  {"title": "<标题>", "hypothesis": "<假设>", "checks": ["<检查>", "..."]}
- 如果信息不足以判断，输出 {"unknown": true}。不要猜。
- 不要输出命令、路径、脚本、事件 ID、严重程度、修复补丁或任何执行指令。
- 你的输出只是建议文字，不是事实判定，也不会被自动执行。
"""


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_advisory(raw: str) -> dict | None:
    """Parse and validate. Unknown fields, abstention and malformed JSON all
    collapse to `None`, which the miner treats as 'no note, keep the finding'."""
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict) or parsed.get("unknown") is True:
        return None
    return validate_advisory(parsed)


def describe(finding: dict) -> str:
    """Only the deterministic summary reaches the model, never file contents."""
    return json.dumps({
        "rule": finding.get("rule"),
        "count": finding.get("count"),
        "scanned": finding.get("scanned"),
        "summary": finding.get("summary"),
    }, ensure_ascii=False)


def local_model_proposer(backend: str = "ollama", model: str = "", endpoint: str = "",
                         timeout: float = 10.0, ask=None):
    """Return a `propose(finding)` callable backed by a local, ephemeral model.

    `backend` selects the wire shape: Ollama `/api/chat` or LM Studio's
    OpenAI-compatible `/v1/chat/completions`. Loading/unloading is owned by
    `local_model_session.EphemeralModelSession`; this function only speaks HTTP.

    `ask` is injectable so parsing, abstention and failure handling are testable
    without a socket. Any failure propagates to the miner, which degrades to the
    rule-only result.
    """
    if backend not in BACKENDS:
        raise ValueError(f"backend must be one of {BACKENDS}")
    if backend == "lmstudio":
        model = model or os.environ.get("AEP_INSIGHT_MODEL", LMSTUDIO_MODEL)
        endpoint = (endpoint or os.environ.get("AEP_LMSTUDIO_ENDPOINT",
                                               LMSTUDIO_ENDPOINT)).rstrip("/")
        url = f"{endpoint}/v1/chat/completions"
    else:
        model = model or os.environ.get("AEP_INSIGHT_MODEL", DEFAULT_MODEL)
        endpoint = (endpoint or os.environ.get("AEP_OLLAMA_ENDPOINT",
                                               DEFAULT_ENDPOINT)).rstrip("/")
        url = f"{endpoint}/api/chat"

    def ask_backend(text: str) -> str:
        messages = [{"role": "system", "content": PROMPT},
                    {"role": "user", "content": text}]
        if backend == "lmstudio":
            body = _post_json(url, {"model": model, "messages": messages,
                                    "stream": False, "temperature": 0,
                                    "max_tokens": 240,
                                    "response_format": {"type": "json_object"}}, timeout)
            choices = body.get("choices") or []
            return ((choices[0].get("message") or {}).get("content") or "") if choices else ""
        body = _post_json(url, {"model": model, "messages": messages,
                                "stream": False, "format": "json", "think": False,
                                "options": {"temperature": 0, "num_predict": 240}}, timeout)
        return (body.get("message") or {}).get("content") or ""

    ask = ask or ask_backend

    def propose(finding: dict):
        return parse_advisory(ask(describe(finding)))

    return propose


# Backwards-compatible name for callers that predate the LM Studio backend.
ollama_insight_proposer = local_model_proposer
