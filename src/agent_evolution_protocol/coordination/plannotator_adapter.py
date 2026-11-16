"""Portable Plannotator review packages for AEP plans and evidence.

The adapter intentionally has no dependency on the Plannotator binary.  It
exports ordinary Markdown plus a machine-readable manifest that can be opened
by Plannotator, a browser, or another review client.  Imported decisions are
immutable AEP records; they do *not* alter an order, create a task, or invoke a
desktop executor.  A scheduler must separately interpret an approved decision.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

from .foundation import read_json, write_json_atomic, write_text_atomic
from .workspace_identity import validate_workspace_id


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
DECISIONS = {"approve", "revise", "hold"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(name: str, value: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ValueError(f"invalid {name}")
    return value


def _root(root: Path) -> Path:
    path = Path(root) / "plannotator"
    (path / "exports").mkdir(parents=True, exist_ok=True)
    (path / "decisions").mkdir(parents=True, exist_ok=True)
    return path


def _key(workspace_id: str, event_id: str) -> str:
    return hashlib.sha256(f"{workspace_id}:{event_id}".encode("utf-8")).hexdigest()[:24]


def _frontmatter(value: dict[str, Any]) -> str:
    # JSON values are valid YAML scalars/arrays and keep arbitrary Chinese text
    # round-trippable without inventing a YAML dependency in AEP.
    return "---\n" + "\n".join(f"{key}: {json.dumps(item, ensure_ascii=False)}"
                                 for key, item in value.items()) + "\n---\n"


def export_review(root: Path, *, workspace_id: str, event_id: str, title: str,
                  plan_markdown: str, artifacts: list[dict[str, Any]] | None = None,
                  source: str = "aep") -> dict[str, Any]:
    """Export one durable, workspace-watermarked review package.

    The export is idempotent only when its content hash is identical.  Reusing
    an event ID to silently replace a reviewed plan raises ``EXPORT_CONFLICT``;
    callers must mint a revision event instead.
    """
    validate_workspace_id(workspace_id)
    _id("event_id", event_id)
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title is required")
    if not isinstance(plan_markdown, str) or not plan_markdown.strip():
        raise ValueError("plan_markdown is required")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source is required")
    entries = []
    for artifact in artifacts or []:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("id"), str):
            raise ValueError("artifacts require an id")
        entries.append({key: artifact[key] for key in ("id", "path", "sha256", "kind") if key in artifact})
    identity = {"workspace_id": workspace_id, "event_id": event_id, "title": title.strip(),
                "source": source.strip(), "artifacts": entries}
    digest = hashlib.sha256(json.dumps({**identity, "plan_markdown": plan_markdown},
                                       ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    base = _root(root) / "exports" / _key(workspace_id, event_id)
    manifest_path, markdown_path = base.with_suffix(".json"), base.with_suffix(".md")
    existing = read_json(manifest_path)
    if existing is not None:
        if existing.get("content_sha256") != digest:
            raise ValueError("EXPORT_CONFLICT: event already names different review content")
        return {"status": "DUPLICATE", "manifest": str(manifest_path), "markdown": str(markdown_path),
                "event_id": event_id, "execution_started": False}
    manifest = {"schema": "aep-plannotator-review-v1", **identity, "created_at": _now(),
                "content_sha256": digest, "execution_started": False,
                "note": "Review package only. Approval does not dispatch or deliver an AEP order."}
    markdown = _frontmatter({"aep_workspace_id": workspace_id, "aep_event_id": event_id,
                             "title": title.strip(), "source": source.strip(),
                             "content_sha256": digest}) + "\n# " + title.strip() + "\n\n" + plan_markdown.rstrip() + "\n"
    write_json_atomic(manifest_path, manifest, indent=2)
    write_text_atomic(markdown_path, markdown)
    return {"status": "EXPORTED", "manifest": str(manifest_path), "markdown": str(markdown_path),
            "event_id": event_id, "execution_started": False}


def record_decision(root: Path, *, workspace_id: str, event_id: str, decision_id: str,
                    decision: str, reviewer: str, summary: str,
                    annotations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Record one non-destructive review decision against an exported package."""
    validate_workspace_id(workspace_id)
    _id("event_id", event_id)
    _id("decision_id", decision_id)
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {sorted(DECISIONS)}")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("reviewer is required")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("summary is required")
    exported = _root(root) / "exports" / _key(workspace_id, event_id)
    manifest = read_json(exported.with_suffix(".json"))
    if not isinstance(manifest, dict):
        raise ValueError("REVIEW_EXPORT_REQUIRED")
    path = _root(root) / "decisions" / f"{_key(workspace_id, event_id)}-{decision_id}.json"
    record = {"schema": "aep-plannotator-decision-v1", "workspace_id": workspace_id,
              "event_id": event_id, "decision_id": decision_id, "decision": decision,
              "reviewer": reviewer.strip(), "summary": summary.strip(),
              "annotations": list(annotations or []), "review_content_sha256": manifest["content_sha256"],
              "recorded_at": _now(), "dispatch_performed": False, "delivery_claim": "NONE"}
    previous = read_json(path)
    stable = {key: record[key] for key in ("workspace_id", "event_id", "decision_id", "decision", "reviewer", "summary", "annotations", "review_content_sha256")}
    if previous is not None:
        prior = {key: previous.get(key) for key in stable}
        if prior != stable:
            raise ValueError("DECISION_CONFLICT")
        return {"status": "DUPLICATE", "decision": previous, "execution_started": False}
    write_json_atomic(path, record, indent=2)
    return {"status": "RECORDED", "decision": record, "execution_started": False}


def main(argv: list[str] | None = None) -> int:
    # A Windows task or redirected PowerShell session can inherit cp1252 even
    # though AEP's persisted review records are UTF-8.  Printing the receipt
    # must not turn a successfully recorded Chinese decision into a CLI crash.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("export", "decision"))
    parser.add_argument("--coord-root", type=Path, required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--title", default="")
    parser.add_argument("--plan-file", type=Path)
    parser.add_argument("--decision-id", default="")
    parser.add_argument("--decision", choices=sorted(DECISIONS))
    parser.add_argument("--reviewer", default="")
    parser.add_argument("--summary", default="")
    args = parser.parse_args(argv)
    if args.action == "export":
        if args.plan_file is None:
            parser.error("--plan-file is required for export")
        result = export_review(args.coord_root, workspace_id=args.workspace_id, event_id=args.event_id,
                               title=args.title, plan_markdown=args.plan_file.read_text(encoding="utf-8"))
    else:
        result = record_decision(args.coord_root, workspace_id=args.workspace_id, event_id=args.event_id,
                                 decision_id=args.decision_id, decision=args.decision,
                                 reviewer=args.reviewer, summary=args.summary)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
