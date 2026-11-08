"""Foundational, dependency-free primitives for the coordination layer.

This package is the single, authoritative home for the cross-cutting helpers
that used to be copy-pasted into a dozen modules (atomic JSON writes, time/id
digests, and the exception taxonomy).  It is intentionally:

* **stdlib-only** -- nothing here imports another ``coordination`` module, so it
  can never participate in an import cycle and can be imported from anywhere.
* **side-effect free** -- importing it does not open files, spawn processes,
  touch the network, or load a model.
* **deterministic** -- given the same inputs the helpers return the same shape.

Every other module that carried its own ``_atomic``/``_read``/``now`` copy
should converge on this package so corruption handling, fsync policy and JSON
shape stay consistent project-wide.  See
``docs/ARCHITECTURE_REVIEW_20261108.zh-CN.md`` (finding V2).
"""

from .errors import (
    AEPError,
    ProtocolError,
    TransportError,
    VerificationError,
    StorageError,
    ClosedLoopError,
)
from .ids import iso_now, uuid_id, order_id, sha256_hex
from .io import (
    write_json_atomic,
    read_json,
    load_json_strict,
    write_text_atomic,
    read_text,
)

__all__ = [
    "AEPError",
    "ProtocolError",
    "TransportError",
    "VerificationError",
    "StorageError",
    "ClosedLoopError",
    "iso_now",
    "uuid_id",
    "order_id",
    "sha256_hex",
    "write_json_atomic",
    "read_json",
    "load_json_strict",
    "write_text_atomic",
    "read_text",
]
