"""Explicit, local binding between project directories and existing workspaces.

Binding is metadata, not proof of membership, identity or session delivery.
The federation state remains authoritative for the workspace's existence.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import time
from datetime import datetime, timezone

from .workspace_identity import validate_workspace_id
from ..federation import engine

DB_NAME = "workspace_bindings.sqlite3"
_SCHEMA = "CREATE TABLE IF NOT EXISTS bindings (workspace_id TEXT PRIMARY KEY, project_root TEXT NOT NULL, project_key TEXT NOT NULL, federation_root TEXT NOT NULL, federation_key TEXT NOT NULL, bound_at TEXT NOT NULL)"


def _directory(value: Path) -> tuple[str, str]:
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError("DIRECTORY_REQUIRED")
    return str(path), os.path.normcase(str(path))


def bind(coord_root: Path, federation_root: Path, project_root: Path, workspace_id: str) -> dict:
    """Bind an existing workspace to a directory; never create a workspace."""
    validate_workspace_id(workspace_id)
    project, key = _directory(project_root)
    federation, federation_key = _directory(federation_root)
    engine.load(Path(federation), workspace_id)
    coord = Path(coord_root)
    coord.mkdir(parents=True, exist_ok=True)
    for attempt in range(5):
        db = None
        try:
            db = sqlite3.connect(coord / DB_NAME, timeout=30, isolation_level=None)
            db.execute("BEGIN IMMEDIATE")
            # Serialize first-time DDL with all other writes, including across
            # processes. SQLite can still transiently report readonly on
            # Windows during first creation, so retry the entire transaction.
            db.execute(_SCHEMA)
            existing = db.execute("SELECT project_root, project_key, federation_root, federation_key, bound_at FROM bindings WHERE workspace_id=?", (workspace_id,)).fetchone()
            if existing and (existing[1] != key or existing[3] != federation_key):
                raise ValueError("WORKSPACE_BINDING_CONFLICT")
            created = existing is None
            if created:
                db.execute("INSERT INTO bindings VALUES(?,?,?,?,?,?)", (workspace_id, project, key, federation, federation_key, datetime.now(timezone.utc).isoformat()))
            db.commit()
            return {"schema": "aep-workspace-binding-v1", "workspace_id": workspace_id,
                    "project_root": project, "federation_root": federation, "created": created,
                    "workspace_state_validated": True, "project_association_verified": False,
                    "identity_verified": False, "session_verified": False}
        except sqlite3.OperationalError as exc:
            if db is not None and db.in_transaction:
                db.rollback()
            if attempt == 4 or not any(word in str(exc).lower() for word in ("locked", "readonly")):
                raise
            time.sleep(0.01 * (2 ** attempt))
        except BaseException:
            if db is not None and db.in_transaction:
                db.rollback()
            raise
        finally:
            if db is not None:
                db.close()
    raise AssertionError("unreachable")  # pragma: no cover


def lookup(coord_root: Path, project_root: Path, workspace_id: str = "") -> dict:
    """Resolve a binding; ambiguous directories require explicit workspace ID."""
    if workspace_id:
        validate_workspace_id(workspace_id)
    project, key = _directory(project_root)
    db_path = Path(coord_root) / DB_NAME
    if not db_path.is_file():
        raise ValueError("BINDING_NOT_FOUND")
    with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT workspace_id,project_root,federation_root,bound_at FROM bindings WHERE project_key=? AND (?='' OR workspace_id=?) ORDER BY workspace_id", (key, workspace_id, workspace_id)).fetchall()
    if not rows:
        raise ValueError("BINDING_NOT_FOUND")
    if len(rows) != 1:
        raise ValueError("AMBIGUOUS_BINDING_SPECIFY_WORKSPACE")
    result = dict(rows[0])
    # Detect removal or mismatch of the authoritative state; do not mint a new ID.
    engine.load(Path(result["federation_root"]), result["workspace_id"])
    return {"schema": "aep-workspace-binding-v1", **result,
            "workspace_state_validated": True, "project_association_verified": False,
            "identity_verified": False, "session_verified": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("bind", "lookup"))
    parser.add_argument("--coord-root", required=True, type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--federation-root", type=Path)
    parser.add_argument("--workspace-id", default="")
    args = parser.parse_args()
    if args.action == "bind":
        if not args.federation_root or not args.workspace_id:
            parser.error("bind requires --federation-root and --workspace-id")
        result = bind(args.coord_root, args.federation_root, args.project_root, args.workspace_id)
    else:
        result = lookup(args.coord_root, args.project_root, args.workspace_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
