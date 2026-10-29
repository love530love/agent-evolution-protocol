from __future__ import annotations

import pytest

from agent_evolution_protocol.coordination.workspace_identity import (
    ensure_workspace_id,
    new_workspace_id,
    validate_workspace_id,
    watermark,
)


def test_workspace_id_is_stable_when_record_is_reloaded():
    first = ensure_workspace_id({"goal": "x"})
    second = ensure_workspace_id(first)
    assert first["workspace_id"] == second["workspace_id"]
    assert validate_workspace_id(first["workspace_id"]) == first["workspace_id"]


def test_watermark_rejects_cross_workspace_payload():
    workspace_id = new_workspace_id()
    assert watermark({"event": "wake"}, workspace_id)["workspace_id"] == workspace_id
    with pytest.raises(ValueError, match="WORKSPACE_MISMATCH"):
        watermark({"workspace_id": new_workspace_id()}, workspace_id)
