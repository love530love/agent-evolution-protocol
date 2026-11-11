# Elevated desktop worker (v0.6.31 building block)

This is a deliberately narrow Windows execution-plane helper for the situation
where the target desktop client runs at a higher integrity level than the normal
coordinator.  Windows UIPI correctly blocks low-to-high UI injection; a normal
supervisor tick must therefore return `EXECUTOR_TOO_LOW`, not pretend it sent a
message.

The helper is not a second commander and not a general task scheduler.  It has
one job: invoke the existing commander for **one exact, already deposited
order** from an interactive highest-integrity token.

## Contract

`scripts/run-elevated-desktop-order.ps1` requires all of:

- exact `-OrderId` and exact `-EventId`;
- an explicit `-ReceiptPath` that is **exactly**
  `<CoordRoot>\\commander\\receipts\\elevated\\<OrderId>.json`;
- an existing `commander/inbox/<OrderId>.json` whose ID, event, and state are
  exactly `DEPOSITED`;
- `allow_launch: false` in that order; and
- `-Apply` plus an elevated interactive token for the actual call.

`ReceiptPath` is not a general output argument: an elevated worker rejects every
other path before it writes anything.  Its Python executable is likewise limited
to an existing repository-local AEP runtime (`.tools\\agent-runtime`,
`.tools\\desktop-sdk`, or `.venv`); a caller cannot provide an arbitrary exe to
the scheduled task.

It does not enumerate a queue, call `tick`, start a desktop client, create a
new conversation, or turn a visibility/ACK claim into a success.  The order is
checked once in PowerShell and again in the Python bootstrap immediately before
the exact existing `commander.dispatch(...)` call.  The commander's own locks,
original-session gate, transport receipt, and retry safeguards remain in force.

The external worker receipt is intentionally conservative:

```json
{
  "state": "EXECUTOR_STARTED",
  "executor_started": true,
  "delivery_success": false,
  "task_delivered": false,
  "agent_accepted": false
}
```

`EXECUTOR_STARTED` means only that a correctly elevated worker passed its
preflight and invoked the single-order commander.  Read `commander_result` and
the normal commander receipt to determine `INPUT_SUBMITTED`,
`MESSAGE_VISIBLE`, `AGENT_ACK`, or a later task-result readback.  A scheduled
task status is never evidence that any of those later states occurred.

## First live verification: probe-only

`-ProbeOnly` is a separate exact-order mode for the first elevated Qoder (or
other target) contact. With `-Apply`, it runs only:

```text
coord-uia observe --agent <order target> --ocr
```

from the interactive highest-integrity worker. It does not call
`commander.dispatch`, focus a window, type, send, launch a client, or create a
session. Its external receipt ends in `PROBE_COMPLETED` (or `PROBE_FAILED`),
never `MESSAGE_VISIBLE`, `AGENT_ACK`, or a delivery success claim. The UIA/OCR
report is embedded under `probe` in the UTF-8 executor receipt.

## Preview and registration

Preview first.  Neither command below sends anything or starts a client:

```powershell
$id = "20261111T000000000000Z-example"
$event = "EVT-EXAMPLE"
$root = "K:\PythonProjects5\agent-evolution-protocol\coordination"
$receipt = Join-Path $root "commander\receipts\elevated\$id.json"

pwsh -File K:\PythonProjects5\agent-evolution-protocol\scripts\run-elevated-desktop-order.ps1 `
  -OrderId $id -EventId $event -ReceiptPath $receipt -CoordRoot $root

pwsh -File K:\PythonProjects5\agent-evolution-protocol\scripts\run-elevated-desktop-order.ps1 `
  -OrderId $id -EventId $event -ReceiptPath $receipt -CoordRoot $root -ProbeOnly

pwsh -File K:\PythonProjects5\agent-evolution-protocol\scripts\register-elevated-desktop-order-task.ps1 `
  -OrderId $id -EventId $event -ReceiptPath $receipt -CoordRoot $root
```

After reviewing the task plan, explicitly register a per-order, on-demand task:

```powershell
pwsh -File K:\PythonProjects5\agent-evolution-protocol\scripts\register-elevated-desktop-order-task.ps1 `
  -OrderId $id -EventId $event -ReceiptPath $receipt -CoordRoot $root -ProbeOnly -Register
```

The registered task is intentionally `RunLevel Highest`, `LogonType Interactive`,
`MultipleInstances IgnoreNew`, and has a five-minute execution limit.  It has
no trigger and this helper never calls `Start-ScheduledTask`; an operator or a
future separately-audited dispatcher must start the exact reviewed task.

## Limits and next acceptance step

This building block cannot bypass a missing verified original-session profile,
an ambiguous window, a stale executable fingerprint, a busy composer, or a
target that does not echo its event ID.  It also cannot prove a delivery merely
because the process exited with code zero.

The acceptance sequence remains:

`INPUT_SUBMITTED -> MESSAGE_VISIBLE -> AGENT_ACK -> TASK_RESULT_READBACK`.

Use it only with an existing order that was already safe to send.  If the
order is no longer `DEPOSITED`, expired, mismatched, or has `allow_launch:
true`, the worker refuses before it invokes the commander.
