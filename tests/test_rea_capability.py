from pathlib import Path
import tempfile

from agent_evolution_protocol.coordination import bridge, rea_capability


def test_announcement_is_idempotent_and_not_ack():
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        doc = base / "REA.md"
        doc.write_text("v1", encoding="utf-8")
        first = rea_capability.announce(base, doc)
        second = rea_capability.announce(base, doc)
        assert set(first["new_mailboxes"]) == set(bridge.AGENTS)
        assert second["new_mailboxes"] == []
        assert "ACK" in first["note"]
        for agent in bridge.AGENTS:
            assert (base / "messages" / agent / f"{first['event_id']}.json").is_file()
