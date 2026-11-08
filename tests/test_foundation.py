"""Regression tests for the dependency-free foundation package.

These tests are pure and deterministic: they touch only ``tmp_path`` and the
standard library, never the network, a model, the desktop, or a long-running
process.  They encode the contract that every module now converges on.
"""

import json
from pathlib import Path

from agent_evolution_protocol.coordination.foundation import (
    AEPError,
    ClosedLoopError,
    ProtocolError,
    StorageError,
    TransportError,
    VerificationError,
    iso_now,
    load_json_strict,
    order_id,
    read_json,
    read_text,
    sha256_hex,
    uuid_id,
    write_json_atomic,
    write_text_atomic,
)
from agent_evolution_protocol.coordination.foundation.errors import AEPError as _AEPError


class TestAtomicJsonIO:
    def test_write_then_read_roundtrip(self, tmp_path: Path):
        target = tmp_path / "a" / "b" / "order.json"
        payload = {"id": "x", "n": 1, "list": [1, 2, 3]}
        write_json_atomic(target, payload)
        assert target.is_file()
        assert read_json(target) == payload

    def test_parent_directory_is_created(self, tmp_path: Path):
        target = tmp_path / "nested" / "deep" / "x.json"
        write_json_atomic(target, {"k": 1})
        assert target.is_file()

    def test_no_temp_leftover_after_write(self, tmp_path: Path):
        target = tmp_path / "x.json"
        write_json_atomic(target, {})
        tmps = list(tmp_path.glob("*.tmp"))
        assert tmps == [], f"stray temp files: {tmps}"

    def test_existing_file_survives_failed_write(self, tmp_path: Path):
        target = tmp_path / "x.json"
        write_json_atomic(target, {"keep": True})
        # A non-serializable payload must raise and must not clobber the target.
        try:
            write_json_atomic(target, {"bad": object()})
        except StorageError:
            pass
        else:
            raise AssertionError("expected StorageError for non-serializable payload")
        assert read_json(target) == {"keep": True}

    def test_missing_file_returns_default(self, tmp_path: Path):
        assert read_json(tmp_path / "nope.json", default={"d": 1}) == {"d": 1}
        assert read_json(tmp_path / "nope.json") is None

    def test_corrupt_file_returns_default(self, tmp_path: Path):
        target = tmp_path / "bad.json"
        target.write_text("{not valid json", encoding="utf-8")
        assert read_json(target, default="fallback") == "fallback"

    def test_load_json_strict_raises_on_missing(self, tmp_path: Path):
        try:
            load_json_strict(tmp_path / "missing.json")
        except StorageError as exc:
            assert "missing" in str(exc) or "cannot read" in str(exc)
        else:
            raise AssertionError("expected StorageError on missing file")

    def test_load_json_strict_raises_on_corrupt(self, tmp_path: Path):
        target = tmp_path / "bad.json"
        target.write_text("}{", encoding="utf-8")
        try:
            load_json_strict(target)
        except StorageError:
            pass
        else:
            raise AssertionError("expected StorageError on corrupt JSON")

    def test_unicode_is_preserved(self, tmp_path: Path):
        target = tmp_path / "u.json"
        payload = {"text": "请尽快回表结论 — AEP-ACK"}
        write_json_atomic(target, payload)
        # Read back with the standard library to prove no BOM / mojibake.
        assert json.loads(target.read_text(encoding="utf-8")) == payload

    def test_json_shape_is_stable(self, tmp_path: Path):
        target = tmp_path / "s.json"
        write_json_atomic(target, {"b": 1, "a": 2})
        text = target.read_text(encoding="utf-8")
        # sort_keys=True => 'a' before 'b'; trailing newline preserved.
        assert text.index('"a"') < text.index('"b"')
        assert text.endswith("\n")


class TestAtomicTextIO:
    def test_text_roundtrip(self, tmp_path: Path):
        target = tmp_path / "x.txt"
        write_text_atomic(target, "hello\n世界")
        assert read_text(target) == "hello\n世界"

    def test_text_missing_returns_default(self, tmp_path: Path):
        assert read_text(tmp_path / "nope.txt", default="d") == "d"

    def test_text_failed_write_raises_storage_error(self, tmp_path: Path):
        # A directory as the target cannot be opened for writing.
        try:
            write_text_atomic(tmp_path, "x")
        except StorageError:
            pass
        else:
            raise AssertionError("expected StorageError writing to a directory")


class TestIds:
    def test_iso_now_is_utc_and_parseable(self):
        stamp = iso_now()
        # datetime.fromisoformat handles the trailing 'Z' only on 3.11+; the
        # project emits timezone-aware offsets, so assert the shape instead.
        assert stamp.endswith("+00:00") or "+00:00" in stamp
        assert "Z" not in stamp  # we never emit the bare-Z form that 3.10 rejects

    def test_uuid_id_is_32_hex_chars(self):
        assert len(uuid_id()) == 32
        assert uuid_id() != uuid_id()

    def test_order_id_has_no_fs_unsafe_chars(self):
        oid = order_id()
        for banned in (":", ".", "+"):
            assert banned not in oid, f"order_id {oid!r} contains {banned!r}"

    def test_sha256_hex_is_stable(self):
        assert sha256_hex("abc") == sha256_hex("abc")
        assert sha256_hex("abc") != sha256_hex("abd")
        # str and utf-8 bytes hash identically.
        assert sha256_hex("中文") == sha256_hex("中文".encode("utf-8"))


class TestErrorTaxonomy:
    def test_subclasses_are_catchable_as_aep_error(self):
        for cls in (ProtocolError, TransportError, VerificationError,
                    StorageError, ClosedLoopError):
            assert issubclass(cls, AEPError)
            try:
                raise cls("x")
            except _AEPError:
                pass
            else:
                raise AssertionError(f"{cls.__name__} not caught as AEPError")

    def test_storage_error_wraps_cause(self):
        try:
            raise StorageError("boom") from ValueError("root")
        except StorageError as exc:
            assert exc.__cause__ is not None
