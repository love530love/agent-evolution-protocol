# Multi-Agent Session Affinity

Long-running agent work fails when every coordination message opens a fresh
desktop conversation.  Fresh windows lose the operational memory that matters:
site state, previous failed attempts, exact local paths, unresolved blockers,
and small user decisions.  They also clutter the human operator's workspace.

This protocol separates delivery from execution.

## Rules

1. Ordinary `send` writes to the mailbox only. It must not create a model
   session.
2. `wake` focuses or nudges the existing desktop app by default. It does not
   authorize a new conversation unless `allow_new_session=true`.
3. Each agent has a session record:
   - `mode`: `sticky`, `manual`, or `spawn-allowed`
   - `thread_id`: optional human-visible existing conversation identifier
   - `new_session_policy`: `forbid-by-default`, `allow-on-explicit-wake`, or
     `allow`
   - `handoff_style`: `digest-first`, `full-context`, or `minimal`
4. New windows are reserved for explicit user requests, dead existing sessions,
   or cleanly isolated tasks that would contaminate the current context.
5. A real handoff is proven only by `claim` or `task-state phase=working`.

## Recommended WorkBuddy policy

WorkBuddy should use:

```powershell
bridge.py session-set --agent workbuddy `
  --mode sticky `
  --new-session-policy forbid-by-default `
  --handoff-style digest-first `
  --notes "Use existing WorkBuddy conversation. Read digest/status/board before acting."
```

The WorkBuddy wake adapter honors this policy by default: it starts/focuses
`WorkBuddy.exe` instead of using a deep-link wake URI that may create a new
task window.  To deliberately create a new session, the caller must pass:

```powershell
bridge.py wake --to workbuddy ... --allow-new-session
```

## Digest-first handoff

Agents should read:

```powershell
bridge.py digest --agent workbuddy
bridge.py board
bridge.py status
```

The digest groups unread messages by topic and gives short previews.  The agent
can then request or inspect only the relevant full messages, reducing context
load while keeping continuity.

## Failure mode this prevents

Bad pattern:

```text
message arrives -> desktop app creates a new chat -> model lacks context ->
asks user for old details -> creates another window on next update
```

Preferred pattern:

```text
message arrives -> mailbox/digest updates -> existing session is focused ->
agent reads status board + digest -> claims/updates the task
```

