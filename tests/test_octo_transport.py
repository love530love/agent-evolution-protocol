import json
from types import SimpleNamespace

from agent_evolution_protocol.coordination import octo_transport


EVENT = {"id": "D-test-1", "kind": "delegation", "to": "qoder", "task": "T125",
         "instruction": "Inspect private local failure log"}


def test_preview_redacts_body_and_never_claims_ack():
    result = octo_transport.send_event(EVENT, channel_id="chat-1")
    assert result["state"] == "PREVIEW_ONLY"
    assert result["agent_accepted"] is False
    content = result["request"]["payload"]["content"]
    assert "D-test-1" in content
    assert "private local failure log" not in content


def test_explicit_send_uses_one_no_retry_call(monkeypatch):
    monkeypatch.setattr(octo_transport.shutil, "which", lambda _: "C:/octo-cli.exe")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "data": {"message_id": "m1"}}))

    result = octo_transport.send_event(EVENT, channel_id="chat-1", execute=True, runner=fake_run)
    assert result["state"] == "SENT_UNVERIFIED"
    assert result["agent_accepted"] is False
    assert len(calls) == 1
    assert "--no-retry" in calls[0]
    assert json.loads(calls[0][calls[0].index("--data") + 1])["channel_id"] == "chat-1"


def test_timeout_is_unknown_not_retry(monkeypatch):
    import subprocess
    monkeypatch.setattr(octo_transport.shutil, "which", lambda _: "C:/octo-cli.exe")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("octo-cli", 20)

    result = octo_transport.send_event(EVENT, channel_id="chat-1", execute=True, runner=timeout)
    assert result["state"] == "UNKNOWN_DELIVERY"
    assert result["agent_accepted"] is False
