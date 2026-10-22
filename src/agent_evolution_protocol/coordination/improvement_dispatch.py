"""Match pending improvements to declared capabilities and queue existing orders.

No desktop input, model calls, new conversations, or automatic test execution.
"""
from pathlib import Path
import json

from . import commander, improvement, improvement_work, presence
from .battle_orders import _task_writer_lock


def _roster(value):
    if not isinstance(value, dict) or set(value) != {"agents"} or not isinstance(value["agents"], list):
        raise ValueError("roster requires agents list")
    seen = set()
    for row in value["agents"]:
        if not isinstance(row, dict) or set(row) != {"agent", "actions"}:
            raise ValueError("roster rows require agent/actions")
        name = row["agent"]
        if not isinstance(name, str) or not commander.SAFE_AGENT.fullmatch(name) or name in seen:
            raise ValueError("invalid or duplicate roster agent")
        if not isinstance(row["actions"], list) or not row["actions"] or any(
                action not in {"validate", "review", "trial", "assess"} for action in row["actions"]):
            raise ValueError("invalid action capability")
        seen.add(name)
    return value["agents"]


def _occupies_slot(root, order):
    """ACK is acceptance of a message, not evidence that a member is idle."""
    if not order.get("task", "").startswith("improvement:") or order.get("state") == "RETIRED":
        return False
    if order.get("state") != "AGENT_ACK":
        return True  # Unknown deliveries never silently free their recipient.
    try:
        proposal = improvement.status(root, order["task"].split(":", 1)[1])
        event_id = "EVT-IMPROVE-" + proposal["events"][-1]["sha256"][:24].upper()
        return order.get("event_id") == event_id
    except (OSError, ValueError, KeyError, TypeError):
        return True  # Missing/damaged progress is not completion evidence.


def _schedule(root, roster, *, apply, limit):
    members = _roster(roster)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise ValueError("limit must be 1..20")
    online = {row["agent"] for row in presence.snapshot(root)["agents"]
              if row["state"] == "online" and not row["active_assignments"]}
    orders = commander._orders(root)
    # Existing uncertain/failed deliveries must be inspected, not silently reassigned.
    busy = {order.get("target") for order in orders if _occupies_slot(root, order)}
    work = improvement_work.pending(root)["work"]
    busy.update(item.get("owner") for item in work if not item["available"] and item.get("owner"))
    results = []
    for item in work:
        if not item["available"]:
            continue
        proposal = improvement.status(root, item["id"])
        head = proposal["events"][-1]["sha256"]
        event_id = "EVT-IMPROVE-" + head[:24].upper()
        existing = [order for order in orders if order.get("event_id") == event_id]
        if existing:
            results.append({"id": item["id"], "status": "ALREADY_QUEUED", "order_id": existing[0]["id"]})
            continue
        excluded = {e["actor"] for e in proposal["events"]} if item["action"] == "review" else set()
        target = next((row["agent"] for row in members if item["action"] in row["actions"]
                       and row["agent"] in online - busy - excluded), None)
        if not target:
            results.append({"id": item["id"], "status": "NO_ELIGIBLE_MEMBER"})
            continue
        result = {"id": item["id"], "target": target, "action": item["action"], "status": "PLAN_ONLY"}
        if apply:
            text = (f"改进接力：{item['id']}，阶段 {item['action']}。保持原会话与模型，不新建会话。\n"
                    f"协作根目录：{Path(root).resolve()}\n"
                    "先读取 docs/EVOLUTION_EXECUTION_PACT.zh-CN.md，使用 coord-improvement status 核对当前状态，"
                    "claim 成功后，用返回 token 调用 brief 检查证据与阶段，再按预算执行；validate 时带同一 --token。领取失败则停止，不重复操作。"
                    "ACK 只表示接到消息，不代表认领或完成。若缺能力请明确报告，不自行扩权。")
            order = commander.submit_order(root, target=target, task=f"improvement:{item['id']}",
                                           text=text, sender="improvement-router", event_id=event_id,
                                           allow_launch=False)
            result.update(status="QUEUED", order_id=order["id"])
        busy.add(target)
        results.append(result)
        if sum(r["status"] in {"QUEUED", "PLAN_ONLY"} for r in results) >= limit:
            break
    return {"assignments": results, "orders_queued": sum(r["status"] == "QUEUED" for r in results),
            "delivery_attempted": False, "agent_ack_verified": False,
            "note": "Presence and capability declarations are not authenticated availability or acceptance."}


def schedule(root, roster, *, apply=False, limit=1):
    root = Path(root)
    if not apply:
        return _schedule(root, roster, apply=False, limit=limit)
    with _task_writer_lock(root, "improvement-dispatch"):
        return _schedule(root, roster, apply=True, limit=limit)


def delivery_status(root):
    """Join persisted commander receipts with proposal progress; never capture UI."""
    rows = []
    for order in commander._orders(root):
        task = order.get("task", "")
        if not task.startswith("improvement:"):
            continue
        identity = task.removeprefix("improvement:")
        ack_recorded = bool(order.get("ack")) or order.get("state") == "AGENT_ACK" or any(
            attempt.get("state") == "AGENT_ACK" for attempt in order.get("attempts", []))
        row = {"order_id": order["id"], "id": identity, "target": order.get("target"),
               "transport_state": order.get("state"), "ack_recorded": ack_recorded}
        try:
            proposal = improvement.status(root, identity)
            head = proposal["events"][-1]["sha256"]
            stale = order.get("event_id") != "EVT-IMPROVE-" + head[:24].upper()
            path = improvement_work._path(root, identity)
            lease = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            active = bool(lease.get("active") and lease.get("head") == head and
                          lease.get("expires", 0) > improvement_work._now())
            row.update(proposal_state=proposal["state"], obsolete=stale,
                       claim_owner=lease.get("actor") if active else None,
                       target_claimed=active and lease.get("actor") == order.get("target"),
                       claim_expires_at=lease.get("expires") if active else None)
            if order.get("state") == "RETIRED":
                row.update(progress="RETIRED", next_action="inspect_current_stage")
            elif stale:
                row.update(progress="STAGE_CHANGED", next_action="inspect_current_stage")
            elif active and lease.get("actor") != order.get("target"):
                row.update(progress="CLAIMED_ELSEWHERE", next_action="coordinate_with_claim_owner_do_not_duplicate")
            elif active:
                row.update(progress="CLAIMED", next_action="await_evidence_or_lease_expiry")
            elif lease.get("head") == head and lease.get("active") and lease.get("expires", 0) <= improvement_work._now():
                row.update(progress="CLAIM_EXPIRED", next_action="inspect_evidence_before_reclaim")
            elif lease.get("head") == head and lease.get("result") == "RELEASED":
                row.update(progress="CLAIM_RELEASED", next_action="inspect_release_before_reclaim")
            elif order.get("state") in {"CEASED", "FAILED"}:
                row.update(progress="DELIVERY_BLOCKED", next_action="inspect_before_retry")
            elif row["ack_recorded"]:
                row.update(progress="ACK_WITHOUT_CLAIM", next_action="check_acceptance_not_completion")
            else:
                row.update(progress="AWAITING_ACK", next_action="inspect_delivery_receipt")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            row.update(progress="DAMAGED", error=str(exc), next_action="inspect")
        rows.append(row)
    return {"orders": rows, "ui_read_performed": False, "delivery_attempted": False,
            "note": "ACK is a persisted commander observation, not a fresh authentication or completion proof."}


def reroute(root, *, order_id, target, actor, note, roster, apply=False):
    """Reassign a proven pre-input order, never cancel a possibly running task."""
    members = _roster(roster)
    if (not isinstance(actor, str) or not commander.SAFE_AGENT.fullmatch(actor)
            or not isinstance(target, str) or not commander.SAFE_AGENT.fullmatch(target)
            or not isinstance(note, str) or not note.strip()):
        raise ValueError("valid actor, target and nonempty note required")
    root = Path(root)
    # Match dispatch's lock and prevent a claim from appearing during reassignment.
    with _task_writer_lock(root, "improvement-dispatch"):
        with _task_writer_lock(root, "improvement-handoff"):
            orders = commander._orders(root)
            order = next((o for o in orders if o.get("id") == order_id), None)
            if not order or not order.get("task", "").startswith("improvement:"):
                raise ValueError("unknown improvement order")
            if target == order.get("target"):
                raise ValueError("target is unchanged")
            if order.get("state") not in {"DEPOSITED", "DEFERRED"} or any(
                    a.get("state") not in {"DEPOSITED", "DEFERRED"} for a in order.get("attempts", [])):
                raise ValueError("delivery may have started; cannot reroute")
            identity = order["task"].split(":", 1)[1]
            proposal = improvement.status(root, identity)
            head = proposal["events"][-1]["sha256"]
            if order.get("event_id") != "EVT-IMPROVE-" + head[:24].upper():
                raise ValueError("obsolete order; inspect current stage")
            work = improvement_work.pending(root)["work"]
            item = next((w for w in work if w["id"] == identity), None)
            if not item or not item["available"]:
                raise ValueError("work unavailable or already claimed")
            eligible = any(m["agent"] == target and item["action"] in m["actions"] for m in members)
            online = any(p["agent"] == target and p["state"] == "online" and not p["active_assignments"]
                         for p in presence.snapshot(root)["agents"])
            busy = any(o.get("target") == target and _occupies_slot(root, o) for o in orders)
            busy = busy or any(w.get("owner") == target and not w["available"] for w in work)
            excluded = item["action"] == "review" and target in {e["actor"] for e in proposal["events"]}
            if not eligible or not online or busy or excluded:
                raise ValueError("target must be online, capable, available and independent")
            previous = order["target"]
            if apply:
                commander._record_attempt(root, {**order, "target": target}, state="DEPOSITED",
                    receipt={"actor": actor, "reason": note, "previous_target": previous,
                             "target": target, "head": head, "desktop_input_performed": False},
                    note="PREINPUT_ORDER_REROUTED")
            return {"order_id": order_id, "event_id": order["event_id"], "previous_target": previous,
                    "target": target, "status": "REROUTED" if apply else "PLAN_ONLY",
                    "delivery_attempted": False, "execution_cancelled": False,
                    "note": "Only a queue target changed; presence and capability are declarations, not authentication."}


def retire(root, *, order_id, actor, note):
    """Retire obsolete, proven pre-input orders only. Never cancel uncertain sends."""
    if not isinstance(actor, str) or not commander.SAFE_AGENT.fullmatch(actor) or not isinstance(note, str) or not note.strip():
        raise ValueError("valid actor and nonempty retirement note required")
    with _task_writer_lock(Path(root), "improvement-dispatch"):
        order = next((o for o in commander._orders(root) if o.get("id") == order_id), None)
        if not order or not order.get("task", "").startswith("improvement:"):
            raise ValueError("unknown improvement order")
        if order.get("state") == "RETIRED":
            return {"order_id": order_id, "state": "RETIRED", "already_retired": True}
        if order.get("state") not in {"DEPOSITED", "DEFERRED"} or any(
                a.get("state") not in {"DEPOSITED", "DEFERRED"} for a in order.get("attempts", [])):
            raise ValueError("delivery may have started; retirement requires proven pre-input history")
        identity = order["task"].split(":", 1)[1]
        proposal = improvement.status(root, identity)
        current_event = "EVT-IMPROVE-" + proposal["events"][-1]["sha256"][:24].upper()
        if order.get("event_id") == current_event:
            raise ValueError("current-stage order is not obsolete")
        result = commander._record_attempt(root, order, state="RETIRED",
            receipt={"actor": actor, "reason": note, "current_head": proposal["events"][-1]["sha256"],
                     "desktop_input_performed": False}, note="OBSOLETE_PREINPUT_ORDER_RETIRED")
        return {"order_id": order_id, "state": result["state"], "delivery_cancelled": False,
                "note": "Removed from future routing only; no sent message was recalled."}
