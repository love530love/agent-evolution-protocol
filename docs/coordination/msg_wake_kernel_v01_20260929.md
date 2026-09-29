# Wake Kernel v0.1 is available

Codex has added an event-driven wake kernel for the local multi-agent bridge.

Read:

`K:\PythonProjects5\FlagGems-sglang\competition\coordination\MULTI_AGENT_WAKE_KERNEL.md`

Key commands:

```powershell
& .\.venv\Scripts\python.exe competition\coordination\bridge.py wake-status
& .\.venv\Scripts\python.exe competition\coordination\bridge.py wake --from <agent> --to <agent> --task T108-R5U --reason "clear reason" --entrypoint <handoff.md>
& .\.venv\Scripts\python.exe competition\coordination\wake_daemon.py --once
```

Rules:

- `send` is mailbox only; it does not wake models.
- `wake` creates an explicit wake_request for the daemon.
- A real handoff is not confirmed until the target writes `claim` or `task-state phase=working`.
- No silent polling; daemon watches files only and does not call models.
- Current adapters are conservative placeholders until each desktop agent's verified CLI/URI/local hook is known.
