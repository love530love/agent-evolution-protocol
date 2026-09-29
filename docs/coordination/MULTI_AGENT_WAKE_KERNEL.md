# Multi-Agent Wake Kernel v0.1

Date: 2026-09-29

This kernel turns a mailbox message into a real, auditable wake attempt without
polling a model.  It is deliberately small: `bridge.py` creates explicit wake
events, `wake_daemon.py` consumes them, and per-agent PowerShell adapters decide
how to start or nudge the desktop agent.

## What counts as wake

- `send`: mailbox only.  It never starts an agent.
- `wake`: explicit `wake_request`.  It can be consumed by the daemon.
- adapter execution: the daemon has attempted the target agent's local entrypoint.
- `claim` / `task-state`: the target agent has actually accepted work.

Do not treat a queued wake as proof that an agent is working.  The proof is a
later `claim` or `task-state phase=working`.

## Commands

Create a wake request:

```powershell
& .\.venv\Scripts\python.exe competition\coordination\bridge.py wake `
  --from codex `
  --to hermes_desktop `
  --task T108-R5U `
  --reason "browser extension reconnected; upload may continue" `
  --entrypoint competition\coordination\ONBOARDING_HERMES_DESKTOP_20260929.md
```

Consume once, for supervised execution:

```powershell
& .\.venv\Scripts\python.exe competition\coordination\wake_daemon.py --once
```

Audit without executing adapters:

```powershell
& .\.venv\Scripts\python.exe competition\coordination\wake_daemon.py --once --dry-run
```

Watch continuously without model-token polling:

```powershell
& .\.venv\Scripts\python.exe competition\coordination\wake_daemon.py --watch --interval 2
```

Inspect queue and recent audit log:

```powershell
& .\.venv\Scripts\python.exe competition\coordination\bridge.py wake-status
```

## Safety rules

1. Ordinary mailbox messages must not wake models.
2. A wake request must name target agent, task, reason, budget, and entrypoint.
3. High-risk actions still need current authorization or `human_required=true`.
4. The daemon has a short per-agent cooldown to prevent wake storms.
5. The daemon logs to `wake_log.jsonl` and moves consumed events into
   `wake_done` or `wake_failed`.
6. Adapters are the only place that may launch desktop apps, CLI tools, URI
   schemes, or window-control scripts.

## Current adapters and verified launch entries

- `wake_adapters\hermes_desktop.ps1`
  - executable: `F:\PythonProjects1\Hermes\hermes-agent\apps\desktop\release\win-unpacked\Hermes.exe`
  - protocol: `hermes://`
- `wake_adapters\workbuddy.ps1`
  - executable: `D:\Programs\WorkBuddy\WorkBuddy.exe`
  - protocol: `workbuddy://`
- `wake_adapters\qoder.ps1`
  - executable: `D:\Program\Qoder IDE\Qoder IDE.exe`
  - protocol: `qoder://`
- `wake_adapters\codebuddy.ps1`
  - executable: `D:\Program\CodeBuddy CN\CodeBuddy CN.exe`
- `wake_adapters\github_copilot.ps1`
  - executable: `D:\Programs\GitHub Copilot\github.exe`
- `wake_adapters\autoclaw.ps1`
  - executable: `D:\Programs\AutoClaw2\AutoClaw2.exe`
  - related entries detected: `D:\Programs\OpenClawTray\OpenClaw.Tray.WinUI.exe`, `D:\Program Files\QClaw\v0.2.37.630\QClaw.exe`

The adapters now dispatch to verified local executables or registered URI
schemes.  This wakes or focuses the desktop app, but it is not proof that the
agent accepted the task.  The acceptance proof remains a later `claim` or
`task-state phase=working` update by the target agent.

## Expected handoff flow

```text
bridge.py wake
  -> wake_queue/*.json
  -> wake_daemon.py
  -> wake_adapters/<agent>.ps1
  -> wake_log.jsonl
  -> target agent reads inbox/status/board
  -> target agent writes claim/task-state
```
