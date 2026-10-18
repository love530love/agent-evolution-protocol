"""Match pending improvements to declared capabilities and queue existing orders.

No desktop input, model calls, new conversations, or automatic test execution.
"""
from pathlib import Path

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


def _schedule(root, roster, *, apply, limit):
    members = _roster(roster)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise ValueError("limit must be 1..20")
    online = {row["agent"] for row in presence.snapshot(root)["agents"]
              if row["state"] == "online" and not row["active_assignments"]}
    orders = commander._orders(root)
    # Existing uncertain/failed deliveries must be inspected, not silently reassigned.
    busy = {order.get("target") for order in orders if order.get("task", "").startswith("improvement:")
            and order.get("state") != "AGENT_ACK"}
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
                    "claim 成功后按证据与预算执行。领取失败则停止，不重复操作。"
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
