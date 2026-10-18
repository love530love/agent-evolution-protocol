"""Explicit, audited cooperative/enforced workflow policy (not authentication)."""
from datetime import datetime, timezone
import json
from pathlib import Path

from .battle_orders import _atomic_json, _task_writer_lock

MODES = {"cooperative", "enforced"}


def read(root):
    path = Path(root) / "improvement_policy.json"
    if not path.exists():
        return {"mode": "cooperative", "history": [], "authorization_verified": False}
    row = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(row, dict) or row.get("mode") not in MODES or not isinstance(row.get("history"), list):
        raise ValueError("invalid improvement policy; inspect before executing")
    return {**row, "authorization_verified": False}


def configure(root, *, mode, actor, note):
    if mode not in MODES or not isinstance(actor, str) or not actor.strip() or not isinstance(note, str) or not note.strip():
        raise ValueError("mode, actor and decision note required")
    with _task_writer_lock(Path(root), "improvement-policy"):
        current = read(root)
        record = {"at": datetime.now(timezone.utc).isoformat(), "actor": actor,
                  "from": current["mode"], "to": mode, "note": note}
        _atomic_json(Path(root) / "improvement_policy.json", {"mode": mode, "history": current["history"] + [record]})
    return read(root)
