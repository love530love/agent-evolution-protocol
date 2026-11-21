from hashlib import sha256

import pytest

from agent_evolution_protocol.coordination import (
    chat_dispatch, commander_base, conversation_journal, dispatch_gate,
    instruction_revision, workspace_identity,
)


def test_desktop_gate_accepts_both_explicit_fanout_recipients(tmp_path):
    workspace = workspace_identity.new_workspace_id()
    event = conversation_journal.append_event(
        tmp_path, room="workspace", actor="user", source="operator",
        kind="message", text="Review the artifact", workspace_id=workspace)["id"]
    chat_dispatch.queue(tmp_path, event_id=event, workspace=workspace,
                        task="T1", targets=["qoder", "workbuddy"])
    orders = commander_base._orders(tmp_path)
    for order in orders:
        verdict = dispatch_gate.check(order, profile={"agent": order["target"]},
                                      newer_orders=orders)
        assert verdict["verdict"] == "send", verdict


def test_queue_refuses_stale_revision_before_creating_orders(tmp_path):
    def publish(text):
        return conversation_journal.append_event(
            tmp_path, room="agent-legion", actor="user", source="operator",
            kind="message", text=text)["id"]

    workspace = workspace_identity.new_workspace_id()
    old = publish("Submit the package")
    new = publish("Prepare the package only")
    instruction_revision.activate(tmp_path, workspace, "T1", event=new,
                                  expected="", actor="operator")
    with pytest.raises(ValueError, match="not the active instruction revision"):
        chat_dispatch.queue(tmp_path, event_id=old, workspace=workspace,
                            task="T1", targets=["qoder", "workbuddy"])
    assert not commander_base._orders(tmp_path)
    assert len(chat_dispatch.queue(tmp_path, event_id=new, workspace=workspace,
                                   task="T1", targets=["qoder"])["orders"]) == 1


def test_bound_event_cannot_be_activated_or_queued_in_another_workspace(tmp_path):
    workspace = workspace_identity.new_workspace_id()
    other = workspace_identity.new_workspace_id()
    event = conversation_journal.append_event(
        tmp_path, room="workspace", actor="user", source="operator", kind="message",
        text="Review the artifact", workspace_id=workspace)["id"]
    with pytest.raises(ValueError, match="different workspace"):
        instruction_revision.activate(tmp_path, other, "T1", event=event, expected="", actor="operator")
    with pytest.raises(ValueError, match="different workspace"):
        chat_dispatch.queue(tmp_path, event_id=event, workspace=other, task="T1", targets=["qoder"])
    assert not commander_base._orders(tmp_path)
    assert chat_dispatch.queue(tmp_path, event_id=event, workspace=workspace,
                               task="T1", targets=["qoder"])["status"] == "QUEUED"


def test_repeat_fanout_reuses_orders_and_can_finish_partial_fanout(tmp_path):
    event = conversation_journal.append_event(
        tmp_path, room="agent-legion", actor="user", source="operator",
        kind="message", text="Apply the revised acceptance criteria")
    request = dict(event_id=event["id"], workspace=workspace_identity.new_workspace_id(),
                   task="T1", urgent=True)
    first = chat_dispatch.queue(tmp_path, targets=["qoder"], **request)
    expanded = chat_dispatch.queue(tmp_path, targets=["qoder", "workbuddy"], **request)
    assert expanded["orders"][0] == first["orders"][0]
    assert len(expanded["orders"]) == 2
    assert chat_dispatch.queue(tmp_path, targets=["qoder", "workbuddy"], **request) == expanded
    with pytest.raises(ValueError, match="different dispatch metadata"):
        chat_dispatch.queue(tmp_path, targets=["codex"], **{**request, "task": "T2"})


def test_assistant_message_does_not_become_a_dispatch(tmp_path):
    event = conversation_journal.append_event(
        tmp_path, room="agent-legion", actor="assistant", source="codex",
        kind="result", text="Suggested changes")
    with pytest.raises(ValueError, match="workspace user message"):
        chat_dispatch.queue(tmp_path, event_id=event["id"], workspace=workspace_identity.new_workspace_id(),
                            task="T1", targets=["qoder"])


def test_manual_order_with_same_event_key_is_not_reused(tmp_path):
    event = conversation_journal.append_event(
        tmp_path, room="agent-legion", actor="user", source="operator",
        kind="message", text="Review this task")
    workspace = workspace_identity.new_workspace_id()
    event_hash = sha256(f"{workspace}\n{event['id']}\nqoder".encode("utf-8")).hexdigest()
    commander_base.submit_order(tmp_path, sender="operator", target="qoder", task="T1",
                                text="Other directive", workspace_id=workspace,
                                event_id="EVT-CHAT-" + event_hash)
    with pytest.raises(ValueError, match="event ID collision"):
        chat_dispatch.queue(tmp_path, event_id=event["id"], workspace=workspace,
                            task="T1", targets=["workbuddy", "qoder"])
    assert len(commander_base._orders(tmp_path)) == 1
