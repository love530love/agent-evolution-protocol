import json
from pathlib import Path

import pytest

from agent_evolution_protocol.coordination.artifact_corroboration import (
    MAX_TEXT_BYTES,
    corroborate,
    main,
)


EVENT = "20261122T034130140395Z-bef2fe82"
ACK = "AEP-ACK:CF7E352CBD583C"


def _write(tmp_path: Path, name: str, text: str | bytes) -> Path:
    path = tmp_path / name
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


def test_matching_artifact_is_corroboration_not_an_agent_ack(tmp_path: Path):
    artifact = _write(tmp_path, "review.md", f"event {EVENT}\nAEP-ACK: CF7E352CBD583C\n")

    result = corroborate(artifact, event_id=EVENT, expected_ack=ACK)

    assert result["status"] == "ARTIFACT_CORROBORATES_CHALLENGE"
    assert result["corroborated"] is True
    assert result["challenge_lines"] == [2]
    assert len(result["artifact_sha256"]) == 64
    assert result["proves"]["artifact_file_observed"] is True
    assert result["proves"]["challenge_text_present"] is True
    assert result["proves"]["original_session_ack"] is False
    assert result["proves"]["task_result_readback"] is False
    assert result["proves"]["acceptance"] is False
    assert result["may_advance"] is False


def test_challenge_without_event_id_is_not_bound_to_the_order(tmp_path: Path):
    artifact = _write(tmp_path, "review.md", "AEP-ACK:CF7E352CBD583C\n")

    result = corroborate(artifact, event_id=EVENT, expected_ack=ACK)

    assert result["status"] == "CHALLENGE_UNBOUND_TO_EVENT"
    assert result["corroborated"] is False
    assert result["event_id_present"] is False
    assert result["challenge_present"] is True
    assert result["may_advance"] is False


def test_event_without_the_specific_challenge_is_not_corroboration(tmp_path: Path):
    artifact = _write(tmp_path, "review.md", f"event {EVENT}\nAEP-ACK:OTHER123\n")

    result = corroborate(artifact, event_id=EVENT, expected_ack=ACK)

    assert result["status"] == "EVENT_PRESENT_CHALLENGE_ABSENT"
    assert result["corroborated"] is False
    assert result["proves"]["original_session_ack"] is False


def test_missing_and_non_utf8_artifacts_do_not_claim_text_evidence(tmp_path: Path):
    missing = corroborate(tmp_path / "missing.md", event_id=EVENT, expected_ack=ACK)
    binary = corroborate(_write(tmp_path, "binary.bin", b"\xff\xfe"),
                         event_id=EVENT, expected_ack=ACK)

    assert missing["status"] == "ARTIFACT_MISSING"
    assert missing["proves"]["artifact_file_observed"] is False
    assert binary["status"] == "ARTIFACT_NOT_UTF8"
    assert binary["proves"]["artifact_file_observed"] is True
    assert binary["proves"]["challenge_text_present"] is False


def test_size_bound_refuses_to_scan_an_oversized_artifact(tmp_path: Path):
    artifact = _write(tmp_path, "large.md", "x" * 12)

    result = corroborate(artifact, event_id=EVENT, expected_ack=ACK, max_text_bytes=11)

    assert result["status"] == "ARTIFACT_TOO_LARGE"
    assert result["artifact_sha256"] == ""
    assert result["may_advance"] is False
    with pytest.raises(ValueError, match="positive integer"):
        corroborate(artifact, event_id=EVENT, expected_ack=ACK, max_text_bytes=True)
    assert MAX_TEXT_BYTES >= 1024


@pytest.mark.parametrize("expected_ack", ["", "AEP-ACK:", "AEP-ACK: two words", "other:CF7E"])
def test_expected_ack_must_be_a_single_explicit_challenge(tmp_path: Path, expected_ack: str):
    artifact = _write(tmp_path, "review.md", "nothing")

    with pytest.raises(ValueError, match="expected_ack"):
        corroborate(artifact, event_id=EVENT, expected_ack=expected_ack)


def test_cli_reports_corroboration_without_granting_advance(tmp_path: Path, capsys):
    artifact = _write(tmp_path, "review.md", f"event {EVENT}\nAEP-ACK: {ACK.split(':')[1].strip()}\n")

    code = main([str(artifact), "--event-id", EVENT, "--expected-ack", ACK])

    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "ARTIFACT_CORROBORATES_CHALLENGE"
    assert report["corroborated"] is True
    assert report["advisory_only"] is True
    assert report["may_advance"] is False
    assert report["proves"]["original_session_ack"] is False
    assert report["proves"]["acceptance"] is False


def test_cli_missing_artifact_is_advisory_not_an_error(tmp_path: Path, capsys):
    code = main([str(tmp_path / "missing.md"), "--event-id", EVENT, "--expected-ack", ACK])

    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "ARTIFACT_MISSING"
    assert report["may_advance"] is False
