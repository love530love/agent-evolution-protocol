from pathlib import Path
from agent_evolution_protocol.coordination.artifact_manifest import build, verify


def test_manifest_verifies_and_detects_changes(tmp_path: Path):
    p = tmp_path / "result.txt"; p.write_text("ok", encoding="utf-8")
    m = build([p], root=tmp_path)
    assert verify(m)["ok"]
    p.write_text("changed", encoding="utf-8")
    assert not verify(m)["ok"]


def test_missing_artifact_is_incomplete(tmp_path: Path):
    m = build([tmp_path / "missing.bin"])
    assert not m["complete"]
    assert not verify(m)["ok"]
