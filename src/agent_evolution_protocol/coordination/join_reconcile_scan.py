"""Bounded, restartable recovery of join journal projections for bound workspaces.

Only explicit workspace bindings are considered.  This worker is not a
notification, session discovery, desktop delivery, or acknowledgement service.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from ..federation.locking import workspace_lock
from . import join_reconciler, workspace_binding
from .workspace_identity import validate_workspace_id

SCHEMA = "aep-join-reconcile-scan-v1"
CORE_LOCK_TIMEOUT_SECONDS = 0.25
CORE_DB_TIMEOUT_SECONDS = 0.25
CORE_BUDGET_SECONDS = 2.0


def _state(root: Path) -> Path:
    return Path(root) / "join_reconciler" / "scan-state.json"


def _read_state(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        return ""
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError("SCAN_CURSOR_UNREADABLE") from exc
    if (not isinstance(value, dict) or value.get("schema") != SCHEMA
            or not isinstance(value.get("last_workspace"), str)
            or len(value["last_workspace"]) > 128):
        raise ValueError("SCAN_CURSOR_INVALID")
    return value["last_workspace"]


def _save_state(path: Path, last_workspace: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump({"schema": SCHEMA, "last_workspace": last_workspace}, handle)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _bindings(root: Path, *, after: str, limit: int) -> list[tuple[str, str]] | None:
    path = Path(root) / workspace_binding.DB_NAME
    try:
        path.stat()
    except FileNotFoundError:
        return None
    db = sqlite3.connect(path.resolve(strict=True).as_uri() + "?mode=ro", uri=True,
                         timeout=CORE_DB_TIMEOUT_SECONDS)
    try:
        return db.execute("SELECT workspace_id,project_root FROM bindings WHERE workspace_id > ? "
                          "ORDER BY workspace_id LIMIT ?", (after, limit)).fetchall()
    finally:
        db.close()


def run(root: Path, *, max_workspaces: int = 1, events_per_workspace: int = 20,
        budget_seconds: float = CORE_BUDGET_SECONDS) -> dict:
    """Reconcile at most one bounded page per selected explicit binding.

    A persistent round-robin position prevents one large or broken workspace
    from starving later workspaces. Per-workspace source cursors remain owned
    by join_reconciler, so replay after a crash does not duplicate journal files.
    """
    for value, name, upper in ((max_workspaces, "max_workspaces", 100),
                               (events_per_workspace, "events_per_workspace", 500)):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
            raise ValueError(f"{name} must be between 1 and {upper}")
    if (isinstance(budget_seconds, bool) or not isinstance(budget_seconds, (int, float))
            or not math.isfinite(budget_seconds) or not 0 < budget_seconds <= 30):
        raise ValueError("budget_seconds must be finite and between 0 and 30")
    deadline = time.monotonic() + budget_seconds
    root = Path(root)
    path = _state(root)
    with workspace_lock(path.with_suffix(".lock"), timeout=CORE_LOCK_TIMEOUT_SECONDS):
        join_reconciler.join_journal._check_deadline(deadline)
        last = _read_state(path)
        page = _bindings(root, after=last, limit=max_workspaces)
        join_reconciler.join_journal._check_deadline(deadline)
        if page is None:
            return {"schema": SCHEMA, "status": "NO_BINDING_STORE", "processed": 0,
                    "rows": [], "notification_delivered": False, "agent_ack": False}
        if not page and last:
            page = _bindings(root, after="", limit=max_workspaces)
            if page is None:
                return {"schema": SCHEMA, "status": "NO_BINDING_STORE", "processed": 0,
                        "rows": [], "notification_delivered": False, "agent_ack": False}
        if not page:
            return {"schema": SCHEMA, "status": "NO_BINDINGS", "processed": 0,
                    "rows": [], "notification_delivered": False, "agent_ack": False}
        # Malformed registry rows are not valid scheduling cursors. Do not
        # advance past them or write a cursor that cannot be read next time.
        for workspace_id, project_root in page:
            validate_workspace_id(workspace_id)
            if not isinstance(project_root, str) or not project_root:
                raise ValueError("BINDING_PROJECT_INVALID")
        rows = []
        for workspace_id, project_root in page:
            if time.monotonic() >= deadline:
                break
            try:
                # Revalidate the persisted binding and its existing federation
                # state. We do not infer membership or create a workspace.
                workspace_binding.lookup(root, Path(project_root), workspace_id,
                                         timeout_seconds=CORE_DB_TIMEOUT_SECONDS)
                join_reconciler.join_journal._check_deadline(deadline)
                value = join_reconciler.reconcile(root, workspace_id=workspace_id,
                                                  limit=events_per_workspace,
                                                  lock_timeout_seconds=CORE_LOCK_TIMEOUT_SECONDS,
                                                  timeout_seconds=CORE_DB_TIMEOUT_SECONDS,
                                                  deadline=deadline)
                rows.append({"workspace_id": workspace_id, "status": value["status"],
                             "projected": value.get("projected", 0)})
            except Exception as exc:
                rows.append({"workspace_id": workspace_id, "status": "ERROR",
                             "error": f"{type(exc).__name__}: {exc}"[:500]})
        # Failures are retried on the next round after all selected bindings
        # have had a chance to progress; their source cursor is not advanced.
        if rows:
            _save_state(path, rows[-1]["workspace_id"])
        exhausted = len(rows) < len(page) or time.monotonic() >= deadline
        degraded = exhausted or any(row["status"] not in {"IDLE", "PROJECTED"} for row in rows)
        return {"schema": SCHEMA, "status": "DEGRADED" if degraded else "OK",
                "processed": len(rows), "rows": rows, "budget_exhausted": exhausted,
                "notification_delivered": False, "agent_ack": False}
