"""Single source of truth for collaboration identities whose names nest.

``workbuddy``, ``workbuddy3``, ``workbuddy_ai`` and ``workbuddyai`` are four
distinct mailboxes and four distinct agents.  They are *not* spelling variants:
a message addressed to one must never be silently delivered to another.  The
older hard-coded agent tuples in :mod:`bridge` and :mod:`cli` omitted the
``workbuddyai`` identity, so peers doing discovery saw only ``workbuddy`` and
addressed their receipts there.  This module owns the identity list and an
explicit no-alias rule, so the fix cannot drift back into fuzzy name matching.

Read-only and dependency-free: it never touches the filesystem, the network,
a client window or a model.
"""
from __future__ import annotations

# The identity this project uses in the local coordination area.
SELF = "workbuddyai"

# Deliberately separate identities.  Do not "normalise" these into one another.
WORKBUDDY_IDENTITIES = ("workbuddy", "workbuddy3", "workbuddy_ai", "workbuddyai")

# Explicitly empty: adding a mapping here would turn a wrong recipient into a
# silently accepted one.  A typo must fail, not be redirected to a mailbox that
# happens to share a prefix.
ALIASES: dict[str, str] = {}

# Legacy mailbox roster plus the WorkBuddy-family identities that were already
# live in ``coordination/messages/`` but missing from both hard-coded tuples.
LEGACY_BRIDGE_AGENTS = (
    "codex",
    "workbuddy",
    "workbuddy3",
    "workbuddy_ai",
    "workbuddyai",
    "qoder",
    "codebuddy",
    "github_copilot",
    "hermes_desktop",
    "autoclaw",
)


def canonical(value: str) -> str:
    """Trim and lowercase without applying any alias or prefix matching."""
    name = str(value or "").strip().lower()
    if not name:
        raise ValueError("agent identity is empty")
    return name


def is_same(left: str, right: str) -> bool:
    return canonical(left) == canonical(right)


def confusable_with(value: str) -> tuple[str, ...]:
    """Other WorkBuddy-family identities a human or agent might confuse it with."""
    name = canonical(value)
    return tuple(item for item in WORKBUDDY_IDENTITIES if item != name)


def describe(value: str) -> dict:
    """A read-only identity card suitable for diagnostics and peer replies."""
    name = canonical(value)
    return {
        "schema": "aep-agent-identity-v1",
        "identity": name,
        "is_self": is_same(name, SELF),
        "known": name in LEGACY_BRIDGE_AGENTS,
        "aliases_applied": [],
        "confusable_with": confusable_with(name),
        "mailbox": f"messages/{name}",
        "note": ("Exact identity only. workbuddy, workbuddy3, workbuddy_ai and "
                 "workbuddyai are distinct agents; no prefix or substring match "
                 "is used for addressing."),
    }
