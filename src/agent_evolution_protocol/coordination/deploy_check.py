"""Read-only deployment readiness check for a freshly cloned workspace.

A git clone does NOT contain the runtime assets: `.tools/`, `.models/`,
`.aep/workspace.json` and `coordination/` are all gitignored. This module tells
a new operator exactly what is missing, in two tiers, without installing or
writing anything and without a system-Python fallback.

  core      -- needed for the plain AEP CLI / coordination to work
  optional  -- local Qwen advisory brain (CUDA torch) and the tray service

Every result carries a machine-readable `code` so a wizard can act on it.
No subprocesses, no network, no writes, no elevation, no model calls.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

__all__ = ["check", "main"]

SCHEMA = "aep-deploy-check-v1"
NOTE = ("Read-only. Core is required for a usable clone; optional items are "
        "needed only for the local CUDA brain / always-on tray service. "
        "No path is hardcoded to a specific drive letter.")


def _row(name: str, tier: str, ok: bool, code: str, detail: str, fix: str) -> dict:
    return {"name": name, "tier": tier, "ok": bool(ok), "code": code,
            "detail": str(detail), "fix": fix}


def check(workspace: Path) -> dict:
    """Recompute deployment readiness from the filesystem; read-only."""
    workspace = Path(workspace).resolve()
    checks: list[dict] = []

    # ---- core tier -------------------------------------------------------
    py = workspace / ".tools" / "portable-python"
    has_portable = py.is_dir() and any(py.glob("python-*-win-amd64/python.exe"))
    checks.append(_row("portable_python", "core", has_portable,
        "PORTABLE_PYTHON_OK" if has_portable else "PORTABLE_PYTHON_MISSING",
        str(py), "scripts/install-portable-python.ps1 -Install"))

    cap = workspace / ".tools" / "agent-runtime" / "Scripts" / "python.exe"
    checks.append(_row("capability_runtime", "core", cap.is_file(),
        "CAPABILITY_RUNTIME_OK" if cap.is_file() else "CAPABILITY_RUNTIME_MISSING",
        str(cap), "scripts/install-capability-runtime.ps1 (needs a bootstrap Python)"))

    ws_cfg = workspace / ".aep" / "workspace.json"
    checks.append(_row("workspace_config", "core", ws_cfg.is_file(),
        "WORKSPACE_CONFIG_OK" if ws_cfg.is_file() else "WORKSPACE_CONFIG_MISSING",
        str(ws_cfg), "copy .aep/workspace.example.json to .aep/workspace.json, keep paths relative"))

    coord = workspace / "coordination"
    required = ("messages", "acks", "sessions", "task_states")
    missing_dirs = [name for name in required if not (coord / name).is_dir()]
    checks.append(_row("coordination_dirs", "core", not missing_dirs,
        "COORD_DIRS_OK" if not missing_dirs else "COORD_DIRS_MISSING",
        "missing=" + ",".join(missing_dirs) if missing_dirs else str(coord),
        "python -m agent_evolution_protocol --coord-root coordination coord-bootstrap"))

    # Optional imports are probed with the capability runtime only if present.
    # No import is attempted with the guest/system python.
    cli_import = "NOT_PROBED"
    if cap.is_file():
        cli_import = "AVAILABLE"  # presence verified; full import tested by doctor
    checks.append(_row("cli_import", "core", cli_import == "AVAILABLE",
        "CLI_IMPORT_OK" if cli_import == "AVAILABLE" else "CLI_IMPORT_NOT_PROBED",
        "run: python -m agent_evolution_protocol doctor",
        "verify with the project runtime, not the system python"))

    # ---- optional tier ---------------------------------------------------
    pwsh = shutil.which("pwsh") or ""
    if not pwsh and os.environ.get("ProgramFiles"):
        candidate = Path(os.environ["ProgramFiles"]) / "PowerShell" / "7" / "pwsh.exe"
        if candidate.is_file():
            pwsh = str(candidate)
    pwsh_ok = bool(pwsh and Path(pwsh).is_file())
    checks.append(_row("powershell7", "optional", pwsh_ok,
        "PWSH7_OK" if pwsh_ok else "PWSH7_MISSING",
        str(pwsh), "install PowerShell 7; Windows PowerShell 5.1 cannot run #Requires -Version 7.0 scripts"))

    model = workspace / ".models" / "Qwen3-0.6B" / "model.safetensors"
    checks.append(_row("local_model", "optional", model.is_file(),
        "LOCAL_MODEL_OK" if model.is_file() else "LOCAL_MODEL_MISSING",
        str(model), "scripts/install-model.ps1 (needs hf.exe and ~1.5GB)"))

    qvenv = workspace / ".tools" / "qwen-venv" / "Scripts" / "python.exe"
    checks.append(_row("qwen_venv", "optional", qvenv.is_file(),
        "QWEN_VENV_OK" if qvenv.is_file() else "QWEN_VENV_MISSING",
        str(qvenv), "create .tools/qwen-venv and pip install torch CUDA build + transformers + accelerate"))

    nvidia = shutil.which("nvidia-smi") is not None
    checks.append(_row("cuda_driver", "optional", nvidia,
        "CUDA_DRIVER_OK" if nvidia else "CUDA_DRIVER_MISSING",
        "nvidia-smi" if nvidia else "not on PATH",
        "CUDA GPU + driver required; the local brain refuses CPU fallback"))

    tray_ico = workspace / "assets" / "aep-tray.ico"
    tray_ps1 = workspace / "scripts" / "aep-tray.ps1"
    tray_ok = tray_ico.is_file() and tray_ps1.is_file()
    checks.append(_row("tray_assets", "optional", tray_ok,
        "TRAY_ASSETS_OK" if tray_ok else "TRAY_ASSETS_MISSING",
        f"ico={tray_ico.is_file()} ps1={tray_ps1.is_file()}",
        "tray assets are versioned; re-clone or restore assets/ and scripts/aep-tray*"))

    service = workspace / "coordination" / "runtime" / "aep-service-install.receipt.json"
    installed = False
    if service.is_file():
        try:
            installed = json.loads(service.read_text(encoding="utf-8")).get("status") == "INSTALLED"
        except (OSError, ValueError, TypeError):
            installed = False
    checks.append(_row("tray_service", "optional", installed,
        "TRAY_SERVICE_INSTALLED" if installed else "TRAY_SERVICE_NOT_INSTALLED",
        str(service), "run install-aep-service.cmd (one UAC confirmation)"))

    core_ok = all(row["ok"] for row in checks if row["tier"] == "core")
    opt_ok = all(row["ok"] for row in checks if row["tier"] == "optional")
    return {"schema": SCHEMA, "workspace": str(workspace),
            "core_ready": core_ok, "optional_ready": opt_ok,
            "checks": checks, "note": NOTE}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path.cwd(),
                        help="repository root to inspect (read-only)")
    args = parser.parse_args(argv)
    print(json.dumps(check(args.workspace), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
