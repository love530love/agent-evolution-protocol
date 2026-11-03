"""Optional proposal source for `routing.classify_intent_with_proposal`.

Deliberately kept out of `routing.py`: routing has to stay deterministic and socket-free so a
test can prove what the rules do without a model, a network, or a GPU. This module is the only
place that talks to Ollama, and it is never imported by the default path.

Why a proposal is worth having at all (measured, `.tools/MODEL_CHOICE_20261103.zh-CN.md`):
the rule engine answered 0 of 17 phrases that hit no keyword, because every miss falls back to
`project-orientation` plus a clarification request. A local 4B model answered 15 of those 17.
It also misread 4 of 8 explicit negations and was more permissive on the safety-critical
subset — which is exactly why the caller gates it and this module never decides anything.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_MODEL = "qwen3:4b"
DEFAULT_ENDPOINT = "http://127.0.0.1:11434"

SCENARIOS = {
    "project-orientation": "新成员加入/接入协作区，问要准备什么、规则是什么",
    "task-join": "把某个具体任务的执行也接上，与其他工作并行",
    "takeover": "从别人手里接手/接管一项正在进行的工作",
    "review-only": "只审查、只评估、只读，明确不执行任何写操作",
    "isolated-exploration": "多方各自独立出候选方案，最后比较择优",
    "shared-resource-lock": "要占用共享资源（浏览器、上传、提交、发布、删除、支付）",
    "stalled-recovery": "某个 agent 卡住/失联/失败，需要恢复或重试",
    "wake-agent": "通知或唤醒某个 agent 继续干活",
    "status-report": "汇报当前状态、进展、什么情况",
    "cli-less-fallback": "执行方没有命令行能力，只能读文件，需要降级方案",
}

PROMPT = """你是一个任务路由器。把用户的请求归到下面 10 个场景之一。

{scenarios}

严格要求：
- 只输出 JSON，不要解释。格式：{{"scenario": "<场景名>", "unknown": <true|false>}}
- 如果请求**没有明确表达**任何上面场景的意图（例如闲聊、无意义片段、信息不足以判断），
  必须令 unknown=true 且 scenario 填 "project-orientation"。不要猜。
- 如果请求同时包含互相冲突的独立意图，scenario 填 "review-only"，unknown=false。
"""


def build_prompt() -> str:
    return PROMPT.format(scenarios="\n".join(f"- {name}: {text}" for name, text in SCENARIOS.items()))


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def ollama_proposer(model: str = "", endpoint: str = "", timeout: float = 10.0, ask=None):
    """Return a `propose(intent)` callable backed by a local Ollama model.

    `ask` is injectable so tests can exercise parsing, abstention and failure handling without
    a socket. Any failure raises; `classify_intent_with_proposal` turns that into
    `proposer-failed:<Exc>` and keeps the deterministic verdict, so a dead Ollama costs a
    fallback, not an outage.
    """
    model = model or os.environ.get("AEP_ROUTING_MODEL", DEFAULT_MODEL)
    endpoint = (endpoint or os.environ.get("AEP_OLLAMA_ENDPOINT", DEFAULT_ENDPOINT)).rstrip("/")
    url = f"{endpoint}/api/chat"
    system = build_prompt()

    def ask_ollama(intent: str) -> str:
        body = _post_json(url, {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": intent}],
            "stream": False,
            "format": "json",
            "think": False,
            "options": {"temperature": 0, "num_predict": 120},
        }, timeout)
        return (body.get("message") or {}).get("content") or ""

    ask = ask or ask_ollama

    def propose(intent: str):
        raw = ask(intent)
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(parsed, dict) or parsed.get("unknown") is True:
            return None  # abstention is a real answer, and the caller keeps its fallback
        scenario = parsed.get("scenario")
        if not isinstance(scenario, str) or scenario not in SCENARIOS:
            return None
        return {"scenario": scenario, "source": f"ollama:{model}"}

    return propose
