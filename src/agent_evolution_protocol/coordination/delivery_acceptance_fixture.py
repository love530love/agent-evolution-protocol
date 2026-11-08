"""Offline, recomputable acceptance fixtures for desktop delivery receipts.

Four real-machine cases (2026-11-05 / 2026-11-06 / 2026-11-08) are frozen
here as SYNTHETIC, self-contained data so CI can recompute them without any
gitignored runtime file. This module never reads a window, starts an adapter,
opens a socket, writes state, or authenticates anywhere.

Two ideas are borrowed from upstream acceptance practice and independently
implemented here (no source copied, no upstream runtime referenced):

1. A round that declares ``requiredEvidence`` **cannot pass on prose alone**.
   A missing artifact marks the round ``uncertain`` and holds the delivery.
   ``task_delivered=true`` and a ``status="message-visible"`` string are not
   artifacts.
2. A bounded experiment has six named parts -- experiment / effect / safety /
   oracle / receipt / result -- so a reviewer can tell what was injected,
   what was expected, what was actually measured, and what must never happen.

All verdicts come from the project's existing contract:
``action_verification.from_send_report`` / ``verdict``,
``delivery_preflight.postflight`` (advisory second opinion), and
``uia_transcript_verdict.evaluate`` (independent UIA transcript source).
Nothing here replaces or relaxes them; the primary verdict is reported
exactly as computed, even when the second source disagrees.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import hashlib
import json
from typing import Any

from . import action_verification, delivery_preflight, uia_transcript_verdict

SCHEMA = "aep-delivery-acceptance-fixture-v1"
SECTIONS = ("experiment", "effect", "safety", "oracle", "receipt", "result")

NOTE = ("Read-only offline acceptance fixtures. No adapter, no network, no "
        "process, no model, no login, no write, no UI input. The ideas are "
        "independently implemented; no upstream source is copied or linked.")

#: Evidence names a fixture may require. Each is a fact that can be checked
#: from the frozen data, never a claim made by a receipt's own text.
EVIDENCE_NAMES = ("receipt", "check_fields", "uia_transcript",
                  "event_id_in_transcript", "composer_hint", "probe")

EVENT_A = "20261106T065238959490Z-9fd49a50"   # 2026-11-06 first ChatGPT compose
EVENT_B = "20261106T074149902108Z-4ec5697b"   # 2026-11-06 second ChatGPT compose
EVENT_C = "20261106T074613464456Z-71409ad5"   # 2026-11-06 workbuddy3 compose
HINT = "和 Codex 一起工作"


def _transcript_delivered(event_id: str, probe: str = "请回表讨论") -> str:
    """Shape of a real read-only UIA capture: id above the composer boundary."""
    return "\n".join([
        "历史会话",
        "请查看队友通报并讨论",
        f"【协作区讨论邀请】事件ID：{event_id}",
        f"这条消息是真实桌面投递，不是信箱落盘。{probe}",
        "ChatGPT 说：我会按事件 ID 做一次只读审计",
        "最新一条回复",
        f"{HINT} | {HINT}",
        "完全访问 | 完全访问",
        "暂无聊天 | 暂无聊天",
    ])


def _transcript_uncertain() -> str:
    """OCR capture with a partial probe but no event id and no placeholder."""
    return "\n".join([
        "协作区回表邀请",
        "逆向分析工程师已完成 4m8s",
        "查看通报",
        "部分正文被 OCR 替换成同音字，事件 ID 未被识别",
    ])


@dataclass(frozen=True)
class Fixture:
    name: str
    source: str
    effect: str
    description: str
    receipt: dict[str, Any]
    transcript: str | None
    event_id: str
    probe: str
    composer_hint: str
    required_evidence: tuple[str, ...]
    oracle: dict[str, Any]

    def with_transcript(self, value: str | None) -> "Fixture":
        return replace(self, transcript=value)

    def with_receipt(self, value: dict[str, Any]) -> "Fixture":
        return replace(self, receipt=value)

    def with_oracle(self, value: dict[str, Any]) -> "Fixture":
        return replace(self, oracle=value)

    def canonical(self) -> str:
        return json.dumps({
            "name": self.name, "source": self.source, "effect": self.effect,
            "description": self.description, "receipt": self.receipt,
            "transcript": self.transcript, "event_id": self.event_id,
            "probe": self.probe, "composer_hint": self.composer_hint,
            "required_evidence": list(self.required_evidence),
            "oracle": self.oracle,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()


FIXTURES: tuple[Fixture, ...] = (
    Fixture(
        name="ocr_band_false_negative",
        source="2026-11-06 ChatGPT compose #1 (event 20261106T065238959490Z-9fd49a50)",
        effect="OCR band split on a multi-monitor window rect [960,429,2880,1659]",
        description=("Adapter reported task_delivered=false with all four OCR "
                     "checks failing, while a read-only UIA capture held the "
                     "event id and the agent had begun a read-only audit."),
        receipt={"status": "submitted-unconfirmed", "task_delivered": False,
                 "probe_in_composer_before_send": False,
                 "probe_in_transcript": False,
                 "probe_in_composer_after": False,
                 "placeholder_gone": False, "composer_cleared": True,
                 "agent_accepted": False},
        transcript=_transcript_delivered(EVENT_A),
        event_id=EVENT_A, probe="请回表讨论", composer_hint=HINT,
        required_evidence=("receipt", "check_fields", "uia_transcript",
                           "event_id_in_transcript", "composer_hint", "probe"),
        oracle={"primary_verified": False, "second_verified": True,
                "conflict": True, "outcome": "pass"},
    ),
    Fixture(
        name="boolean_false_positive",
        source="2026-11-05 qoder interject receipt AEP-INTERJECT-20261105-04-qoder.json",
        effect="Receipt declared task_delivered=true without any check field",
        description=("SEND_REPORT_CHECKS were entirely absent (only "
                     "composer_cleared / event_witness of different names). "
                     "No UIA transcript was captured, so the round must hold."),
        receipt={"status": "message-visible", "task_delivered": True,
                 "composer_cleared": True, "event_witness": "unmapped-field"},
        transcript=None,
        event_id="AEP-INTERJECT-20261105-04", probe="", composer_hint="",
        required_evidence=("receipt", "check_fields", "uia_transcript",
                           "event_id_in_transcript"),
        oracle={"primary_verified": None, "second_verified": None,
                "conflict": False, "outcome": "uncertain"},
    ),
    Fixture(
        name="primary_kept_under_conflict",
        source="2026-11-06 ChatGPT compose #2 (event 20261106T074149902108Z-4ec5697b)",
        effect="placeholder_gone=false while the UIA transcript proves delivery",
        description=("First end-to-end success of the exposed compose command. "
                     "Three OCR checks passed and one failed; the independent "
                     "UIA source said delivered. The primary verdict must stay "
                     "false -- a second source never relaxes the contract."),
        receipt={"status": "message-visible", "task_delivered": True,
                 "probe_in_composer_before_send": True,
                 "probe_in_transcript": True,
                 "probe_in_composer_after": False,
                 "placeholder_gone": False, "composer_cleared": True,
                 "agent_accepted": False},
        transcript=_transcript_delivered(EVENT_B, "请尽快回表结论"),
        event_id=EVENT_B, probe="请尽快回表结论", composer_hint=HINT,
        required_evidence=("receipt", "check_fields", "uia_transcript",
                           "event_id_in_transcript", "composer_hint", "probe"),
        oracle={"primary_verified": False, "second_verified": True,
                "conflict": True, "outcome": "pass"},
    ),
    Fixture(
        name="submitted_unconfirmed",
        source="2026-11-06 workbuddy3 compose (event 20261106T074613464456Z-71409ad5)",
        effect="composer_cleared=true but OCR read-back lost the event id and hint",
        description=("The hint was hit exactly, yet the receipt was "
                     "submitted-unconfirmed and the post-send OCR capture had "
                     "no event id and no composer placeholder. The second "
                     "source must answer unknown, never a guess; the uncertain "
                     "result must never be replayed."),
        receipt={"status": "submitted-unconfirmed", "task_delivered": False,
                 "probe_in_composer_before_send": False,
                 "probe_in_transcript": False,
                 "probe_in_composer_after": False,
                 "placeholder_gone": True, "composer_cleared": True},
        transcript=_transcript_uncertain(),
        event_id=EVENT_C, probe="请尽快回表结论",
        composer_hint="今天帮你做些什么",
        required_evidence=("receipt", "check_fields", "uia_transcript",
                           "composer_hint", "probe"),
        oracle={"primary_verified": False, "second_verified": None,
                "conflict": True, "outcome": "pass"},
    ),
)


def _collect_evidence(fixture: Fixture) -> dict[str, bool]:
    """Facts, not claims. A receipt's own prose never counts as evidence."""
    receipt = fixture.receipt or {}
    transcript = fixture.transcript or ""
    has_check = any(isinstance(receipt.get(field), bool)
                    for field in action_verification.SEND_REPORT_CHECKS)
    return {
        "receipt": bool(receipt) and isinstance(receipt, dict),
        "check_fields": has_check,
        "uia_transcript": bool(transcript.strip()),
        "event_id_in_transcript": bool(fixture.event_id
                                       and fixture.event_id in transcript),
        "composer_hint": bool(fixture.composer_hint.strip()),
        "probe": bool(fixture.probe.strip()),
    }


def evaluate(fixture: Fixture) -> dict[str, Any]:
    """Recompute one frozen case and report the six contract sections."""
    evidence = _collect_evidence(fixture)
    missing = [name for name in fixture.required_evidence
               if not evidence.get(name, False)]
    unknown_names = [name for name in fixture.required_evidence
                     if name not in EVIDENCE_NAMES]

    post = delivery_preflight.postflight(
        fixture.receipt, transcript=fixture.transcript,
        event_id=fixture.event_id, probe=fixture.probe,
        composer_hint=fixture.composer_hint)
    second = post.get("transcript_verdict")
    actual = {
        "primary_verified": post["verified"],
        "second_verified": second["verified"] if second else None,
        "conflict": post["sources_in_conflict"],
    }
    expected_fields = {key: value for key, value in fixture.oracle.items()
                       if key != "outcome"}
    verdict_match = all(actual.get(key) == expected
                        for key, expected in expected_fields.items())

    if missing or unknown_names:
        outcome = "uncertain"
        reason = "MISSING_REQUIRED_EVIDENCE:" + ",".join(missing + unknown_names)
    elif not verdict_match:
        outcome = "fail"
        mismatched = [key for key, expected in expected_fields.items()
                      if actual.get(key) != expected]
        reason = "ORACLE_MISMATCH:" + ",".join(mismatched)
    else:
        outcome = "pass"
        reason = ""

    # A correctly HELD round (uncertain as declared by the oracle) is a
    # successful acceptance result, but it is not an accepted delivery.
    matched = bool(verdict_match
                   and outcome == fixture.oracle.get("outcome", "pass"))

    return {
        "schema": SCHEMA,
        "experiment": {"name": fixture.name, "source": fixture.source,
                       "description": fixture.description},
        "effect": {"injected": fixture.effect,
                   "note": "synthetic frozen data; no live window was touched"},
        "safety": {"no_adapter": True, "no_network": True, "no_process": True,
                   "no_write": True, "no_login": True, "no_model": True,
                   "no_ui_input": True,
                   "note": "pure recomputation over in-memory frozen data"},
        "oracle": {**fixture.oracle,
                   "note": "declared before recomputation; never derived from it"},
        "receipt": {"evidence": evidence, "missing_evidence": missing,
                    "fixture_sha256": fixture.sha256(),
                    "postflight": post,
                    "verdict_source": "uia_transcript_verdict.evaluate",
                    "note": "primary verdict reported unchanged, advisory_only preserved"},
        "result": {"outcome": outcome, "matched": bool(matched),
                   "reason": reason, "actual": actual,
                   "note": ("uncertain holds the round; only pass counts as "
                            "an accepted acceptance result")},
    }


def run_all() -> dict[str, Any]:
    rows = [evaluate(fixture) for fixture in FIXTURES]
    passed = sum(1 for row in rows if row["result"]["matched"]
                 and row["result"]["outcome"] == "pass")
    held = sum(1 for row in rows if row["result"]["matched"]
               and row["result"]["outcome"] == "uncertain")
    failed = sum(1 for row in rows if not row["result"]["matched"]
                 or row["result"]["outcome"] == "fail")
    uncertain = sum(1 for row in rows
                    if row["result"]["outcome"] == "uncertain")
    return {"schema": SCHEMA, "cases": len(rows), "passed": passed,
            "held": held, "failed": failed, "uncertain": uncertain,
            "results": rows,
            "models_loaded": 0, "processes_started": 0,
            "ui_input_performed": False, "deployment_performed": False,
            "system_tasks_registered": False,
            "external_login_performed": False,
            "note": NOTE}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only offline delivery-receipt acceptance fixtures")
    parser.add_argument("--json", action="store_true",
                        help="print the full recomputation report as JSON")
    parser.add_argument("--list", action="store_true",
                        help="list frozen case names and sources")
    args = parser.parse_args(argv)
    report = run_all()
    if args.list:
        payload = {"schema": SCHEMA,
                   "cases": [{"name": f.name, "source": f.source,
                              "sha256": f.sha256()} for f in FIXTURES]}
    else:
        payload = report
        if not args.json:
            payload = {"schema": SCHEMA, "cases": report["cases"],
                       "passed": report["passed"], "held": report["held"],
                       "failed": report["failed"],
                       "uncertain": report["uncertain"],
                       "outcomes": {r["experiment"]["name"]:
                                    r["result"]["outcome"]
                                    for r in report["results"]}}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
