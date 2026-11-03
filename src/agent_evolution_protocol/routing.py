"""Deterministic, model-free intent routing. Classification is not authorization.

The default path never touches a network or a model, and that is enforced by a test rather
than promised by this sentence. `classify_intent_with_proposal` accepts an *injected* proposal
callable and lets it fill exactly one gap — "no rule matched anything" — while the protection,
ambiguity and scenario-name checks keep the final word.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path


KEYWORDS = {
    "project-orientation": ["加入", "接入", "join", "onboard"],
    "task-join": [],
    "takeover": ["接手", "接管", "take over", "takeover"],
    "review-only": ["审查", "评审", "审议", "review", "audit", "catfish", "鲶鱼"],
    "isolated-exploration": ["各自", "独立探索", "并行", "候选", "方案", "parallel", "proposal", "commit-reveal"],
    "shared-resource-lock": ["浏览器", "上传", "提交", "发布", "删除", "支付", "browser", "upload", "submit", "publish", "delete", "payment", "pay"],
    "stalled-recovery": ["卡住", "失联", "失败", "重试", "stalled", "stuck", "unresponsive", "retry"],
    "wake-agent": ["通知", "唤醒", "wake", "notify", "ping"],
    "status-report": ["什么情况", "状态", "汇报", "进展", "status", "summary", "inspect"],
    "cli-less-fallback": ["不能运行命令", "不支持命令", "不支持 cli", "只能读文件", "no cli", "cannot run commands", "read files only"],
}
READ_ONLY = ["只审查", "只看", "只读", "不要执行", "不执行", "不要上传", "不要提交", "不要唤醒", "无需执行", "review only", "read only", "read-only", "do not execute", "don't execute", "do not upload", "do not submit", "do not wake"]
PRIORITY = ("cli-less-fallback", "review-only", "wake-agent", "takeover", "stalled-recovery", "status-report", "shared-resource-lock", "isolated-exploration", "task-join", "project-orientation")
# Scenarios whose execution mutates something outside the agent's own context. Two of these
# joined by a sequencing marker means the order and the lock scope are both undetermined.
WRITE_INTENTS = frozenset({"shared-resource-lock", "wake-agent", "takeover", "stalled-recovery"})
# Scenarios that never write; a proposal may not move a read-only verdict into their complement.
READ_ONLY_SCENARIOS = frozenset({"review-only", "status-report", "project-orientation", "cli-less-fallback"})
COMPOUND_SEQUENCE = re.compile(r"(?:\bthen\b|然后|之后|再(?:上传|提交|发布|删除|支付))")


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def contains(text: str, term: str) -> bool:
    term = normalized(term)
    if re.fullmatch(r"[a-z0-9 _-]+", term):
        return bool(re.search(r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])", text))
    return term in text


def validate_config(config: dict) -> dict:
    if not isinstance(config, dict) or set(config) - {"schema", "keywords", "replace_keywords"}:
        raise ValueError("routing config must contain schema, keywords and optional replace_keywords only")
    if config.get("schema") != "agent-evolution-routing-v1":
        raise ValueError("unsupported routing config schema")
    for key in ("keywords", "replace_keywords"):
        mapping = config.get(key, {})
        if not isinstance(mapping, dict) or set(mapping) - KEYWORDS.keys():
            raise ValueError(f"{key} must map canonical scenarios to literal phrases")
        for phrases in mapping.values():
            if not isinstance(phrases, list) or len(phrases) > 64:
                raise ValueError("each scenario supports at most 64 phrases")
            if any(not isinstance(term, str) or not term.strip() or len(term) > 120 for term in phrases):
                raise ValueError("phrases must be nonempty strings of at most 120 characters")
    return config


def load_route_config(workspace: Path) -> dict | None:
    path = Path(workspace) / ".aep" / "routing.json"
    if not path.exists():
        return None
    if path.stat().st_size > 65536:
        raise ValueError("routing config exceeds 64 KiB")
    try:
        return validate_config(json.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read routing config: {exc}") from exc


def classify_intent(intent: str, *, task: str = "", config: dict | None = None) -> dict:
    text = normalized(intent)
    if not text or len(text) > 16000:
        raise ValueError("intent must contain 1-16000 characters")
    words = {name: list(terms) for name, terms in KEYWORDS.items()}
    if config is not None:
        validate_config(config)
        for name, terms in config.get("replace_keywords", {}).items():
            words[name] = list(terms)
        for name, terms in config.get("keywords", {}).items():
            words[name].extend(terms)
    hits = {name: [term for term in terms if contains(text, term)] for name, terms in words.items()}
    hits = {name: terms for name, terms in hits.items() if terms}
    protected = [term for term in READ_ONLY if contains(text, term)]
    # Configuration cannot remove built-in read-only, capability or external-write guards.
    for name in ("review-only", "status-report", "cli-less-fallback", "shared-resource-lock"):
        guard_hits = [term for term in KEYWORDS[name] if contains(text, term)]
        if guard_hits:
            hits[name] = list(dict.fromkeys(hits.get(name, []) + guard_hits))
    if task:
        hits["task-join"] = ["explicit-or-inferred-task"]
    if protected:
        hits["review-only"] = list(dict.fromkeys(hits.get("review-only", []) + protected))
    scenario = next((name for name in PRIORITY if name in hits), "project-orientation")
    reasons = [f"matched {name}: {', '.join(terms)}" for name, terms in hits.items()]
    reasons.append(f"selected {scenario} by protected-first priority")
    # Distinguish task identity, modifiers and recover-before-write from independent commands.
    independent = set(hits) - {"task-join", "project-orientation", "isolated-exploration", "shared-resource-lock"}
    if "takeover" in independent:
        independent.discard("stalled-recovery")
    if protected:
        independent = {"review-only"}
    ambiguous = len(independent) > 1
    if "status-report" in hits and "shared-resource-lock" in hits and COMPOUND_SEQUENCE.search(text):
        ambiguous = True
    # Two mutating intents joined by a sequencing marker: order and lock scope are both
    # undetermined, so the shared-resource write would run without a decision on its lock.
    if "shared-resource-lock" in hits and len(hits.keys() & WRITE_INTENTS) > 1 and COMPOUND_SEQUENCE.search(text):
        ambiguous = True
    if "cli-less-fallback" in hits:
        scenario = "cli-less-fallback"
        ambiguous = False
    if ambiguous:
        scenario = "review-only" if "review-only" in hits else "status-report"
        reasons.append("independent intents conflict; read-only clarification required before writes")
    return {
        "scenario": scenario,
        "confidence": "low" if ambiguous or not hits else "high",
        "ambiguity": ambiguous,
        # "unknown" is a verdict, not a fallback. Without it a caller reading only `scenario`
        # cannot tell "this is an orientation request" from "no rule matched anything".
        "unknown": not hits,
        "requires_clarification": ambiguous or not hits,
        "matched_rules": hits,
        "decision_trace": reasons,
        "read_only": bool(protected) or scenario in READ_ONLY_SCENARIOS,
        # Distinct from `read_only`: that one is also true for the fallback scenario, so it
        # cannot answer "did the user explicitly forbid execution?". Only this one may veto.
        "read_only_explicit": bool(protected),
        "config_applied": config is not None,
    }


def classify_intent_with_proposal(intent: str, *, task: str = "", config: dict | None = None,
                                  propose=None) -> dict:
    """Deterministic verdict first; a proposal may only fill a gap, never widen the answer.

    `propose` is injected — this module imports no model and opens no socket, so the default
    path stays deterministic and testable. It is consulted on exactly one branch: when the
    rule engine matched nothing. That is the only place it has information the rules lack.

    Four vetoes survive, because a generator has no channel for them:
      1. `read_only_explicit` — the user wrote "do not upload". A model reading the noun
         misses the negation (measured: 4 of 8 such phrases misread), so we do not ask it.
      2. `ambiguity` — conflicting intents need a human, not a guess.
      3. unknown scenario names — a proposal outside KEYWORDS is discarded, not mapped.
      4. write proposals keep `requires_clarification` — a guessed write is confirmed once.
         Read-only proposals clear it, because getting those wrong costs a no-op.
    """
    verdict = classify_intent(intent, task=task, config=config)
    proposal = {"applied": False, "scenario": None, "reason": "not-consulted", "source": None}
    verdict["proposal"] = proposal
    if propose is None:
        return verdict
    # Ordered most-specific first: an audit trail that says "rules matched" when the real
    # reason was "the user wrote 不要" hides the mechanism we actually rely on.
    if verdict["read_only_explicit"]:
        proposal["reason"] = "vetoed-explicit-read-only"
        return verdict
    if not verdict["unknown"]:
        proposal["reason"] = "rules-matched"
        return verdict
    if verdict["ambiguity"]:
        proposal["reason"] = "vetoed-ambiguity"
        return verdict
    try:
        suggested = propose(intent)
    except Exception as exc:  # a broken proposer must not take routing down with it
        proposal["reason"] = f"proposer-failed:{exc.__class__.__name__}"
        return verdict
    if isinstance(suggested, dict):
        proposal["source"] = suggested.get("source")
        suggested = suggested.get("scenario")
    proposal["scenario"] = suggested
    if suggested is None:
        # Abstention is a real answer and the caller keeps the deterministic fallback.
        proposal["reason"] = "proposer-abstained"
        return verdict
    if not isinstance(suggested, str) or suggested not in KEYWORDS:
        proposal["reason"] = "unknown-scenario"
        return verdict
    proposal["applied"] = True
    proposal["reason"] = "filled-unknown"
    verdict["scenario"] = suggested
    # The gap is filled, so it is no longer unknown. Leaving this True made every applied
    # proposal still report "I don't know", which is the bug the eval caught.
    verdict["unknown"] = False
    verdict["read_only"] = suggested in READ_ONLY_SCENARIOS
    verdict["confidence"] = "low"
    verdict["decision_trace"] = list(verdict["decision_trace"]) + [
        f"no rule matched; proposal supplied {suggested}"]
    if suggested in WRITE_INTENTS:
        verdict["requires_clarification"] = True
        proposal["reason"] = "filled-unknown-write-needs-confirmation"
    else:
        verdict["requires_clarification"] = False
    return verdict
