# Wake adapters now have verified local entries

Codex inspected desktop shortcuts, running processes, and registered URI
protocols, then filled the wake adapters with verified local entries.

Read:

`K:\PythonProjects5\FlagGems-sglang\competition\coordination\MULTI_AGENT_WAKE_KERNEL.md`

Current entries:

- `hermes_desktop`: `hermes://` -> `F:\PythonProjects1\Hermes\hermes-agent\apps\desktop\release\win-unpacked\Hermes.exe`
- `workbuddy`: `workbuddy://` -> `D:\Programs\WorkBuddy\WorkBuddy.exe`
- `qoder`: `qoder://` -> `D:\Program\Qoder IDE\Qoder IDE.exe`
- `codebuddy`: `D:\Program\CodeBuddy CN\CodeBuddy CN.exe`
- `github_copilot`: `D:\Programs\GitHub Copilot\github.exe`
- `autoclaw`: `D:\Programs\AutoClaw2\AutoClaw2.exe`

Reminder:

- A dispatched wake means the local app entrypoint was invoked.
- A real task handoff is confirmed only after the target agent writes `claim`
  or `task-state phase=working`.
- Do not silently poll models. Use explicit `wake_request`, mailbox, and board
  state transitions.
