import json
from pathlib import Path

import pytest

from agent_evolution_protocol.coordination import execution_provider_catalog


ROOT = Path(__file__).parents[1]


def test_catalog_is_valid_and_contains_all_execution_layers():
    catalog = execution_provider_catalog.load(ROOT / "examples" / "execution-provider-catalog.json")
    layers = {row["layer"] for row in catalog["providers"]}
    assert {"browser", "desktop_visual", "terminal_execution", "code_engineering", "mobile", "reverse_engineering"} <= layers
    assert all(row["status"] == "candidate" for row in catalog["providers"])


def test_catalog_inventory_is_read_only_and_filterable():
    path = ROOT / "examples" / "execution-provider-catalog.json"
    rows = execution_provider_catalog.inventory(path, layer="browser")
    assert {row["id"] for row in rows} >= {"browser_use", "skyvern", "lavague"}
    with pytest.raises(ValueError, match="invalid provider status"):
        execution_provider_catalog.inventory(path, status="trusted")


def test_catalog_rejects_duplicate_provider_ids(tmp_path):
    data = json.loads((ROOT / "examples" / "execution-provider-catalog.json").read_text(encoding="utf-8"))
    data["providers"].append(dict(data["providers"][0]))
    with pytest.raises(ValueError, match="unique"):
        execution_provider_catalog.validate_catalog(data)
