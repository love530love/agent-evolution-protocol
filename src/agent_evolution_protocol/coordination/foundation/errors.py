"""Single exception taxonomy for the protocol / coordination layer.

Historically each module raised ad-hoc ``ValueError`` / ``RuntimeError``
instances, so a caller could not reliably tell a contract violation apart from a
transient transport failure -- and ``except Exception`` catch-alls silently
swallowed both.  All protocol-level errors derive from :class:`AEPError` so
callers can catch the base class and still let genuine bugs (e.g. a
``NameError``) propagate.

These are pure and have no dependency on the rest of the package.
"""

from __future__ import annotations


class AEPError(Exception):
    """Base class for every raised, catchable protocol error."""


class ProtocolError(AEPError):
    """A message / envelope / contract violation (the input was malformed)."""


class TransportError(AEPError):
    """A desktop or network transport could not complete (no input attempted)."""


class VerificationError(AEPError):
    """An evidence check did not reach ``VERIFIED`` and must not close a task."""


class StorageError(AEPError):
    """A persistent read or write failed (see :mod:`foundation.io`)."""


class ClosedLoopError(AEPError):
    """An operation would duplicate a send or violate the no-replay fence."""
