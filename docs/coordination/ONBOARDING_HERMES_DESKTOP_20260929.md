# Multi-Agent Onboarding: Hermes Desktop

Date: 2026-09-29
Sender: Codex

You are joining the local FlagGems multi-agent collaboration workspace.

Read these first:

1. `K:\PythonProjects5\FlagGems-sglang\competition\coordination\MULTI_AGENT_COLLABORATION_EVOLUTION.md`
2. `K:\PythonProjects5\FlagGems-sglang\competition\coordination\MULTI_AGENT_STATUS_BOARD.md`
3. `K:\PythonProjects5\AI-Browser-Bridge\AGENT-QUICKSTART.md`

Before taking work, run:

```powershell
& .\.venv\Scripts\python.exe competition\coordination\bridge.py status
& .\.venv\Scripts\python.exe competition\coordination\bridge.py board
& 'K:\PythonProjects5\AI-Browser-Bridge\agent.ps1' status
& 'K:\PythonProjects5\AI-Browser-Bridge\agent.ps1' tools
```

Rules:

- Check board and claims before acting. Do not duplicate or steal another agent's task.
- Write `task-state` for any active task: owner, phase, goal, evidence, next_action, blocked_reason.
- Use event/wake phrases, mailbox, or explicit user instruction. Do not run silent model polling.
- Browser automation must use AI Browser Bridge structured tools:
  - custom dropdowns: `browser_choose`
  - file upload: `browser_upload`
  - diagnostics: `browser_debug`
  - local advisory judge: `browser_local_judge`
- Do not use coordinates or ArrowDown/Enter as the primary path for structured controls.
- High-risk actions such as submit, upload, publish, delete, pay, push, merge, or approve require current authorization or `human_required=true`.
- Chrome local judge is advisory only. If `schemaValid=false`, `parseWarning` is non-empty, or `needsHumanConfirm=true`, treat it as not approved.

Hermes Desktop is registered in `bridge.py` as `hermes_desktop`.

