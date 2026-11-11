#!/usr/bin/env python
"""Export the local UI profiles as distributable templates.

``coordination/ui_profiles/`` is gitignored on purpose, and
``docs/EXECUTION_PLANE_UPGRADE_PLAN.zh-CN.md`` already says what should happen
instead: move templates that carry *no PID, no token and no personal
conversation text* into ``examples/``, and let the target machine re-probe its
own anchors.  This script does that export.

A profile mixes two kinds of fact:

  * what is true about the *client* -- which control is the composer, whether
    the interject affordance exists and when it appears, which transport can
    reach it.  That knowledge was expensive to measure and is worth sharing.
  * what is true about *this machine and this person* -- the absolute path to
    the installed executable, a phrase that exists only in the target
    conversation, and the pid it was probed from.  In a public repository that
    is a disclosure, not a portability fix.

The local files stay local; this writes the portable half to
``examples/ui_profiles/<agent>.json`` with the machine-specific values replaced
by explicit placeholders.  Regenerating is a command, not a copy-paste, because
a template maintained by hand is a template that drifts away from the profile
it claims to describe.

Usage:
    python scripts/export_ui_profile_examples.py           # write
    python scripts/export_ui_profile_examples.py --check    # exit 1 if stale
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "coordination" / "ui_profiles"
TARGET = ROOT / "examples" / "ui_profiles"

# Replaced rather than removed: a missing key is a worse instruction than a
# value that says what to put there.  Both placeholders also fail closed --
# load_profile rejects a non-absolute exe, and a marker that matches nothing
# raises SESSION_MARKER_MISSING -- so a template cannot be used by accident.
PLACEHOLDERS = {
    "exe": "<REPLACE: absolute path to the client executable, read from the "
           "running process rather than guessed from the install layout>",
    "allowed_exe_roots": [
        "<OPTIONAL_REPLACE: absolute updater root for the same executable name; "
        "delete this key when the client has no versioned updater directory>"
    ],
    "session_marker": "<REPLACE: a phrase that exists only in the target "
                      "conversation, never in any other chat of this client>",
}

# Dropped: one-off probe records and the exporter's own bookkeeping.  They name
# a single moment and are not configuration.
DROP = {"exe_note", "evidence", "_note", "_fill_in_locally"}

FILL_IN = ["exe", "session_marker"]

# "no PID" is a requirement of the plan quoted above, and a pid is exactly the
# kind of detail that survives a well-meaning manual scrub.
PID = re.compile(r"\bpid \d+")


def scrub_value(value):
    if isinstance(value, str):
        return PID.sub("pid <local>", value)
    if isinstance(value, list):
        return [scrub_value(item) for item in value]
    if isinstance(value, dict):
        return {key: scrub_value(item) for key, item in value.items()}
    return value


def scrub(profile: dict) -> dict:
    fill_in = list(FILL_IN)
    if "allowed_exe_roots" in profile:
        fill_in.append("allowed_exe_roots")
    out: dict = {
        "_note": (
            "Template, not a working profile. Replace the values named in "
            "_fill_in_locally, then copy this file to "
            "coordination/ui_profiles/<agent>.json -- that directory is "
            "gitignored because it holds machine paths and conversation "
            "titles. Everything else here is a measured fact about the client "
            "and is safe to share."
        ),
        "_fill_in_locally": fill_in,
    }
    for key, value in profile.items():
        if key in DROP:
            continue
        if key in PLACEHOLDERS:
            out[key] = PLACEHOLDERS[key]
        else:
            out[key] = scrub_value(value)
    return out


def render(profile: dict) -> str:
    return json.dumps(scrub(profile), ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="exit 1 when a committed template differs from the profile")
    args = parser.parse_args()

    if not SOURCE.is_dir():
        print(f"no local profiles at {SOURCE}", file=sys.stderr)
        return 1
    TARGET.mkdir(parents=True, exist_ok=True)

    stale, written = [], []
    for path in sorted(SOURCE.glob("*.json")):
        profile = json.loads(path.read_text(encoding="utf-8"))
        agent = str(profile.get("agent") or path.stem)
        target = TARGET / f"{agent}.json"
        text = render(profile)
        if args.check:
            if not target.exists() or target.read_text(encoding="utf-8") != text:
                stale.append(target.name)
            continue
        target.write_text(text, encoding="utf-8", newline="\n")
        written.append(target.name)

    if args.check:
        if stale:
            print("stale templates: " + ", ".join(stale), file=sys.stderr)
            print("run scripts/export_ui_profile_examples.py", file=sys.stderr)
            return 1
        print("templates in sync")
        return 0

    for name in written:
        print(f"wrote examples/ui_profiles/{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
