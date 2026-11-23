"""Bridge the two capability vocabularies used by cross-vendor handoff.

Why this exists
---------------
``coordination.role_contract`` declares capabilities as a *list* of vendor
neutral tokens (``desktop-ui``, ``research``, ``git-access``, ``packaging``,
``cli`` ...).  ``federation.command_handoff`` guards takeover with a *single*
required token per role (``command``, ``recon``, ``repo_intel``, ``build``).
The two vocabularies had no mapping, so a weak agent could not be judged
mechanically eligible to take over a role: an operator had to translate by
hand, which defeats "any agent can take over when a vendor hits its quota".

This module is a pure, deterministic adapter between them.  It:

* maps ``role_contract`` capability tokens to ``command_handoff`` tokens;
* derives the ``capability`` string that ``command_handoff.volunteer`` /
  ``claim`` expect from an agent's declared capability list;
* reports eligibility with an explicit reason, fail-closed.

Contract properties
-------------------
* **pure** -- no filesystem, network, clock, random or subprocess.
* **fail-closed** -- unknown role raises ``ValueError``; unknown capability
  tokens are ignored (never guessed into an eligibility).
* **recomputable** -- identical inputs yield byte-identical output; no
  wall-clock field, no random id.
* **consistent** -- the derived capability string is split with the same
  ``[, \\s]+`` rule as ``command_handoff._covers``, so a reported
  ``eligible=True`` is exactly what ``command_handoff`` will accept.
"""
from __future__ import annotations

import json
import re

from .command_handoff import DEFAULT_ROLES as _HANDOFF_ROLES
from ..coordination.role_contract import ROLES as _CONTRACT_ROLES

SCHEMA = "aep-capability-bridge-v1"

#: ``role_contract`` capability token -> ``command_handoff`` required token.
#: Only the four competition roles are mapped; ``cli`` is intentionally absent
#: because it is universal and is not a takeover capability by itself.
CAPABILITY_ALIASES = {
    "desktop-ui": "command",
    "research": "recon",
    "git-access": "repo_intel",
    "packaging": "build",
}

_TOKEN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ROLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_CAP_SPLIT = re.compile(r"[,\s]+")


def _role_index():
    index = {}
    for role in _CONTRACT_ROLES:
        index[role["id"]] = role
    return index


def _require_role(role_id):
    if not isinstance(role_id, str) or not _ROLE_ID.fullmatch(role_id):
        raise ValueError("INVALID_ROLE")
    if role_id not in _HANDOFF_ROLES:
        raise ValueError("UNKNOWN_HANDOFF_ROLE")
    return role_id


def _normalise_declared(declared):
    if not isinstance(declared, (list, tuple)):
        raise ValueError("declared capabilities must be a list")
    result = []
    for item in declared:
        if not isinstance(item, str) or not _TOKEN.fullmatch(item.strip()):
            raise ValueError("invalid capability token")
        token = item.strip()
        if token not in result:
            result.append(token)
    return result


def handoff_capability(role_id):
    """Return the single ``command_handoff`` token a role requires."""
    return _HANDOFF_ROLES[_require_role(role_id)]


def map_capabilities(role_id, declared):
    """Translate declared ``role_contract`` capabilities to a handoff string.

    Returns a dict with ``required``, ``tokens`` (the mapping result, ready to
    pass as ``capability`` to ``command_handoff``), ``mapped_from`` (source
    tokens that produced a required token) and ``ignored`` (unmapped tokens).
    Unknown capability tokens are ignored rather than treated as a match.
    """
    role_id = _require_role(role_id)
    required = _HANDOFF_ROLES[role_id]
    tokens = []
    mapped_from = []
    ignored = []
    for token in _normalise_declared(declared):
        target = CAPABILITY_ALIASES.get(token)
        if target == required:
            mapped_from.append(token)
            if target not in tokens:
                tokens.append(target)
        elif token == required:  # agent already speaks the handoff vocabulary
            mapped_from.append(token)
            if token not in tokens:
                tokens.append(token)
        else:
            ignored.append(token)
    return {
        "role": role_id,
        "required": required,
        "tokens": tokens,
        "capability": ", ".join(tokens),
        "mapped_from": mapped_from,
        "ignored": ignored,
    }


def eligibility(role_id, declared):
    """Judge whether an agent may take over ``role_id`` with ``declared``.

    ``eligible=True`` means ``command_handoff._covers`` will accept the derived
    capability string.  The verdict carries a stable ``reason`` token so an
    operator can audit *why* an agent was rejected.
    """
    mapped = map_capabilities(role_id, declared)
    eligible = mapped["required"] in set(_CAP_SPLIT.split(mapped["capability"]))
    return {
        "role": role_id,
        "required_capability": mapped["required"],
        "declared": list(_normalise_declared(declared)),
        "capability": mapped["capability"],
        "mapped_from": mapped["mapped_from"],
        "ignored": mapped["ignored"],
        "eligible": eligible,
        "reason": "CAPABILITY_COVERED" if eligible else "MISSING_REQUIRED_CAPABILITY",
    }


def describe():
    """Return the full, recomputable mapping table for audit and discovery."""
    roles = []
    contract = _role_index()
    for role_id in sorted(_HANDOFF_ROLES):
        handoff = _HANDOFF_ROLES[role_id]
        source = [token for token, target in CAPABILITY_ALIASES.items()
                  if target == handoff]
        declared = contract.get(role_id, {}).get("required_capability", [])
        roles.append({
            "role": role_id,
            "handoff_capability": handoff,
            "aliases": source,
            "contract_required_capability": list(declared),
        })
    return {"schema": SCHEMA, "roles": roles}


def __json_default(value):
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))
