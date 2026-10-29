from pathlib import Path
from agent_evolution_protocol.coordination.transport_catalog import TransportRecord, upsert, observe, ranked


def test_catalog_ranks_executable_healthy_transport(tmp_path: Path):
    upsert(tmp_path, TransportRecord("uia", "desktop", 50, discovered=True, executable=True))
    upsert(tmp_path, TransportRecord("mailbox", "file", 90, discovered=True, executable=True))
    observe(tmp_path, name="uia", status="ACKNOWLEDGED")
    assert ranked(tmp_path)[0].name == "uia"


def test_catalog_penalizes_failures(tmp_path: Path):
    upsert(tmp_path, TransportRecord("clawchat", "local-http", 10, discovered=True, executable=True))
    observe(tmp_path, name="clawchat", status="TRANSPORT_ERROR")
    observe(tmp_path, name="clawchat", status="TRANSPORT_ERROR")
    assert ranked(tmp_path)[0].failure_count == 2
