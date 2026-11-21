"""Queue explicit user directives recorded in the conversation journal."""

from hashlib import sha256
import json
from pathlib import Path

from . import commander_base, conversation_journal as journal, instruction_revision, workspace_identity
from .battle_orders import _task_writer_lock


def queue(root: Path, *, event_id: str, workspace: str, task: str,
          targets: list[str], urgent: bool = False) -> dict:
    workspace_identity.validate_workspace_id(workspace)
    if task.startswith("improvement:"):
        raise ValueError("improvement tasks require the dedicated improvement dispatch workflow")
    if not journal.SAFE_EVENT.fullmatch(event_id):
        raise ValueError("invalid journal event ID")
    if not targets or any(not journal.SAFE_AGENT.fullmatch(target) for target in targets):
        raise ValueError("explicit valid targets are required")
    path = journal.archive_root(root) / "events" / f"{event_id}.json"
    event = json.loads(path.read_text(encoding="utf-8"))
    if (event.get("id") != event_id or event.get("actor") != "user"
            or event.get("kind") != "message" or event.get("visibility") != "workspace"):
        raise ValueError("dispatch requires a workspace user message")
    if "workspace_id" in event and event["workspace_id"] != workspace:
        raise ValueError("journal event belongs to a different workspace")
    if not isinstance(event.get("text"), str) or not event["text"].strip():
        raise ValueError("journal message is empty")
    if not journal.SAFE_AGENT.fullmatch(str(event.get("source", ""))):
        raise ValueError("invalid journal source")
    provenance = {"id": event_id, "room": event["room"], "task": task, "workspace": workspace,
                  "urgent": urgent, "sha256": sha256(event["text"].encode("utf-8")).hexdigest()}
    orders = []
    keys = {target: "EVT-CHAT-" + sha256(
        f"{workspace}\n{event_id}\n{target}".encode("utf-8")).hexdigest()
            for target in dict.fromkeys(targets)}
    with (instruction_revision.lock(root, workspace, task),
          _task_writer_lock(root, f"chat-dispatch-{event_id}")):
        if instruction_revision.stale(root, {"workspace_id": workspace, "task": task,
                                             "source_chat": provenance}):
            raise ValueError("chat event is not the active instruction revision")
        existing = {}
        targets_by_key = {key: target for target, key in keys.items()}
        for row in commander_base._orders(root):
            previous = row.get("source_chat") or {}
            key = row.get("event_id")
            if previous.get("id") == event_id and (
                    previous != provenance or row.get("sender") != event["source"]):
                raise ValueError("event already queued with different dispatch metadata")
            if key not in targets_by_key:
                continue
            if (key in existing or previous != provenance
                    or row.get("target") != targets_by_key[key]
                    or row.get("workspace_id") != workspace
                    or row.get("sender") != event["source"]):
                raise ValueError("chat dispatch event ID collision")
            existing[key] = row
        for target, key in keys.items():
            order = existing.get(key)
            if order is None:
                order = commander_base.submit_order(
                    root, target=target, task=task, text=event["text"], sender=event["source"],
                    workspace_id=workspace, event_id=key, source_chat=provenance,
                    priority="urgent" if urgent else "normal", urgent_interject=urgent)
            orders.append({key: order[key] for key in ("id", "target", "event_id")})
    return {"source_event_id": event_id, "status": "QUEUED", "orders": orders}
