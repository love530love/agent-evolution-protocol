from unittest.mock import patch

import pytest

from agent_evolution_protocol.coordination import (
    chat_dispatch, commander, conversation_journal, instruction_revision, workspace_identity,
)


def _event(root, text):
    return conversation_journal.append_event(
        root, room="workspace", actor="user", source="operator", kind="message", text=text)["id"]


def test_delivery_status_distinguishes_old_visible_from_uncertain(tmp_path):
    workspace = workspace_identity.new_workspace_id()
    old = _event(tmp_path, "Original")
    new = _event(tmp_path, "Correction")
    chat_dispatch.queue(tmp_path, event_id=old, workspace=workspace, task="T1",
                        targets=["qoder", "workbuddy", "codex"])
    for order in commander._orders(tmp_path):
        if order["target"] != "codex":
            commander._record_attempt(tmp_path, order, state=(
                "MESSAGE_VISIBLE" if order["target"] == "qoder" else "INPUT_SUBMITTED"))
    instruction_revision.activate(tmp_path, workspace, "T1", event=new, expected="", actor="operator")
    report = instruction_revision.delivery_status(tmp_path, workspace, "T1")
    assert report["active_event_id"] == new
    assert {row["target"]: row["next_step"] for row in report["orders"]} == {
        "qoder": "CHECK_CORRECTION_ACK", "workbuddy": "RECONCILE_OLD_DELIVERY",
        "codex": "SUPERSEDED_BEFORE_INPUT",
    }
    chat_dispatch.queue(tmp_path, event_id=new, workspace=workspace, task="T1",
                        targets=["qoder", "workbuddy"])
    for order in commander._orders(tmp_path):
        if order["source_chat"]["id"] == new and order["target"] == "qoder":
            commander._record_attempt(tmp_path, order, state="AGENT_ACK",
                                      receipt={"evidence": "fixture ACK for correction"})
    previous = {row["target"]: row for row in
                instruction_revision.delivery_status(tmp_path, workspace, "T1")["orders"]
                if row["journal_event_id"] == old}
    assert previous["qoder"]["next_step"] == "VERIFY_CORRECTION_ADOPTION"
    assert previous["qoder"]["correction"]["state"] == "AGENT_ACK"
    assert previous["workbuddy"]["next_step"] == "RECONCILE_OLD_DELIVERY"


def test_duplicate_corrections_require_reconciliation(tmp_path):
    workspace = workspace_identity.new_workspace_id()
    old = _event(tmp_path, "Original")
    new = _event(tmp_path, "Corrected")
    old_order = chat_dispatch.queue(tmp_path, event_id=old, workspace=workspace,
                                    task="T1", targets=["qoder"])["orders"][0]
    from agent_evolution_protocol.coordination import commander_base
    order = next(item for item in commander._orders(tmp_path) if item["id"] == old_order["id"])
    commander._record_attempt(tmp_path, order, state="MESSAGE_VISIBLE")
    instruction_revision.activate(tmp_path, workspace, "T1", event=new, expected="", actor="operator")
    chat_dispatch.queue(tmp_path, event_id=new, workspace=workspace, task="T1", targets=["qoder"])
    replacement = next(item for item in commander._orders(tmp_path) if item["source_chat"]["id"] == new)
    commander_base.submit_order(tmp_path, target="qoder", task="T1", text="Corrected",
                                sender="operator", workspace_id=workspace,
                                source_chat=replacement["source_chat"])
    report = instruction_revision.delivery_status(tmp_path, workspace, "T1")
    old_row = next(row for row in report["orders"] if row["journal_event_id"] == old)
    assert old_row["next_step"] == "RECONCILE_DUPLICATE_CORRECTIONS"
    assert len(old_row["correction"]["order_ids"]) == 2


def test_group_order_keeps_its_explicit_target_after_input_is_denied(tmp_path):
    order = {"target": "qoder", "source_chat": {"id": "source-event"}}
    with patch.object(commander, "_continuation_surfaces") as candidates:
        result = commander._switch_surface(
            tmp_path, order, blocked_target="qoder", transport="profiled-ui",
            receipt={"status": "TARGET_NEEDS_ELEVATION"})
    assert result is None
    assert order["target"] == "qoder"
    candidates.assert_not_called()


def test_new_revision_stops_only_old_orders_in_its_task(tmp_path):
    workspace = workspace_identity.new_workspace_id()
    old = _event(tmp_path, "Prepare and submit")
    new = _event(tmp_path, "Only prepare")
    instruction_revision.activate(tmp_path, workspace, "T1", event=old, expected="", actor="operator")
    for event, task in ((old, "T1"), (_event(tmp_path, "Other task"), "T2")):
        chat_dispatch.queue(tmp_path, event_id=event, workspace=workspace, task=task, targets=["qoder"])
    instruction_revision.activate(tmp_path, workspace, "T1", event=new, expected=old, actor="operator")
    chat_dispatch.queue(tmp_path, event_id=new, workspace=workspace, task="T1", targets=["qoder"])
    orders = {row["source_chat"]["id"]: row for row in commander._orders(tmp_path)}
    with patch.object(commander, "_dispatch", side_effect=lambda root, order: order) as send:
        result = commander.dispatch(tmp_path, order=orders.pop(old))
        assert result["state"] == "CEASED"
        assert result["attempts"][-1]["note"] == "INSTRUCTION_REVISION_SUPERSEDED"
        send.assert_not_called()
        with patch.object(commander, "_pick_reinforcement_candidate") as pick:
            assert commander.reinforce(tmp_path, result["id"]) is None
            pick.assert_not_called()
        for row in orders.values():
            commander.dispatch(tmp_path, order=row)
        assert send.call_count == 2


def test_activation_requires_current_base_and_serializes_with_dispatch(tmp_path):
    scope = (tmp_path, workspace_identity.new_workspace_id(), "T1")
    old = _event(tmp_path, "First")
    new = _event(tmp_path, "Second")
    instruction_revision.activate(*scope, event=old, expected="", actor="operator")
    with pytest.raises(ValueError, match="current revision changed"):
        instruction_revision.activate(*scope, event=new, expected="", actor="operator")
    with instruction_revision.lock(*scope):
        with pytest.raises(ValueError, match="retry after checking status"):
            instruction_revision.activate(*scope, event=new, expected=old, actor="operator")
    record = instruction_revision.activate(*scope, event=new, expected=old, actor="operator")
    assert [revision["event_id"] for revision in record["history"]] == [old, new]
