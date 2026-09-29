# Multi-Agent Status Board

Date: 2026-09-29

This is the minimal operational layer above the existing mailbox and claim system. It does not wake models by itself and does not replace explicit task ownership.

## Purpose

Every active task should have a single visible row with:

- owner
- phase
- goal
- evidence
- blocked reason
- next safe action
- wake phrase
- whether human input is required

This prevents agents from guessing from old chat context or starting duplicate browser workflows.

## Commands

Set or update a task row:

```powershell
& .\.venv\Scripts\python.exe competition\coordination\bridge.py task-state `
  --agent codex `
  --task T108 `
  --phase working `
  --goal "Upload and verify Task 108 package" `
  --evidence "browser bridge 0.3.6 connected" `
  --next-action "Use browser_choose then browser_upload; no coordinate fallback"
```

Show the board:

```powershell
& .\.venv\Scripts\python.exe competition\coordination\bridge.py board
```

Show one owner's rows:

```powershell
& .\.venv\Scripts\python.exe competition\coordination\bridge.py board --agent workbuddy
```

The existing `status` command now includes claims, board rows, and unread counts.

## Phases

Allowed phases:

- `todo`
- `claimed`
- `working`
- `waiting`
- `blocked`
- `needs-review`
- `ready`
- `done`
- `abandoned`

Use `waiting` when an agent is intentionally quiet until an event or user action. Use `blocked` only when no safe next action exists.

## Rules

1. A claim owns execution; a board row explains the current state.
2. A wake phrase is a request path, not automatic permission.
3. Browser writes must still obey AI Browser Bridge rules.
4. High-risk actions need explicit current authorization or human_required=true.
5. Do not run silent polling watchers. Prefer mailbox events, wake phrases, and explicit user actions.

