"""Schema assets are JSON and carry the mandatory federation identity fields."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCHEMA_DIR = ROOT / "schemas"


def test_federation_schema_assets_are_valid_json_and_have_ids():
    paths = sorted(SCHEMA_DIR.glob("*.schema.json"))
    assert {p.name for p in paths} == {
        "event-envelope-v1.schema.json",
        "handoff-packet-v1.schema.json",
        "task-envelope-v2.schema.json",
        "workspace-snapshot-v1.schema.json",
    }
    ids = []
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["$schema"].startswith("https://json-schema.org/")
        ids.append(data["$id"])
        assert data["type"] == "object"
        assert data["required"]
    assert len(ids) == len(set(ids))


def test_workspace_snapshot_separates_anchored_state_and_artifacts():
    schema = json.loads((SCHEMA_DIR / "workspace-snapshot-v1.schema.json").read_text(encoding="utf-8"))
    assert set(schema["required"]) >= {"workspace_id", "anchored_request", "dag", "intelligence", "artifacts"}
