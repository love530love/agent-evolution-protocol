"""Three-zone workspace snapshots: immutable anchor, state DAG, artifact archive.

Handing a successor agent the full conversation is how long tasks drift. The
snapshot instead carries exactly three things:

``anchored_request``
    The user's original goal (``text``), acceptance criteria and constraints.
    Its digest is frozen at creation; any later edit is detected and refused.

``dag``
    Task nodes with dependencies, state, owner and attempt count. Enough to
    resume scheduling without replaying the transcript.

``artifacts``
    Finished artifacts as ``{name, sha256, path}``. Content stays on disk; the
    snapshot only pins it.

The emitted object is a valid ``workspace-snapshot-v1`` document (see
``schemas/workspace-snapshot-v1.schema.json``); federation-specific extras such
as ``at`` and ``digest`` ride along under the schema's ``additionalProperties``.
There is deliberately no second, private snapshot standard.

The snapshot itself is content-addressed, so two runs that produced the same
state produce the same digest.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from ..coordination.workspace_identity import validate_workspace_id
from .store import atomic_write, digest, read_json, stamp, workspace_dir


SNAPSHOT_SCHEMA = "aep-workspace-snapshot-v1"
ANCHOR_FIELDS = ("goal", "acceptance", "constraints")


def make_anchor(*, goal, acceptance, constraints=(), moment=None):
    """Create the immutable block; validation is strict on purpose."""
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("anchor.goal is required")
    if not isinstance(acceptance, (list, tuple)) or not acceptance:
        raise ValueError("anchor.acceptance must be a non-empty list")
    for item in acceptance:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("anchor.acceptance entries must be non-empty strings")
    if not isinstance(constraints, (list, tuple)):
        raise ValueError("anchor.constraints must be a list")
    anchor = {"goal": goal.strip(), "acceptance": [item.strip() for item in acceptance],
              "constraints": [str(item).strip() for item in constraints if str(item).strip()],
              "created_at": stamp(moment)}
    anchor["sha256"] = digest({key: anchor[key] for key in ANCHOR_FIELDS})
    return anchor


def verify_anchor(anchor):
    if not isinstance(anchor, dict):
        raise ValueError("anchor must be an object")
    expected = anchor.get("sha256")
    actual = digest({key: anchor[key] for key in ANCHOR_FIELDS})
    if expected != actual:
        raise ValueError("ANCHOR_MUTATED")
    return anchor


def _nodes(dag):
    """Normalise a task mapping (or node list) into the canonical node array."""
    if isinstance(dag, dict):
        rows = [dict(task) for _, task in sorted(dag.items())]
    elif isinstance(dag, (list, tuple)):
        rows = [dict(item) for item in dag]
    else:
        raise ValueError("dag must be a mapping of tasks or a list of nodes")
    return rows


def anchor_of(snapshot):
    """Rebuild the internal anchor view from the canonical snapshot document."""
    request = snapshot.get("anchored_request")
    if not isinstance(request, dict):
        raise ValueError("snapshot is missing anchored_request")
    return {"goal": request.get("text"),
            "acceptance": list(request.get("acceptance") or []),
            "constraints": list(request.get("constraints") or []),
            "sha256": request.get("sha256"),
            "created_at": request.get("created_at")}


def build(workspace_id, *, anchor, dag, artifacts=(), moment=None, version=None,
          parent_snapshot=None):
    """Assemble one ``workspace-snapshot-v1`` document; refuses a mutated anchor."""
    validate_workspace_id(workspace_id)
    verify_anchor(anchor)
    snapshot = {
        "schema": SNAPSHOT_SCHEMA,
        "workspace_id": workspace_id,
        "version": int(version or 1),
        "parent_snapshot": parent_snapshot,
        "at": stamp(moment),
        "anchored_request": {
            "text": anchor["goal"],
            "acceptance": list(anchor["acceptance"]),
            "constraints": list(anchor["constraints"]),
            "sha256": anchor["sha256"],
            "created_at": anchor["created_at"],
        },
        "dag": {"nodes": _nodes(dag)},
        "intelligence": {"anchor_sha256": anchor["sha256"]},
        "artifacts": [dict(item) for item in artifacts],
    }
    snapshot["digest"] = digest({key: snapshot[key] for key in snapshot if key != "digest"})
    return snapshot


def verify(snapshot, *, expected_anchor_sha256=None):
    """Check watermark, anchor integrity and self-digest; never repairs."""
    if not isinstance(snapshot, dict) or snapshot.get("schema") != SNAPSHOT_SCHEMA:
        raise ValueError("unknown snapshot schema")
    validate_workspace_id(snapshot.get("workspace_id"))
    anchor = anchor_of(snapshot)
    verify_anchor(anchor)
    if expected_anchor_sha256 and anchor["sha256"] != expected_anchor_sha256:
        raise ValueError("ANCHOR_MUTATED")
    actual = digest({key: snapshot[key] for key in snapshot if key != "digest"})
    if snapshot.get("digest") != actual:
        raise ValueError("SNAPSHOT_DIGEST_MISMATCH")
    nodes = (snapshot.get("dag") or {}).get("nodes")
    if not isinstance(nodes, list):
        raise ValueError("snapshot dag must carry a node list")
    return {"valid": True, "workspace_id": snapshot["workspace_id"],
            "version": snapshot["version"], "digest": snapshot["digest"],
            "anchor_sha256": anchor["sha256"], "tasks": len(nodes)}


def save(root, snapshot):
    verify(snapshot)
    folder = workspace_dir(root, snapshot["workspace_id"]) / "snapshots"
    path = folder / f"v{snapshot['version']:04d}.json"
    atomic_write(path, snapshot)
    atomic_write(folder / "latest.json", snapshot)
    return path


def latest(root, workspace_id):
    path = workspace_dir(root, workspace_id) / "snapshots" / "latest.json"
    return read_json(path)


def next_version(root, workspace_id):
    folder = workspace_dir(root, workspace_id) / "snapshots"
    if not folder.is_dir():
        return 1
    versions = [int(path.stem[1:]) for path in folder.glob("v*.json") if path.stem[1:].isdigit()]
    return max(versions, default=0) + 1


def history(root, workspace_id):
    folder = workspace_dir(root, workspace_id) / "snapshots"
    if not folder.is_dir():
        return []
    rows = []
    for path in sorted(folder.glob("v*.json")):
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return rows


def rollback(root, workspace_id, version):
    """Return an old snapshot after verifying it; the caller decides to re-apply."""
    path = Path(workspace_dir(root, workspace_id)) / "snapshots" / f"v{int(version):04d}.json"
    snapshot = read_json(path)
    if snapshot is None:
        raise ValueError("snapshot version not found")
    verify(snapshot)
    return snapshot


def artifact(path):
    """Pin one artifact by digest without copying its content into the snapshot."""
    path = Path(path)
    if not path.is_file():
        raise ValueError("artifact does not exist")
    return {"name": path.name, "sha256": digest({"bytes": path.read_bytes().hex()}),
            "path": str(path), "size": path.stat().st_size,
            "pinned_at": datetime.now(timezone.utc).isoformat()}
