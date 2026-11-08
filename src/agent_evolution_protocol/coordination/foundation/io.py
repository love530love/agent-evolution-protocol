"""Crash-safe, encoding-strict JSON / text IO.

This is the single source of truth for persistent writes in the coordination
layer.  The historical pattern -- eleven independent ``_atomic`` copies, each
with subtly different defaults -- meant a crash mid-write, a skipped ``fsync``,
or a stray BOM could corrupt a receipt and nobody would notice until a task
failed to verify.  Converging here makes the contract explicit:

* a write is **atomic** -- ``os.replace`` is atomic on POSIX and Windows, so a
  reader never sees a half-written file;
* a crash leaves at most a ``.tmp`` orphan, never a truncated target;
* the parent directory is created on demand;
* text is always UTF-8 (no locale-dependent encoding, no BOM surprises).

Use :func:`read_json` when a missing or corrupt file is *not* fatal (the old
``_read`` contract) and :func:`load_json_strict` when it must be an error.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from .errors import StorageError


def write_json_atomic(
    path: Path | str,
    payload: Any,
    *,
    indent: int = 1,
    sort_keys: bool = True,
    ensure_ascii: bool = False,
    fsync: bool = True,
) -> None:
    """Write *payload* as JSON to *path* atomically and durably.

    Raises :class:`StorageError` (never a bare ``OSError``) on failure so callers
    can catch the protocol-level type.  A unique temp file is created beside the
    target, fsync'd, then renamed into place.
    """
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.{uuid4().hex}.tmp")
    try:
        with tmp.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=ensure_ascii, indent=indent,
                      sort_keys=sort_keys)
            handle.write("\n")
            handle.flush()
            if fsync:
                os.fsync(handle.fileno())
        os.replace(tmp, dest)
    except (OSError, TypeError, ValueError, OverflowError) as exc:
        raise StorageError(f"failed to write {dest}: {exc}") from exc
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            # A leftover .tmp is harmless; never mask the real outcome.
            pass


def read_json(path: Path | str, *, default: Any = None) -> Any:
    """Read JSON from *path*, returning *default* on any read/decode error.

    Mirrors the historical ``_read`` contract: a missing or corrupt file is not
    fatal.  Prefer :func:`load_json_strict` when absence must be an error.
    """
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def load_json_strict(path: Path | str) -> Any:
    """Read JSON and raise :class:`StorageError` on missing or corrupt input."""
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise StorageError(f"cannot read {source}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise StorageError(f"corrupt JSON in {source}: {exc}") from exc


def write_text_atomic(
    path: Path | str,
    text: str,
    *,
    encoding: str = "utf-8",
    fsync: bool = True,
) -> None:
    """Write *text* to *path* atomically (same guarantees as JSON writes)."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.{uuid4().hex}.tmp")
    try:
        with tmp.open("x", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            if fsync:
                os.fsync(handle.fileno())
        os.replace(tmp, dest)
    except OSError as exc:
        raise StorageError(f"failed to write {dest}: {exc}") from exc
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def read_text(path: Path | str, *, encoding: str = "utf-8",
             default: str | None = None) -> str | None:
    """Read *path* as text, returning *default* on any error."""
    try:
        return Path(path).read_text(encoding=encoding)
    except OSError:
        return default
