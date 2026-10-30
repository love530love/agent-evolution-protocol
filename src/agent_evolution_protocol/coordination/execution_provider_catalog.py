"""Read-only catalog for optional third-party execution providers.

The catalog is deliberately separate from live provider registration.  A row
only describes a candidate integration and never launches code, downloads a
repository, or grants trust.  Promotion is evidence-driven: catalog -> probe
-> session acceptance -> task-tested.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CATALOG_SCHEMA = "aep-execution-provider-catalog-v1"
STATUSES = {"candidate", "probed", "session-accepted", "task-tested", "retired"}


def _catalog_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    return Path(__file__).resolve().parents[3] / "examples" / "execution-provider-catalog.json"


def validate_catalog(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema", "purpose", "providers"}:
        raise ValueError("invalid execution provider catalog fields")
    if value["schema"] != CATALOG_SCHEMA or not isinstance(value["purpose"], str) or not value["purpose"].strip():
        raise ValueError("invalid execution provider catalog header")
    providers = value["providers"]
    if not isinstance(providers, list) or not providers:
        raise ValueError("providers must be a non-empty list")
    ids: set[str] = set()
    normalized: list[dict[str, Any]] = []
    required = {"id", "repository", "layer", "integration", "runtime", "capabilities", "strengths", "limitations", "acceptance", "status"}
    for row in providers:
        if not isinstance(row, dict) or not required.issubset(row):
            raise ValueError("provider row is missing required fields")
        provider_id = row["id"]
        if not isinstance(provider_id, str) or not provider_id or provider_id in ids:
            raise ValueError("provider ids must be unique non-empty strings")
        if not isinstance(row["repository"], str) or not row["repository"].startswith("https://github.com/"):
            raise ValueError("provider.repository must be a GitHub HTTPS URL")
        if row["status"] not in STATUSES:
            raise ValueError("invalid provider status")
        for key in ("runtime", "capabilities", "strengths", "limitations", "acceptance"):
            if not isinstance(row[key], list) or not row[key] or any(not isinstance(item, str) or not item.strip() for item in row[key]):
                raise ValueError(f"provider.{key} must be a non-empty string list")
        ids.add(provider_id)
        normalized.append(dict(row))
    return {"schema": CATALOG_SCHEMA, "purpose": value["purpose"], "providers": normalized}


def load(path: str | Path | None = None) -> dict[str, Any]:
    catalog_path = _catalog_path(path)
    return validate_catalog(json.loads(catalog_path.read_text(encoding="utf-8")))


def inventory(path: str | Path | None = None, *, layer: str = "", status: str = "") -> list[dict[str, Any]]:
    """Return deterministic, read-only provider rows for a planner or UI."""
    if status and status not in STATUSES:
        raise ValueError("invalid provider status")
    rows = load(path)["providers"]
    return [row for row in rows if (not layer or row["layer"] == layer) and (not status or row["status"] == status)]
