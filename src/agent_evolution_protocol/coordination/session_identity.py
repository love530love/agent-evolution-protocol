"""Parse and validate unique multi-instance Agent window identities."""
from __future__ import annotations
import re

PATTERN = re.compile(r"^(?P<agent>[A-Za-z0-9_-]+)-(?P<region>[A-Za-z0-9_-]+)-(?P<workspace>[A-Za-z0-9_-]+)-(?P<role>[A-Za-z0-9_-]+)$")

def parse(title: str) -> dict[str, str] | None:
    if not isinstance(title, str): return None
    match = PATTERN.fullmatch(title.strip())
    return match.groupdict() if match else None

def validate(title: str, *, agent: str, workspace: str, region: str | None = None) -> dict[str, object]:
    identity = parse(title)
    if not identity:
        return {"ok": False, "reason": "window title does not contain a unique instance identity"}
    checks = {"agent": identity["agent"].casefold() == agent.casefold(), "workspace": identity["workspace"].casefold() == workspace.casefold()}
    if region is not None: checks["region"] = identity["region"].casefold() == region.casefold()
    return {"ok": all(checks.values()), "identity": identity, "checks": checks}
