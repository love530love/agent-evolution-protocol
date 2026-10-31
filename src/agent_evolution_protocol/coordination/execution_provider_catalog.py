"""Read-only catalog for optional third-party execution providers.

The catalog is deliberately separate from live provider registration.  A row
only describes a candidate integration and never launches code, downloads a
repository, or grants trust.  Promotion is evidence-driven: catalog -> probe
-> session acceptance -> task-tested.

Promotion is a gate, not an edit.  Every row in the shipped example sits at
``candidate``, which is honest but also useless on its own: a status field that
anyone can retype is a claim, not a state.  ``promote`` therefore refuses to
advance a row unless it is handed the evidence that the *next* step requires,
one step at a time, and it writes an append-only journal next to the catalog so
"how did this get to task-tested" stays answerable.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CATALOG_SCHEMA = "aep-execution-provider-catalog-v1"
STATUSES = {"candidate", "probed", "session-accepted", "task-tested", "retired"}
# Promotion order.  ``retired`` is reachable from anywhere and from nowhere.
STATUS_ORDER = ("candidate", "probed", "session-accepted", "task-tested")
LAYERS = {"browser", "desktop_visual", "desktop_windows", "terminal_execution",
          "code_engineering", "desktop_replay", "mobile", "reverse_engineering"}
INTEGRATIONS = {"skill+connector", "connector", "app+connector", "mcp+connector",
                "mcp", "cli+connector", "vision-service"}

# What each step has to show before it is allowed to happen.  These are the
# machine-checkable half of a row's ``acceptance`` prose.
EVIDENCE = {
    "probed": ("probe_command", "probe_at", "probe_result"),
    "session-accepted": ("session_event_id", "session_receipt", "session_at"),
    "task-tested": ("task_id", "task_receipt", "task_outcome"),
}
# Steps whose evidence has to be a pass.  A failed probe is not a promotion:
# it is either "still a candidate" or "retired".
MUST_PASS = {"probed": "probe_result", "task-tested": "task_outcome"}


def _catalog_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    return Path(__file__).resolve().parents[3] / "examples" / "execution-provider-catalog.json"


def validate_catalog(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema", "purpose", "provenance_policy", "providers"}:
        raise ValueError("invalid execution provider catalog fields")
    if value["schema"] != CATALOG_SCHEMA or not isinstance(value["purpose"], str) or not value["purpose"].strip():
        raise ValueError("invalid execution provider catalog header")
    policy = value["provenance_policy"]
    if not isinstance(policy, dict) or set(policy) != {"default_license_status", "default_distribution_mode", "notice_policy"}:
        raise ValueError("invalid execution provider provenance policy")
    if policy["default_license_status"] != "unverified" or policy["default_distribution_mode"] != "reference-only":
        raise ValueError("unverified providers must remain reference-only")
    if not isinstance(policy["notice_policy"], str) or not policy["notice_policy"].strip():
        raise ValueError("provenance notice policy is required")
    providers = value["providers"]
    if not isinstance(providers, list) or not providers:
        raise ValueError("providers must be a non-empty list")
    ids: set[str] = set()
    normalized: list[dict[str, Any]] = []
    required = {"id", "repository", "layer", "integration", "runtime", "capabilities", "strengths", "limitations", "acceptance", "status"}
    optional = {"source_note", "license_note"}
    for row in providers:
        if not isinstance(row, dict) or not required.issubset(row):
            raise ValueError("provider row is missing required fields")
        # The schema says additionalProperties: false; without this check the
        # two disagreed and the shipped catalog carried fields no validator
        # had ever seen.
        unexpected = set(row) - required - optional
        if unexpected:
            raise ValueError(f"provider row has undeclared fields: {', '.join(sorted(unexpected))}")
        provider_id = row["id"]
        if not isinstance(provider_id, str) or not provider_id or provider_id in ids:
            raise ValueError("provider ids must be unique non-empty strings")
        if not isinstance(row["repository"], str) or not row["repository"].startswith("https://github.com/"):
            raise ValueError("provider.repository must be a GitHub HTTPS URL")
        if row["status"] not in STATUSES:
            raise ValueError("invalid provider status")
        # The JSON schema declares these enums; enforcing them here too is the
        # point.  When only the schema knew, the shipped catalog carried
        # ``integration: vision-service`` for a year while every Python caller
        # accepted it -- two standards, and the weaker one was load-bearing.
        if row["layer"] not in LAYERS:
            raise ValueError(f"invalid provider layer: {row['layer']}")
        if row["integration"] not in INTEGRATIONS:
            raise ValueError(f"invalid provider integration: {row['integration']}")
        for key in ("runtime", "capabilities", "strengths", "limitations", "acceptance"):
            if not isinstance(row[key], list) or not row[key] or any(not isinstance(item, str) or not item.strip() for item in row[key]):
                raise ValueError(f"provider.{key} must be a non-empty string list")
        ids.add(provider_id)
        normalized.append(dict(row))
    return {"schema": CATALOG_SCHEMA, "purpose": value["purpose"],
            "provenance_policy": dict(policy), "providers": normalized}


def load(path: str | Path | None = None) -> dict[str, Any]:
    catalog_path = _catalog_path(path)
    return validate_catalog(json.loads(catalog_path.read_text(encoding="utf-8")))


def inventory(path: str | Path | None = None, *, layer: str = "", status: str = "") -> list[dict[str, Any]]:
    """Return deterministic, read-only provider rows for a planner or UI."""
    if status and status not in STATUSES:
        raise ValueError("invalid provider status")
    rows = load(path)["providers"]
    return [row for row in rows if (not layer or row["layer"] == layer) and (not status or row["status"] == status)]


def journal_path(path: str | Path | None = None) -> Path:
    """Where promotion history lives: beside the catalog, append-only."""
    return _catalog_path(path).parent / "provider_promotions.jsonl"


def _append_journal(target: Path, entry: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
    with target.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def _write_catalog(target: Path, value: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def _stamp(now: str | None) -> str:
    return now or datetime.now(timezone.utc).isoformat()


def promote(path: str | Path | None, provider_id: str, *, to: str,
            evidence: dict[str, Any], now: str | None = None) -> dict[str, Any]:
    """Advance one row exactly one step, and only with that step's evidence.

    Three refusals matter and each one is a defect this project has already
    paid for elsewhere:

    - **No skipping.** A row cannot jump from candidate to session-accepted;
      the intermediate probe is what makes the next claim meaningful.
    - **No unearned step.** Every key in ``EVIDENCE[to]`` must be present and
      non-empty, so a status is a statement about a measurement rather than
      about someone's optimism.
    - **No promotion on a failed measurement.** ``probe_result`` and
      ``task_outcome`` must be ``pass``.  A probe that failed is a finding, not
      a promotion: keep the row a candidate or retire it.
    """
    target = _catalog_path(path)
    value = load(target)
    rows = value["providers"]
    row = next((item for item in rows if item["id"] == provider_id), None)
    if row is None:
        raise ValueError(f"unknown provider: {provider_id}")
    if to not in STATUS_ORDER:
        raise ValueError(f"cannot promote to {to!r}; use retire() to stop a provider")
    current = row["status"]
    if current == "retired":
        raise ValueError(f"{provider_id} is retired; re-add it as a candidate instead of promoting it")
    expected = STATUS_ORDER[STATUS_ORDER.index(current) + 1]
    if to != expected:
        raise ValueError(f"{provider_id} is {current}; the only promotion from here is {expected}")
    evidence = dict(evidence or {})
    missing = [key for key in EVIDENCE[to] if not str(evidence.get(key, "")).strip()]
    if missing:
        raise ValueError(f"promotion to {to} needs evidence: {', '.join(missing)}")
    gate = MUST_PASS.get(to)
    if gate and str(evidence[gate]).strip().lower() != "pass":
        raise ValueError(f"{gate} is {evidence[gate]!r}; a step that did not pass is not a promotion")
    entry = {"at": _stamp(now), "provider": provider_id, "from": current, "to": to,
             "evidence": evidence}
    row["status"] = to
    _write_catalog(target, validate_catalog(value))
    _append_journal(journal_path(target), entry)
    return {"provider": provider_id, "from": current, "to": to,
            "journal": str(journal_path(target)), "evidence": evidence}


def retire(path: str | Path | None, provider_id: str, *, reason: str,
           now: str | None = None) -> dict[str, Any]:
    """Stop a provider from any live status. Retirement is always allowed.

    Removing a candidate on evidence is the whole point of keeping a catalog;
    a registry where nothing is ever dropped is a wish list that only grows.
    """
    if not str(reason or "").strip():
        raise ValueError("retirement needs a reason")
    target = _catalog_path(path)
    value = load(target)
    row = next((item for item in value["providers"] if item["id"] == provider_id), None)
    if row is None:
        raise ValueError(f"unknown provider: {provider_id}")
    current = row["status"]
    if current == "retired":
        return {"provider": provider_id, "from": current, "to": "retired", "unchanged": True}
    entry = {"at": _stamp(now), "provider": provider_id, "from": current,
             "to": "retired", "reason": reason}
    row["status"] = "retired"
    _write_catalog(target, validate_catalog(value))
    _append_journal(journal_path(target), entry)
    return {"provider": provider_id, "from": current, "to": "retired", "reason": reason,
            "journal": str(journal_path(target))}


def history(path: str | Path | None = None, provider_id: str = "") -> list[dict[str, Any]]:
    """Read the promotion journal, newest last. Missing journal means empty."""
    target = journal_path(path)
    if not target.is_file():
        return []
    rows = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        if provider_id and entry.get("provider") != provider_id:
            continue
        rows.append(entry)
    return rows
