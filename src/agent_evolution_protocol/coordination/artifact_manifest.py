"""Reproducible artifact manifest and final delivery verification."""
from __future__ import annotations

import hashlib
from pathlib import Path


def sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build(paths: list[str | Path], *, root: str | Path = "") -> dict:
    base = Path(root).resolve() if root else None
    items = []
    for raw in paths:
        p = Path(raw).resolve()
        if not p.is_file():
            items.append({"path": str(p), "present": False})
            continue
        items.append({"path": str(p), "present": True, "bytes": p.stat().st_size,
                      "sha256": sha256(p),
                      "relative": str(p.relative_to(base)) if base else str(p)})
    return {"artifacts": items, "complete": all(x["present"] for x in items)}


def verify(manifest: dict) -> dict:
    failures = []
    for item in manifest.get("artifacts", []):
        p = Path(item["path"])
        if not p.is_file():
            failures.append({"path": str(p), "reason": "missing"}); continue
        actual = sha256(p)
        if actual != item.get("sha256"):
            failures.append({"path": str(p), "reason": "hash_mismatch", "actual": actual})
    return {"ok": not failures and bool(manifest.get("artifacts")), "failures": failures,
            "checked": len(manifest.get("artifacts", []))}
