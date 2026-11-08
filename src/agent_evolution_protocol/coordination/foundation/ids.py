"""Canonical identifier and digest helpers.

Pure, deterministic and stdlib-only.  Centralising these removes the N copies of
``now()`` / ``uuid4().hex`` / hand-rolled ``sha256`` that drifted across the
coordination tree and gives every durable record the same timestamp and order-id
shape.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone


def iso_now() -> str:
    """UTC timestamp in the same ISO-8601 shape the rest of the project uses."""
    return datetime.now(timezone.utc).isoformat()


def uuid_id() -> str:
    """A fresh 32-char hex identifier with no separators."""
    return uuid.uuid4().hex


def order_id() -> str:
    """A filesystem-safe, monotonic-ish order id (timestamp + short uuid)."""
    return iso_now().replace(":", "").replace(".", "").replace("+", "") + "-" + uuid_id()[:8]


def sha256_hex(data: str | bytes) -> str:
    """Hex SHA-256 of *data* (str is encoded as UTF-8 first)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()
