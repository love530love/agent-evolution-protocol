"""Vendor-diverse coalition planning under quota and time limits.

The package is deliberately a read-mostly layer above presence, capability
declarations, the optional command-handoff ledger and the real delegation
mailbox.  It answers one question: *who can keep this role alive when the
current holder runs out of quota, time or presence?*

Planning never dispatches.  A takeover is a separate, explicitly authorised
call that writes a durable handoff record and uses the existing
``team_dispatch`` mailbox/wake path.  A queued wake, a mailbox deposit and an
agent ACK remain three different evidence levels.
"""

from . import capacity, dispatch, planner, spec

__all__ = ["capacity", "dispatch", "planner", "spec"]
