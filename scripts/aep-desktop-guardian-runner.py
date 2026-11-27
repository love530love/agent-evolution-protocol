"""Direct base-Python entry point for the scheduled Desktop Guardian task.

The project runtime's ``Scripts/python.exe`` is a Windows virtual-environment
redirector.  Task Scheduler can stop that redirector while leaving the real
base-Python Guardian alive.  This runner is invoked by the resolved base
interpreter instead, so the scheduled task owns the actual Guardian process.
It restores only this project's source and venv site-packages; it is not a
general-purpose environment launcher.
"""
from __future__ import annotations

import site
import sys
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
SOURCE = REPOSITORY / "src"
SITE_PACKAGES = REPOSITORY / ".tools" / "agent-runtime" / "Lib" / "site-packages"

if SOURCE.is_dir():
    sys.path.insert(0, str(SOURCE))
if SITE_PACKAGES.is_dir():
    site.addsitedir(str(SITE_PACKAGES))

from agent_evolution_protocol.coordination.resident_guardian import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
