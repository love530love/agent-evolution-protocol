from pathlib import Path
import sys
import tempfile

import pytest

from agent_evolution_protocol.coordination import command_memory, conversation_journal


def test_classifies_windows_failures_and_subst_first():
    assert command_memory.classify("OSError: [WinError 206] Filename too long") == "long_path"
    assert command_memory.classify("ParserError: Unexpected token") == "quoting"
    assert command_memory.classify("UnicodeDecodeError: invalid UTF-8") == "encoding"
    assert command_memory.classify("ParameterBindingException") == "parameter_binding"
    card = command_memory.suggest(Path("nonexistent"), text="WinError 206")["cards"][0]
    assert "subst" in card["guidance"] and "short-path.ps1" in card["example"]


def test_journal_ingestion_is_bounded_and_idempotent():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        conversation_journal.append_event(root, room="test", actor="qoder", source="hook",
                                          kind="result", text="ParserError: Unexpected token in command")
        conversation_journal.append_event(root, room="test", actor="workbuddy", source="hook",
                                          kind="message", text="PowerShell 7 is useful")
        first = command_memory.ingest_journal(root)
        assert first["scanned"] == 2
        assert first["learned"] == 1
        assert command_memory.ingest_journal(root)["learned"] == 0
        digest = command_memory.digest(root)
        assert digest["groups"][0]["category"] == "quoting"
        assert digest["groups"][0]["reported"] == 1


def test_run_captures_success_but_promotion_needs_existing_evidence():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        result = command_memory.run(root, agent="codex",
                                    argv=[sys.executable, "-c", "print('ok')"],
                                    category_hint="quoting")
        assert result["exit_code"] == 0
        item = result["observation"]
        assert item["outcome"] == "observed_success"
        with pytest.raises(ValueError):
            command_memory.promote(root, observation_id=item["id"], title="works", why="test",
                                   evidence_file=root / "missing.txt", reviewer="peer")
        evidence = root / "proof.txt"
        evidence.write_text("argv and output reviewed", encoding="utf-8")
        recipe = command_memory.promote(root, observation_id=item["id"], title="safe quoting",
                                        why="argv avoids shell reparsing", evidence_file=evidence,
                                        reviewer="peer")
        assert recipe["category"] == "quoting"
        assert command_memory.suggest(root, text="ParserError")["verified_recipes"][0]["id"] == item["id"]


def test_reported_chat_error_cannot_be_promoted():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        row = command_memory.record(root, agent="workbuddy", command="", output="WinError 206",
                                    exit_code=None, source="conversation_journal")
        evidence = root / "proof.txt"
        evidence.write_text("untrusted", encoding="utf-8")
        with pytest.raises(ValueError, match="observed successful"):
            command_memory.promote(root, observation_id=row["id"], title="bad", why="bad",
                                   evidence_file=evidence, reviewer="peer")


def test_knowledge_announcement_is_idempotent_mailbox_not_ack():
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        repo = base / "repo"
        docs = repo / "docs" / "coordination"
        docs.mkdir(parents=True)
        for name in ("COMMAND_MEMORY.md", "WINDOWS_BUILD_KNOWLEDGE.md", "BROWSER_CAPABILITY_ROUTING.md"):
            (docs / name).write_text(name, encoding="utf-8")
        root, bridge = base / "coordination", base / "bridge"
        first = command_memory.announce(root, bridge, repo_root=repo)
        assert first["new_mailboxes"]
        assert command_memory.announce(root, bridge, repo_root=repo)["new_mailboxes"] == []
        assert (bridge / "messages" / "qoder" / (first["event_id"] + ".json")).is_file()
