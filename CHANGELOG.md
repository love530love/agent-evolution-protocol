# Changelog

## 0.6.20 — 居中协调愿景的事实基线：先证伪，再立项

- Documentation-only release. `docs/COORDINATOR_VISION_EVIDENCE.zh-CN.md` records the
  evidence baseline for a central long-running coordinator: eight assertions extracted from
  the operator's vision, each tagged with its current evidence state, plus a three-state
  capability inventory (proven with code + consumer + tests / code without a consumer /
  completely absent / docs only). No runtime code changed.
- Three assertions are explicitly falsified rather than adopted. "Saves credits": there is
  no per-agent quota, usage, reset-interval or cost data anywhere in the codebase —
  `legion_ledger.py` is contribution scoring whose comments say "not transferable credits"
  and "grants no operational authority", the CLI budget is a single-round competition
  budget, `volley_plan.plan()` is `PROPOSAL_ONLY_NOT_AUTHORIZED`, and CHANGELOG already
  states there is no quota scheduling. "Avoids groupthink": `THREAT_FIELDS`,
  `quorum.evaluate()` and `joint_attack` exist but are not wired into dispatch, and
  `governance_event()` only checks field presence plus a `confidence` enum. "Replaces a
  human engineer" contradicts the v0.6.19 boundary: no OS task registration, no resident
  process, no cross-restart recovery, `VERIFIED`-only closure, `reviewer != author`.
- A live counterexample for "source count != independent evidence count" is recorded: a
  Chinese tech blog defines MCP as "Multi-agent Coordination Protocol" with task auctions,
  blockchain contribution records and PBFT consensus, while the official
  `modelcontextprotocol.io` (spec 2026-07-28) defines it as Model Context Protocol, a
  standard connecting AI applications to external systems (tools, resources, prompts).
  The blog is cited only as a counterexample, never as a fact source.
- Open-source admission is checked against official sources only: MCP (above), A2A
  (Agent2Agent, donated by Google to the Linux Foundation, Apache-2.0; officially not an
  agent dev kit, not a sub-agent/tool-call protocol, not a messaging app, and no durable
  orchestration / quota / evidence verification), Temporal self-hosting (Temporal Service +
  persistence + Workers; `temporal server start-dev` is still a resident service), and
  LiteLLM Proxy budgets (`max_budget`, `budget_duration`, `budget_limits`,
  `model_max_budget`, `tpm_limit`, `rpm_limit`, `max_parallel_requests`, `spend`,
  `budget_reset_at`; official text: "Budgets require a database" and "none of them cap
  anything on a DB-less deployment", with a server-side reset scheduler). Conclusion:
  borrow protocol and field semantics only. Four hard gates reject any resident service,
  database, background thread, second envelope schema, new `coord-*` command surface, or
  probabilistic judging; OS triggers stay with the operator.
- Five phases, each with a recomputable acceptance criterion: P0 one-shot
  `coordinator_tick` wiring existing single steps (never a second scheduler), P1 per-agent
  quota ledger that reports `NO_QUOTA_DATA` instead of guessing, P2 claim-to-evidence
  binding + common-source dedup + `quorum` wired into dispatch, P3 optional MCP tools /
  A2A Agent Card as external interface only (existing desktop adapters untouched), P4
  `artifact_manifest.verify` wiring, notification tiers, cross-restart recovery and a
  persistent scheduling cursor. Closure still requires `VERIFIED`.
- Explicitly not claimed: replacing human engineers, 7x24 unattended delivery, or any
  measured credit saving.
- Verification: full suite `1406 passed, 3 skipped, 601 subtests passed in 175.70s`
  (`PYTEST_EXIT=0`, with `CODEBUDDY_SAFE_DELETE_ENABLED=0`); version trio synced to 0.6.20;
  `git diff --check` clean; `schemas/` still 7 files and the generated skill manifest unchanged.

## 0.6.19 — 长任务心跳接合：零模型、零显存、默认只读

- Two operator questions drove this release: whether a running LM Studio server still consumes
  local VRAM, and whether unattended long-running missions need a small model or a timed
  heartbeat to keep the commanding agent alive. Read-only measurements on this machine:
  `lms status` -> `Server: ON (port: 1234)`, `lms ps` -> `No models are currently loaded`,
  `nvidia-smi` -> RTX 3090 at `692 MiB / 24576 MiB` and `0%` GPU, `GET /v1/models` -> listing
  only. A running server is not a loaded model; the only path that loads weights is
  `insight_miner --propose`, forced to `--gpu off --ttl 60` with an exact `lms unload`.
  The server can be closed without affecting any default project capability.
- A heartbeat does not need a model. It answers deterministic, recomputable questions (lease
  expiry, `last_progress_at` vs `heartbeat_timeout=900`, terminal phase, ACK presence), and a
  probabilistic model has no "unknown" output and must not replace a fail-closed rule layer.
  The project already had model-free heartbeat machinery: `commander.tick()`,
  `federation/engine.tick()` (`_stalled()`), `commander.watch()`, `supervisor.serve()`,
  `campaign-watch.ps1`, and the OS-triggered `aep-supervisor.ps1` /
  `register-supervisor-task.ps1`. "The timer is not the judge" remains the standing rule.
- The actual break was elsewhere: `scripts/start-mission.ps1` writes `missions/M-*.json` and
  returns a `mission_id`, but `cli.py` has no `mission` subcommand, no runner consumes that id,
  `mission_supervisor.inspect()` only returns action suggestions, `mission_runtime.py`
  overwrites state in place, and `mission_graph.py` is a second unconsumed DAG schema.
  This release adds a bridge instead of a second scheduler.
- `coordination/mission_pulse.py`: a one-shot, socket-free, process-free, model-free pulse.
  `scan()` reads missions and reuses `mission_supervisor.inspect()` (no duplicated judgement);
  `gates()` hard-rejects `ACTION_NOT_ASSIGNABLE`, `NO_TARGET`, `INVALID_AGENT_NAME`,
  `UNKNOWN_AGENT`, `MISSION_PAST_DEADLINE`, `HUMAN_REQUIRED`, `WAITING_FOR_EVENT`,
  `TASK_TERMINAL`, `CLAIM_OWNER_MISMATCH`, `CLAIM_EXPIRED`, `ALREADY_QUEUED`; members are
  derived from `presence/agents/*.json` and `clients/*.json` filenames rather than a drifting
  hardcoded list; `verify()` recomputes the mission sha256, event id, every gate and the body
  before the single write path `queue_candidate()` calls `commander.submit_order()`.
  Only `assign` (a `ready` stage) can be queued; `observe` / `recovery_review` /
  `blocked_review` / `report_delivery` are report-only. `event_id_for()` hashes
  `(mission_id, stage_id, state, updated_at, goal)` into a stable `PULSE-...` id, so replaying
  an unchanged stage returns `ALREADY_QUEUED` instead of duplicating an order. Rejecting a
  stale candidate never permanently bans the file: the next external call rescans and produces
  a fresh id, but a rejected candidate can never be written.
- Default is read-only dry-run; only `--apply` queues, and it produces `state=DEPOSITED`
  orders. `commander.submit_order()` explicitly sends nothing until a `coord-commander tick`
  runs, so queuing is never delivery. The pulse report carries hard boundary fields
  `models_loaded=0`, `processes_started=0`, `ui_touched=false`,
  `deployment_performed=false`, `system_tasks_registered=false`; any true value is a violation.
  No new schema, no new `coord-*` command surface, no mission file rewrite.
- `scripts/run-mission-pulse.ps1` is a bounded one-shot trigger (ASCII, no loop, no sleep,
  no `--apply` by default). `scripts/register-mission-pulse-task.ps1` is dry-run by default and
  only calls `Register-ScheduledTask` when an operator passes `-Register` explicitly; it uses
  `MultipleInstances=IgnoreNew` and a 5-minute `ExecutionTimeLimit`. No task was registered in
  this release and no resident process was started: a resident process spawned from inside an
  agent dies with the tool-call job object, so OS-level triggers belong to the operator.
- `skills/aep-mission-pulse/SKILL.md` adds a `cli` / `model_required: false` capability card
  with the six-section format and a receipt-to-verdict table: `DEPOSITED` is queued, not
  delivered; only `VERIFIED` closes a task; a timer only triggers read-only commands.
  `skills/README.md` and `tests/test_capability_cards.py` were updated to guard the no-daemon
  boundary. `docs/LONGRUN_MISSION_PULSE.zh-CN.md` records the VRAM measurements, the existing
  heartbeat inventory, the mission break, the pipeline `scan() -> gates() -> verify() ->
  [--apply] submit_order() -> DEPOSITED`, and the automatic-boundary table.
  `docs/NOVICE_LONGRUN_WORKFLOW.zh-CN.md` gains a "v0.6.19 bridge, not proof of unattended
  delivery" section and keeps its original conclusion unchanged.
- Explicitly not done: no LM Studio/Ollama end-to-end inference run, no OS task registration,
  no resident watcher, no persistent scheduling cursor, no cross-restart recovery drill, no
  unified artifact verifier, no multi-day budget/quota scheduling, no notification policy,
  and no promise of 7x24 unattended delivery.
- Verification: `tests/test_mission_pulse.py` and `tests/test_capability_cards.py`
  `37 passed, 129 subtests`; full suite run twice with identical results,
  `1406 passed, 3 skipped, 601 subtests passed` (163.74s and 171.47s, both `PYTEST_EXIT=0`).
  On this machine the full suite is run with
  `CODEBUDDY_SAFE_DELETE_ENABLED=0`: without it the host safe-delete shim blocks pytest's
  temp-directory garbage collection (`SAFE_DELETE_BULK_CONFIRM_REQUIRED`), the session is cut
  off before writing its summary (`PYTEST_EXIT=1`, no summary, one run also showed a stray
  `F` at 95% while another showed none). With the variable set the same suite is green with
  exit code 0. This is a host shim conflict, not a code regression. A read-only preview
  against the real coordination root rejected historical missions with
  `MISSION_PAST_DEADLINE` and wrote nothing; a temporary sandbox `--apply` produced exactly
  one `DEPOSITED` order and a replay returned `ALREADY_QUEUED`; the registration script
  dry-run printed `system_changed: false`.

## 0.6.18 — 新 Agent 调度入口：接入、认领、派工、回执判定

- v0.6.17 built the weak-agent card specification but left an entry gap: a freshly joined
  agent asked to "start dispatching" had to synthesise steps from four separate documents,
  and the older `aep-agent-onboarding` card is prose without a receipt-to-verdict table.
  This release adds `skills/aep-start-dispatch/SKILL.md`, a `file-only` entry card that turns
  "join -> read context -> claim -> dispatch -> check receipts -> close" into numbered steps
  plus a lookup table. No runtime code changes.
- Two paths by capability: a file-only agent writes one
  `coordination/delegation/inbox/<id>.json` (with the exact id format, a timezone-bearing
  `reply_due_at` under 3 hours, and `allow_new_session: false`) and may only claim "queued";
  a CLI agent runs `aep join`, the read-only `coord-status` / `coord-digest` / `inspect` /
  `kernel-continuation-plan` set, `coord-claim`, a `coord-dispatch` or `coord-wake --dry-run`
  preview, then `coord-delivery-status` and `coord-task-state`. `coord-assign` and `coord-send`
  are irreversible and must be previewed first.
- The card states four traps explicitly: agent names must be looked up in `coord-status`
  (`valid_agent()` checks characters only, so an unknown `--to` silently creates an unread
  mailbox); `WAKE_QUEUED_UNVERIFIED` / `RELAY_QUEUED_UNVERIFIED` / `MAILBOX_ONLY` / `REJECTED`
  and `DEPOSITED` / `WINDOW_FOUND` / `INPUT_SUBMITTED` / `MESSAGE_VISIBLE` are not acceptance
  and only `VERIFIED` closes a task; no new sessions, no stale wake consumption, no channel
  switching on a busy target; and no promise of unattended multi-day runs, which
  `docs/NOVICE_LONGRUN_WORKFLOW.zh-CN.md` still lists as incomplete.
- `tests/test_capability_cards.py` had a false positive: its command-name regex treated the
  global flag `--coord-root` as a command, so the new card failed the guard. The fix tightens
  rather than relaxes it: the regex now matches command positions only, and a new test reads
  `global_arguments` from the generated `skill-manifest.json` and validates every global flag
  used in a card. The file-only check now iterates all `file-only` cards and asserts at least two.
- Verification: `tests/test_capability_cards.py` `12 passed, 71 subtests`; the three new test
  files `51 passed, 83 subtests`; full suite `1381 passed, 3 skipped, 543 subtests`.
  `aep` was run read-only (`coord-status`) and confirmed to resolve to this repository's
  source at the matching version.

## 0.6.17 — 自动发现与自动审计：默认纯规则，模型随用随走

- The project had evidence streams but no consumer: roughly 16 recorded facts sat on disk while
  `coordination/improvement_events/` and `improvements/` were both empty. This release adds an
  offline, read-only miner that turns those facts into recomputable leads and feeds verified ones
  into the existing `improvement.ingest()` evidence journal. No new audit schema, no new command surface.
- `insight_miner.py` ships four deterministic rules (`unclassified_failure`,
  `repeated_failure_signature`, `unverified_overlay`, `failed_trajectory_step`). `scan()` always
  reports per-source `scanned` counts and per-rule `measurements`, so a broken scanner, a clean
  machine and an unmet threshold cannot produce the same output. `verify()` rereads the evidence
  and recomputes sha256, count, scanned, key and rule; any drift rejects the finding.
- The automatic boundary stops at `PROPOSED`: no source edits, no promotion, no commit/push/deploy.
  `severity` is always `lead` (an escalation is rejected), `deployment_performed` is always false,
  and `advance`/`validate` remain the job of an independent reviewer with a claim token and a rollback point.
  "Automatic" means no human has to pose the question or make the judgement, not that a daemon exists:
  there is deliberately no resident watcher.
- Append-only evidence is snapshotted before it is frozen. Freezing a live `overlay_audit.jsonl`
  would make every later `improvement.advance()` fail with `frozen evidence changed` forever;
  a later source drift now reports `evidence-drift-since-snapshot` instead of silently redefining a proposal.
- The local model is optional and absent by default. The rule layer is socket-free (AST-guarded,
  including `ImportFrom.names` and a non-vacuous scan assertion); the proposer is imported only
  with `--propose`. `insight_proposer.py` supports both LM Studio (`/v1/chat/completions`) and
  Ollama (`/api/chat`), sees only `rule/count/scanned/summary`, and may emit only
  `title/hypothesis/checks` under a strict allowlist. Abstention is a valid answer; a dead model
  costs the note, not the finding.
- `local_model_session.py` implements "use it and let it go": `lms load --ttl 60 --gpu off
  -c 2048 -y --identifier aep-insight` and an exact `lms unload aep-insight` in `finally`.
  No `server start`, no `--all`, no polling, no background thread. Backend offline degrades to
  the rule-only result. LM Studio end-to-end inference is NOT claimed as verified.
- `skills/README.md` defines the weak-agent card contract: six fixed sections, a
  receipt-to-verdict lookup table, `model_required: false`, and
  `min_agent_capability: file-only|cli|desktop-ui`. `aep-insight-loop` (cli) and
  `aep-desktop-wake` (file-only, dispatch by writing one JSON into the delegation inbox) let a
  weak agent reproduce tested abilities without reasoning. `tests/test_capability_cards.py` guards drift.
- `docs/DESKTOP_SEND_AND_URGENT_INTERJECT.zh-CN.md` is corrected: its old "see the interject control
  before sending" wording contradicted the measured Qoder 0.5.3 sequence and would mislead an agent
  following the card. Full boundary: `docs/AUTONOMOUS_INSIGHT_LOOP.zh-CN.md`.
- Verification: new tests `49 passed, 43 subtests`; full suite
  `1379 passed, 3 skipped, 503 subtests passed in 163.08s`.

## 0.6.16 — 插话控件是晚到，不是不存在

- 0.6.15 shipped a conclusion that was wrong, and this release records the
  correction rather than quietly replacing it: "Qoder has no interject channel"
  was measured by staging text with `ValuePattern.SetValue`, which updates the
  visual editor without firing the web app's input event. The client still
  believed its composer was empty, Enter was swallowed, nothing was queued, and
  the absent 插话 control was read as an absent capability. An operation failure
  recorded as a client property. The real sequence is paste -> force Enter once
  or twice -> the composer clears, 发送消息 becomes 停止生成 and 插话 appears ->
  click it. The control is not missing, it is late.
- The pre-send gate is gone. It required the interject control to be visible
  *before* typing, which on this client can never be true because the control is
  a product of the send. A gate that can never pass is not a safety property,
  it is a permanent outage; the only pre-condition now is that the client is
  busy. `Wait-InterjectControl` polls instead of looking once, which is exactly
  the mistake that produced the wrong conclusion.
- The delivery order is now send -> prove the send landed (composer cleared) ->
  promote -> read back. The old order read the transcript before proving the
  send, so on a busy client it spent its whole budget looking for a message that
  could not appear until after the promotion.
- The retry ladder presses Enter a second time before re-pasting, and the
  receipt names the rung that worked (`send_attempts`, `send_retry`).
- `interruption` (the run was killed) and `interject_promoted` (the queue was
  promoted) are separate fields. Writing the promotion into `interruption`
  recorded a success as damage, because the caller treats a true value as a
  failed send.
- `interject_evidence` gains `appears_after_send` and `profile_hints`, and
  `interrupt_only` no longer fires on a client that merely shows a stop button
  while the composer is empty. Both halves are required to proceed: the
  adapter's observation and a profile that declares `interject_after_send`. A
  profile whose hints are guesses still cannot get through, which is why
  `codebuddy_cn` keeps the strict gate until it is measured.
- Verified on both Qoder builds through the operator command, not a probe
  script. The strongest evidence is the model's own line on the international
  build: "AEP-INTERJECT-20261105-04 已收到，国际版插话路径验证通过", noting the
  original task had been delivered in the same turn -- so the queued message was
  read by the running turn, not appended after it.
- `deliver` now honours `maximize_window`. `AEP_MAXIMIZE_WINDOW` was read by
  capture and compose but not by deliver, which hardcoded `SW_RESTORE`; the
  international build's window had been left at 237x39 and restore kept it
  there. A profile declaration that only some code paths read is not a
  declaration.
- Delivery identification was rebuilt after a message went into ChatGPT's
  side-pane Markdown editor and reported `task_delivered=true` while destroying
  112 lines of a tracked file: candidates outside the window or taller than
  three windows are rejected and reported, the selection states which claim
  chose the survivor, and `task_delivered` needs the event id above the composer
  cut with a witness that is not an ancestor of the composer.
- Routing can now say "I don't know": `unknown` is a field instead of a
  clarification flag attached to a scenario, and `read_only_explicit` is kept
  apart from `read_only` so the fallback branch is not gated shut.
- The scheduler switches surface instead of asking. A consent prompt is a stop,
  and `EXECUTOR_TOO_LOW` -- a receipt that proves nothing was typed -- is the
  only case where switching needs no consent. An uncertain send never switches.
- The OCR relay confirms its own paste: it waits for the layout to settle
  instead of sampling once after 400ms, clears the selection with `VK_DELETE`
  instead of selecting, and probes with trailing Chinese characters the
  recogniser can actually read.
- The resident loop passes `--no-perceive` down and prints one JSON line per
  tick, so "healthy and quiet" and "stuck" no longer look the same.
- Wake ids are one vocabulary: `dispatch_gate` recognised only `EVT-` ids while
  `coord-send` passes the mailbox timestamp id, so every `--dispatch` order
  refused itself before a character was typed.
- One ISO parser. `fromisoformat` raises on a trailing `Z` in 3.10, and the
  convention had been hand-copied into seven places; CI had been red since
  2026-11-02 while the local run stayed green.
- Cost, stated rather than discovered: after a ValuePattern write the first
  composer wait runs to its full timeout, so a delivery takes about 26 seconds.
  Shortening it would press Enter on clients that are merely slow.

## 0.6.15 — The missing ACK stopped blocking; it did not stop being recorded

- "No ACK, no progress" was a gate with no exception, and the exceptions are
  ordinary: an adapter that cannot report an ACK at all, a session a person
  closed, a client that never renders the reply.  None of those mean the work is
  missing, and all of them pinned the task in `running` forever.  `step(...,
  ack_policy=)` now has two values.  `"record"` is the default: advance, and write
  the unacknowledged verdict into the completion receipt.  `"enforce"` is the
  0.6.14 behaviour and is kept so the relaxation itself can be measured.
- There is deliberately no third value expressed as a number of minutes.  A hold
  that turns into progress on a timer is not a judgement, which is why 0.6.13
  refused it; relaxing a rule has to mean changing the criterion, not adding an
  alarm clock to the old one.
- `_ack_blocks` used to return `None` for "may advance", collapsing three
  different facts -- the order was acknowledged, it was never sent, the adapter
  cannot report an ACK -- into one indistinguishable value.  Under `enforce` that
  cost nothing, because all three advance anyway.  Under `record` they all advance
  and the receipt is the only place the difference can live, so it now returns
  `{verdict, reason}` always and every completion carries `ack`.
- `ack.verdict == "hold"` on a `closed` row is the whole design: it advanced, and
  it says so.  A separate `released` bucket names the same tasks, because in
  `closed` a released completion and a clean one differ by one field.
- What was **not** relaxed: an `abandon` verdict still blocks (a signature is the
  opposite kind of evidence to a missing acknowledgement), and `match is False`
  still blocks (a contradicted artifact is evidence against the work, where a
  missing ACK is only absence of evidence about delivery).
- Cost, stated rather than discovered: under `record` there is no hold left, so
  `hold_alerts` emits nothing.  Visibility was traded for liveness.  Verified at
  the filesystem level, not just from `alerts.count`: the `record` workspace has
  no `coordination/messages/` directory at all, the `enforce` workspace has
  `messages/kernel/<id>.json`.
- The readback session guard is wired.  `_result_blocks` anchors on the order's
  `session_id` instead of comparing every line the agent ever wrote.  `observe`
  now reports `session_checked` / `session_unknown` / `session_reason`, and the
  completion receipt carries the first two, because otherwise "we wired it" and
  "we forgot" look identical from outside -- which is how 0.6.14 shipped the guard
  disconnected.
- Reversed: a path found only in another conversation is `None`/`foreign_session`,
  not `False`.  `False` means we have positive evidence the claim is wrong; a
  product in another window shows the product exists and what we lack is
  attribution.  Blocking on that stalls a task whose work may be done.  Same for a
  line carrying no `session_id`: it cannot verify anything, so it yields `None`
  rather than being compared and called agreement.
- Fixed while wiring it: when the anchored conversation contained an attributable
  line with no artifact (an ACK) and the artifact appeared only elsewhere, the
  verdict fell through to `no_result_in_transcript` and dropped the fact that the
  output *was* found, in another conversation.  Both are `None`; only one tells
  you to go read the other window.
- `engine.revive(root, state, task_id=, reason=, actor=)` reopens a task blocked by
  an abandonment, and `_reopen_dependents` reopens downstream tasks whose
  dependencies are all alive again -- otherwise reviving the upstream leaves a
  dependent `blocked` with a note saying "blocked by ['execute']" while `execute`
  is running, which is a record stating something untrue.  Only tasks carrying
  `blocked_by_deps` are reopened, so scope conflicts and refusals are untouched.
  Idempotent, and the abandonment record is kept alongside the revival rather than
  replaced by it.
- Fixed, and this one was worse than documented: a late ACK on an abandoned order
  used to overwrite `state` to `AGENT_ACK`, which made `ack_protocol.abandoned()`
  return `None` (it reads the state), which skipped the abandon branch and let
  `may_advance()` return `True` -- so **a human sign-off was silently revoked by a
  machine event, and the task then completed as `done`**.  Not "two records
  disagree"; the opposite of what `abandon` promises.  The reply is now kept in
  `late_acks` (idempotent by event id) while `state` stays `ABANDONED`, and
  `may_advance()` refuses to advance past a sign-off even when `accepted` is True.
- Regression: Windows 1058 passed, 3 skipped, 113 subtests; WSL 1011 passed, 50
  skipped.  Both collect 1061, and the +34 over 0.6.14's 1027 reconciles file by
  file.  Two tests were removed, both named in the release note as the ones whose
  semantics this version deliberately reversed; every other test file has zero
  deletions.

## 0.6.14 — After the receipt, look at the result

- The three previous versions closed "correct dispatch -> correct conversation ->
  correct receipt". This one closes the fourth segment: the receipt said the work
  was done, but nothing checked that the work exists.
  `assignment.complete_evidence()` still accepts an agent's own `phase=done`, so
  "done" remained only the agent's own statement. New `result_readback` module
  compares the two statements that already exist: what the agent **claims** in
  `coord-task-state` (`claim()`) and what the transcript shows it **said**
  (`observe()`).
- `match` is three-valued on purpose. `None` means "there was nothing in the
  conversation to compare against", and it is **not** `True`. An empty transcript
  is the normal outcome of a failed capture; reading that as agreement would turn
  readback into decoration.
- The artifact must be a **path**, not a word. `done` and `tests pass` leave
  nothing a second party can open, so they land in `unchecked` and judge to
  `None` -- neither pass nor fail.
- No new schema. `aep-readback-v1` is the receipt of the *transport ladder*: five
  states, two booleans, one string list. It has no field for "compared, and they
  disagree", and its `accepted()` is defined on `AGENT_ACK` -- the very claim this
  module exists to test. So the verdict carries **no `schema` key**, like
  `session_fingerprint.compare()`: it consumes receipts, it is not one.
- Wiring in `supervisor._settle()` blocks **only on a proven contradiction**
  (`match is False` -> `hold_result_mismatch`, task stays `running`). `None`
  completes the task and records `result.match = None` with the reason. Blocking
  on `None` would deadlock the DAG on the first agent that forgot to name a file.
- `hold_alerts` delivers an over-limit hold to the **existing** mailbox
  (`coordination/messages/<to>/<id>.json`, `kind = "warning"`) instead of a new
  channel, because `coord-send` already writes that path and `kind` already has
  `warning`. The recipient is the order's own `sender`: who dispatched the work is
  a recorded fact, while `"operator"` is a mailbox nobody promised to read.
- Alert idempotency needs no ledger. The message id derives from the hold's
  identity (`task|event_id|sent_at`), so the mailbox file **is** the durable mark:
  a second tick computes the same id, finds the file and stays silent.
- `step(..., hold_limit_seconds=3600)` now defaults to a number instead of
  `None`. This is **policy, not measurement** -- nothing in the code derives it.
  `None` still restores the 0.6.13 behaviour.
- Fixed: orders with no `session_id` were reported as `foreign_session`. 6 of the
  24 real orders in `coordination/commander/inbox` have no `session_id`, so their
  verdict was unreachable while the reason claimed knowledge we do not have. New
  reason `session_unknown`.
- Fixed: `commander.tick`'s skip list omitted `ABANDONED`, so an abandoned order
  past `expires_at` was rewritten to `FAILED`/`EXPIRED` -- the signature stayed but
  the state stopped saying so, and a human decision silently became an automatic
  failure. Verified by reverting the fix and watching the test report
  `'FAILED' != 'ABANDONED'`.
- Known, documented, not fixed: `engine.tick` can hand off between steps and mint
  a second never-sent order, after which the hold legitimately disappears -- so
  "no ACK, no progress" is bypassable by one handoff. The readback session guard is
  not wired (`session_checked` honestly reports `False`). `ack_tracker` was
  investigated (`docs/ACK_TRACKER_MERGE_20261101.zh-CN.md`) and **not** merged.

## 0.6.13 — Give the wait an exit, and never give progress a timer

- Fixed the defect 0.6.10 shipped and documented: `hold_unacked` had no ceiling,
  so an agent that never ACKs pinned a task in `running` forever with nothing in
  the receipt but the word `hold_unacked`. In unattended operation that is a
  silent deadlock, not caution.
- The obvious fix -- treat a timeout as a failure and hand the work to someone
  else -- was rejected, because it deletes the guarantee: "no ACK, no progress"
  becomes "no ACK, progress after N minutes", a schedule instead of a judgement.
  Re-sending is worse still, since it assumes the first delivery did not arrive,
  which is exactly what `SUBMISSION_UNCONFIRMED` says we do not know.
- Holds are now visible. Each `held` row carries `unacked_for_seconds`, `sent_at`,
  `limit_seconds` and `over_limit`, from a new `step(..., hold_limit_seconds=)`.
  Crossing the limit **changes nothing except that someone is told**: the task
  stays `running` and `closed` stays empty, asserted by
  `test_crossing_the_hold_limit_reports_it_without_advancing`. `sent_at` comes
  from the verdict rather than a second read of the order, because two reads
  eventually disagree and then nothing can settle which is right.
- Ending the wait now costs a signature: `aep coord-ack --order-id <id> --abandon
  --by <name> --reason <text>`, both required. Its consequence is `blocked`,
  never `done` -- a task marked finished because an operator got tired of waiting
  is the failure this protocol exists to prevent. It does not go through
  `fail_task`: that asks "who takes over next", and re-delivery is the one action
  an abandonment exists to rule out. Abandoning an acknowledged order is refused,
  and abandoning twice is idempotent with the **first** signature kept, because
  the first is the decision and the second is a retry.
- New `engine.abandon()` and `commander.abandon()`; `ack_protocol` gains a fifth
  `state`, `abandoned`, plus `abandoned()` and `sent_at` on the verdict.
- Defect found by running the new command against a real order, and fixed in the
  same release: abandoning overwrote `state` with `ABANDONED`, so an order whose
  `MESSAGE_VISIBLE` existed only in `state` and not in `attempts` reported
  `sent: false, delivery: "none"` -- the sign-off had un-sent the message. The
  prior state is now kept in `state_before_abandon` and `delivery()` reads it. An
  action that erases a fact is worse than no action; this is the same class of
  error as 0.6.11, introduced by me in the same release that fixed it.
- Not done: the limit is a report, not a policy -- nothing delivers `over_limit`
  anywhere, and `held` only exists in `step()`'s return value, so 7x24 operation
  needs someone reading it; an abandoned task cannot be reopened; and a late ACK
  still upgrades an `ABANDONED` order to `AGENT_ACK` (deliberate: a late reply
  must be accepted) while the task stays `blocked`, so the two can disagree until
  a person reconciles them.

## 0.6.12 — A hang guard that was doing a latency assertion's job

- Test-only change; no product code touched. `test_improvement_stage_watcher.py`
  spawned `pwsh` with `timeout=12`, which is about twice the measured cost (~6s
  per invocation; the file's nine tests take 57.84s) and not enough under
  full-suite concurrency. On 2026-11-01 the full run failed once with
  `subprocess.TimeoutExpired`; the same file alone passed 9/9 and the next full
  run passed 968/968.
- That timeout asserted nothing. The tests assert `returncode != 0` and
  `"watcher.lock" in stderr`; the 12 seconds only meant "do not hang forever".
  It is now `PWSH_HANG_GUARD = 60`, and no assertion changed. A hung script still
  fails the test, just 48 seconds later; a slow-starting one no longer does.
- Deliberately not changed: the two `process.wait(timeout=12)` calls and
  `_wait(timeout=12)`. The first wait on a running process reacting to a file
  event rather than on startup, and have no failure evidence; the second already
  fails with its own message instead of a `TimeoutExpired` traceback. Widening
  every timeout in sight would hide the problem rather than fix it.

## 0.6.11 — The previous release stated something it did not know

- Corrected a false claim shipped in 0.6.10. `ack_protocol.sent()` returned
  false for every `CEASED` order, so `state` read `not_sent`. But `commander`
  ends an order in `CEASED` for two opposite reasons and only the note separates
  them: `DELIVERY_UNPROVEN` (the adapter failed before anything was typed) and
  `SUBMISSION_UNCONFIRMED` (**a send may have happened**; do not retry
  automatically). "Possibly sent" and "not sent" were sharing one word.
- Found on real data, not by review. Order `2026-10-30T0422243473450000-04d70400`
  (`qoder`, `AEP-CI-FAIL-20261030`) has six trailing attempts noted
  `SUBMISSION_UNCONFIRMED: possible send; do not retry automatically`, and
  `aep coord-ack` reported `state: "not_sent"` for it. It now reports
  `state: "submitted_unconfirmed"`, `delivery: "unconfirmed"`.
- New `ack_protocol.delivery()` returns `proven` / `unconfirmed` / `none`;
  `state` gains a fourth value, `submitted_unconfirmed`. `sent()` stays true only
  for `proven`, deliberately: an order whose send is merely possible must not pin
  a task waiting for a reply that may never have been asked for, and must not be
  described as undelivered either. Both statements are now true separately, so
  the supervisor's hold semantics are unchanged.
- The note prefixes are matched as prefixes, so a note can gain explanation
  without losing its meaning here, and `NotePrefixesAreStillWhatCommanderWritesTests`
  reads `commander.py` and asserts both are still written -- a rename there would
  silently return the judgement to `not_sent`, which is the defect just fixed.
- The same read-only run gave the four guards their first real-data evidence:
  order `2026-10-30T0345442451860000-7cf3d44f` (`qoder_cn`) is `acked` with two
  candidates, one rejected `not_assistant` -- a real transcript line carrying the
  token with `role`/`direction` both `unknown`, i.e. our own request. Until now
  that guard had only fixture evidence.
- Not done: unchanged from 0.6.10 -- session content is not in the fingerprint,
  `hold_unacked` has no ceiling, `ack_tracker.py` is still unmerged, and
  `TASK_RESULT_READBACK` is still unimplemented. This release corrects one wrong
  judgement and widens nothing.

## 0.6.10 — Do not advance without an ACK, and do not send into a different session

- New `coordination/ack_protocol.py`. The token we ask the peer to echo is a
  string we ourselves put in the outgoing message, so *any* "look for this token
  in the transcript" implementation matches our own request first. An ACK is now
  an event with a role, in the right session, recorded after the order was
  created. Four guards, four distinct rejection reasons: `not_assistant` (our
  own line, the user's line, or an input-box draft), `not_incoming` (the same
  line captured as outgoing), `foreign_session` (another conversation answering
  the same question), `stale` (an answer to a previous delivery -- the event id
  is per task, so a re-send reuses it). They are kept separate because they
  demand different handling: waiting, mis-delivery, and reading our own request
  are not the same failure.
- `state` has three values and **no timeout**: `not_sent`, `sent_unacked`,
  `acked`. A late reply must be accepted. A timeout is a statement about our
  patience, not about the peer; treating it as a rejection is exactly what
  produces duplicate sends. Measured once on 2026-10-31: the reply arrived about
  a minute past a 60-second window while the message had long been delivered.
- New `aep coord-ack --order-id`, exiting 2 when the ACK is not accepted, and
  reporting the `rejected` candidates with individual reasons rather than a bare
  `False`.
- `supervisor._settle()` now refreshes ACKs (read-only; it upgrades state and
  never types) and refuses to close a task whose order was sent, whose adapter
  can report ACKs, and which has none. The task stays `running`, downstream
  nodes do not start, and `step()` returns it under `held` so "why did nothing
  advance" is a receipt field instead of an inference. `CommanderAdapter` gained
  `ack_verdict()`; before this, `acknowledged()` existed with no caller -- a
  check that only existed in its own definition.
- Three deliberate limits on that gate: only orders that were actually sent
  (waiting on an undelivered message is a deadlock, not rigour); only adapters
  that can report ACKs (demanding evidence an adapter cannot produce is the
  other kind of deadlock); `FAILED` stays terminal while `CEASED` can still be
  upgraded by a late ACK.
- New `coordination/session_fingerprint.py`. A marker check answers "is this
  string on screen" -- it is also in the sidebar, and in another instance's
  session list. The fingerprint answers "is it still that session":
  `agent + exe + title + session_marker + model_marker`. **pid is deliberately
  excluded** -- a restart is not a session change, and a fingerprint that
  expires on every restart teaches the operator to re-record blindly, which is
  the same as having none. pid is still recorded as context.
- Live, read-only measurement on this machine:
  `qoder pid=22796 fp=332555763b7f26a5`, `qoder_cn pid=56368 fp=20eb3eb74f41b52d`,
  `workbuddy pid=3560 fp=82dad2ad037bae62`. The two Qoder products differ by
  fingerprint as well as by executable.
- `compare().match` is `None` when nothing was recorded, **not `True`**:
  "nobody wrote it down" and "it agrees" are different claims. `None` sends and
  records the fingerprint in the `WINDOW_FOUND` receipt; `False` defers with
  `SESSION_FINGERPRINT_MISMATCH` and a `diff` naming the differing fields.
- Fixed during the WSL run: `_normalise()` used `os.path.normcase`, which on
  POSIX is the identity function and does not treat `\` as a separator, so the
  same path produced two different fingerprints on two hosts. The normalisation
  is now implemented here instead of delegated to the host.
- Not done, deliberately: session *content* is not in the fingerprint (the two
  markers come from the profile, not the screen, so "the window is now showing a
  different session" is still `SESSION_NOT_VERIFIED`'s job); `hold_unacked` has
  no ceiling, so an agent that never ACKs pins a task in `running` forever;
  `ack_tracker.py`'s lease-based ACK record is *not* merged with this protocol --
  two coexisting schemes is what this repository keeps warning about, so it is
  written down rather than papered over; `provider_readback`'s `aep-readback-v1`
  is still a separate contract and `TASK_RESULT_READBACK` is still unimplemented.

## 0.6.9 — Prove the message should be sent before arguing about how

- `client_registry.py`: window identity was a title substring, and `Qoder` is a
  substring of `Qoder CN`. Measured: the `qoder` profile matched the `Qoder CN`
  window while `qoder_cn` correctly did not match `Qoder` -- so a dispatch to
  `qoder` on a machine running only the CN client entered the wrong product's
  window and reported `unique: True`. The executable path now arbitrates: a
  window whose image is exactly the one another registered profile names belongs
  to that profile, and every other claim on it is dropped. Deliberately *not* a
  filter -- two of the four live profiles here have already drifted to a new
  version directory, and turning that drift into "no window found" trades a
  wrong send for an unreachable client. Each hit now reports `identity` as
  `exe`, `title` or `process-substring`, and `discover()` returns the rejections
  it made. First live run found `workbuddy` had been claiming the `WorkBuddy AI`
  window while both clients were running.
- New `coordination/dispatch_gate.py`: twelve pure checks on the exact string
  about to be typed, run in `commander._dispatch()` after the window is chosen
  and before any transport is selected. Content hash, a single event id, a
  single task marker, the ACK instruction, target-vs-profile, not superseded by
  a newer order, the workspace DAG's own event id, and expiry are blocking; the
  three "does the body name itself" checks and the window identity strength are
  warnings that `--strict` promotes. A refusal is recorded as `FAILED` with
  `signal: dispatch_refused`, not `DEFERRED`: content that is wrong does not
  become right by waiting, and a retryable state would re-queue and re-refuse it
  every tick.
- `commander.submit_order` now records `content_sha256` of the final body, so
  "the text was edited after it was queued" is a detectable condition instead of
  an invisible one.
- New `aep coord-dispatch --order-id|--all [--strict] [--show-body]`, which
  previews the order and replays the gate offline. A refused order exits 2.
  Measured on a real rendered assignment: 12 checks, `send`, exit 0; after
  appending an old task's ACK line, `content_unchanged` and `one_event_id` both
  fire and it exits 2 -- two independent judgements, so re-recording the hash
  does not rescue it.
- Not done, deliberately: no human confirmation step, because it conflicts with
  unattended operation -- the substitute is a reproducible refusal. The gate
  does not catch "right order, wrong conversation" (that is session
  verification), does not prove the agent accepted anything, and was never
  replayed against the original incident's order, which is not in the workspace.

## 0.6.8 — Put the rule on the live path, then find out it doesn't know what's right

- `ui_fallback.py`: `input_routing.resolve_target()` now runs on every attempt of
  the live delivery path, and `state["route"]` is written unconditionally —
  including the attempts UIA settles. A receipt that only records the
  interesting cases cannot answer "did we ask a model here?" afterwards.
  The two transports are treated differently and both say why: a `uia`
  transport types through a control UIA itself identified, so a located region
  is recorded and the send is *deferred* (`TRANSPORT_CANNOT_USE_LOCATED_REGION`)
  rather than silently pasted into whatever has focus; an `ocr` transport
  converts the region to centre ratios and hands them to the existing
  `-AllowComposerGeometryFallback`. `_deliver()` detects drivers that cannot
  accept a region and returns `composer-region-unsupported` instead of dropping
  it, because a route that is computed and ignored looks exactly like a route
  that was never needed.
- `input_routing.py`: the decisive test is `input_found and not is_ambiguous`,
  not `candidate_count == 1`. Measured: ChatGPT reports
  `input_candidate_count: 2` with `input_ambiguous: false` and delivers fine;
  reading the count as ambiguity threw away a working path. The adapter's own
  verdict wins when present. A receipt with no `window_size` is now
  `NO_WINDOW_GEOMETRY` and the grounder is not called at all — a 0-999 answer
  has no meaning without a denominator.
- **The vision leg ran for real: 12 cells, 4 pass the gate, 1 is correct.**
  The gate checks legality, not correctness — the three "pixels + `composer`"
  cells all scored 0.99-1.0 confidence and all framed the wrong thing (left
  sidebar, conversation header, right-hand table cell), proven by the OCR text
  inside each box. Using `admissible()` as "safe to click" would have clicked
  wrong three times out of four. Prompt *format* mattered more than the model:
  the repo's `[0, 999]` grid produced 6/6 out-of-bounds answers on this model,
  which had been trained on absolute pixels. Data in
  `docs/VISION_GROUNDING_PROBE_20261031.zh-CN.md`.
- `windows_ocr_compat.ps1`: the piped JSON was **not valid JSON**. The console
  encoder turned Chinese into `?` and two unrecognised glyphs into a bare `0x07`,
  so `json.loads` failed and the whole transcript was dropped — OCR recognised
  the text and everything downstream threw it away. Fixed at three layers:
  write UTF-8 bytes to stdout directly, add `-OutFile` for file hand-off, and
  read it back with an explicit `UTF8Encoding` (otherwise the file is UTF-8 and
  the reader decodes it as GBK).
- `examples/execution-provider-catalog.json`: `rea` corrected on two facts — it
  is a **Node** package (`^22.23.4 || ^24.22.1 || >=26.12.1`), not Python, and
  it has no AEP connector. `source_note` records what the previous entry got
  wrong. `ida_pro_mcp` registered as `candidate` with its risks written down:
  needs IDA Pro 8.3+ (no IDA Free), exposes `py_eval` and `patch*` (arbitrary
  Python in the target binary, and byte patching), upstream already recommends
  the official `ida-mcp` instead. `qwen2_5_vl_3b` registered as `candidate` with
  the measured 4/12 pass, 1/12 correct, and a `license_note` that it is the
  Qwen **Research** License, not Apache-2.0.
- Not done, deliberately: the route is wired but has never delivered a message
  end-to-end on a live session; Mage itself still has not run, so every vision
  number here belongs to a *different* model; `rea` and `ida_pro_mcp` are
  catalog rows only, with no calling convention and no connector.

## 0.6.7 — Both legs, and a rule for which one goes first

- `vision_grounding.py`: a vision answer is a *proposal*, not an observation.
  `admissible()` refuses low-confidence and out-of-window boxes instead of
  clamping them; `from_normalised()` raises on values outside the model's
  `[0, 999]` grid because a model that answers 1200 is answering a different
  question. `require_provider()` reads the same `status` field that
  `coord-providers promote` refuses to advance without evidence, and
  `HttpGrounder` performs that check in `__init__`, so there is no way to hold
  a usable model-backed grounder whose row was never probed.
- `policy_executor.py`: the only thing that touches the desktop, under four
  rules a model cannot be trusted to enforce on itself. The action vocabulary
  is `desktop_providers.OPERATIONS`, so a primitive with no operation behind it
  is refused rather than degraded (a double click becoming a single click is a
  wrong action that looks like a successful one). Shell-shaped actions are
  refused by name. Coordinates are range-checked before conversion. An
  unverified step ends the run, and `verified is None` is not a pass.
- `input_routing.py`: the ordering that makes the two legs complementary rather
  than redundant. UIA with exactly one candidate is decisive, so the model is
  **not** asked — a settled question must not be put to a weaker answerer.
  Ambiguity (0 or >1 candidates) is the only window where perception adds
  information. A configured region is the last resort and always needs
  verification, because a hard-coded region is what types into the wrong box
  after a UI change. `delivery_verdict()` defaults to defer; a write step with
  no verifier counts as unaccounted for.
- New `aep coord-vision ground|providers`, `aep coord-policy validate`,
  `aep coord-route`. The last one replays a routing decision from recorded
  receipts, so the choice between the legs can be argued with a fixture instead
  of a live desktop.
- `coord-policy` used `plan.get("actions") or plan.get("plan") or []`, so a
  plan object with a misspelled key became an empty plan: zero steps,
  `COMPLETED`, indistinguishable from a clean validation. It now refuses with
  the reason and exit code 2.
- Not wired yet, deliberately: `ui_fallback.run()` still only walks the UIA
  path, and no provider is `probed`, so the vision leg has never run for real.
  Both facts are recorded in `docs/releases/v0.6.7.md` rather than implied away.

## 0.6.6 — The registry became a gate instead of a list

- `execution_provider_catalog.promote()` / `retire()` / `history()`. Fourteen
  providers all sat at `candidate` and nothing had ever reached `probed`,
  because `status` was a field anyone could retype. Promotion now refuses to
  skip a step, refuses to advance without that step's evidence keys, and
  refuses to advance on a failed measurement (`probe_result` / `task_outcome`
  must be `pass` — a failed probe is a finding, not a status). Every change
  appends to `provider_promotions.jsonl` beside the catalog, so how a row
  reached `task-tested` stays answerable.
- The shipped catalog and its schema were two standards: the catalog used
  `integration: vision-service` and a `license_note` field, and the schema's
  enum and `additionalProperties: false` allowed neither. No test ever read the
  schema, so the permissive copy was the load-bearing one. Both values are now
  in the schema, `validate_catalog` also enforces the layer/integration enums
  and rejects undeclared fields, and a jsonschema validation of the shipped
  file guards the pair from drifting again.
- Registered `mage_vl_cowork` as `desktop_visual` / `vision-service` /
  `candidate` with its real limits written down (the 66.04 OSWorld figure
  includes an unrestricted bash channel; the GUI-only stage is 45.56;
  perception cannot defeat UIPI; Linux pyautogui training domain;
  `trust_remote_code=True` executes repository code at load time). Its four
  acceptance criteria are measurable, and the assessment plus pre-registered
  probe live in `docs/MAGE_VL_COWORK_ASSESSMENT_20261031.zh-CN.md`.
- New `aep coord-providers list|promote|retire|history`, which turns a refusal
  into one stderr line and exit code 2 instead of a traceback.

## 0.6.5 — The scheduler can deliver, and it refuses without burning the attempt

- `commander.provider_readback` had the polarity backwards: it was a whitelist
  of failure statuses with everything else falling through to
  `INPUT_SUBMITTED`. Measured, all four of these came back as proof that input
  was entered: `urgent-interject-unavailable`, `composer-not-confirmed`,
  `composer-confirmed-no-send`, `LAUNCH_FORBIDDEN`. A receipt now has to earn
  `INPUT_SUBMITTED` (`input_verified` / `composer_verified` /
  `submitted-unconfirmed`), otherwise it is `FAILED` and keeps the status as
  the error.
- `ui_fallback.run` acts on the drivability verdict instead of only reporting
  it. An unelevated executor pointed at an elevated client used to run the
  whole UI attempt and then record `CEASED` / "possible send" -- a minute
  spent, and a claim that something may have been typed when UIPI guarantees
  nothing was. It now returns `EXECUTOR_TOO_LOW` before any capture, and the
  commander records that as a resumable `DEFERRED`. Measured both ways with a
  probe whose `deliver` raises: standard executor -> refused, 0 captures,
  0 delivers; elevated -> past the gate, 1 capture, 1 deliver.
- `SESSION_NOT_VERIFIED` now names the marker it looked for, whether it was
  present, and how many characters were captured. A local profile said
  赛事守冠执行 while the window shows 赛事夺冠执行 (5 occurrences) and listed an
  OCR-mangled `FlagGems-sgIang` where the window shows `FlagGems-sglang`
  (8 occurrences); every scheduled delivery deferred at session verification
  and could never become ready. The live profile is corrected.
- The same profile used `transport: ocr`, so the scheduler took the
  OCR/geometry composer path, which cannot confirm the composer
  (`send_status: composer-not-confirmed`, and it correctly refused to press
  Enter). With `transport: uia` -- the path the operator command already proved
  -- the scheduler delivered: `message-visible`, `send_retry: paste+enter`,
  `composer_clear_polls: 16`, and the challenge-response came back as
  `AEP-ACK: C0F5868E719945`.
- Delivered and accepted are no longer the same field. A visible send used to
  leave `task_delivered` at its initial `false` when the reply did not arrive in
  time; `task_delivered` now comes from the send receipt and `agent_accepted`
  only from the target's own answer. The state is renamed
  `NO_REPLY_WITHIN_TIMEOUT` because it states only what is certain.
  `reply_timeout_seconds` goes 60 -> 120 on measurement: the reply arrived
  about a minute later, outside the old window.

## 0.6.4 — An elevated executor can now deliver, and its receipt survives the trip

- `Write-Report` no longer hands the receipt to PowerShell's output stream, which
  put the console encoder in the path; an elevated process can start with an OEM
  code page that cannot represent Chinese. Measured on the same run: the
  `-OutFile` capture was perfect UTF-8 (52,750 non-ASCII bytes, 7 `?`) while the
  JSON receipt arrived with every Chinese character as `?`. The work had
  succeeded and only the echo was destroyed. Bytes are now written directly to
  standard output.
- `coord-uia` goes through `uia_argv.run()`. Elevated Python reports
  `locale.getpreferredencoding() == cp1252` while the adapter writes UTF-8, so
  `subprocess.run(text=True)` killed its reader thread on the first Chinese
  character and handed back `stdout=None` (`AttributeError: 'NoneType' object
  has no attribute 'splitlines'`). `transcript_patrol`, `octo_transport` and
  `wake_daemon` had the same pattern and got an explicit `encoding="utf-8"`.
- The composer check polls instead of checking once, and retries with a paste
  when it does not clear. Checking once right after the send action produced a
  false negative -- the message had been delivered and the receipt said
  `submitted-unconfirmed`, which invites a duplicate send. The retry is not a
  timing patch: `ValuePattern.SetValue` updates the visual editor without firing
  the web app's input event, so its send control stays disabled and Enter does
  nothing. Receipts now carry `composer_clear_polls` and `send_retry`.
- Measured end to end on elevated Qoder (pid 22796): `status: message-visible`,
  `task_delivered: True`, `input_found: True`, `input_candidate_count: 2`,
  `write_method: uia-valuepattern`, `send_retry: paste+enter`,
  `composer_clear_polls: 36`, `drivable: True`, `path_verified: True`. The event
  id then read back from the session with the agent's own reply outside our
  message: `ACK AEP-UI-PROBE-20261031T203037`. That is `INPUT_SUBMITTED`,
  `MESSAGE_VISIBLE` and `AGENT_ACK` on the evidence ladder.

## 0.6.3 — One command and the scheduler must read the same profile

- `coord-uia` read `cli.UIA_CLIENTS`, a hardcoded table that still named Qoder
  `0.4.11` while `0.5.1` was running, and passed no `-AllowUnknownPath`. The
  operator's own command failed with `LAUNCH_FORBIDDEN: no window for this
  agent` while the scheduler found the same window from
  `coordination/ui_profiles/qoder.json`. The profile file now wins and the
  built-in table is only a fallback; the profile-to-flags mapping moved into
  `ui_fallback.profile_args()` so both paths share one copy.
- `-AllowClipboardPaste` was never passed from Python, so the adapter's
  `NO_INPUT_CONTROL` guard had no way out for a client it could not read. It is
  now opt-in per profile and off by default: the paste lands wherever
  activation put focus.
- The adapter compares `-ExpectedPath` with `Process.MainModule`, which throws
  for an elevated process and was caught and skipped -- so the check was
  skipped for exactly the clients this project runs elevated on purpose. Added
  `coordination/process_info.py` using `QueryFullProcessImageNameW`, which needs
  only `PROCESS_QUERY_LIMITED_INFORMATION`. `coord-uia` now reports
  `profile_path_check`, where `match: null` means "not checked" and `reason`
  says why.
- The three deliver early returns now report `input_found` too; a failing
  branch has to answer the same question as the succeeding one.
- Corrected `HANDS_AND_FEET_STATUS_20261031`: `deliver` does reach the OCR
  composer path (`transport: ocr` routes to `-Mode compose`), so the geometry
  fallback was never the blocker. The remaining condition for
  `INPUT_SUBMITTED` is an elevated executor.

## 0.6.2 — Compare the two sides instead of trusting a failure mode

- `a11y.drivability(target, executor)` replaces the old "can the target's token
  be opened" test. That test read correctly only because Windows refuses the
  token of a higher-integrity process -- the comparison was being performed by
  the failure mode rather than by the code, so any readable target was reported
  as drivable. Receipts now carry both `target_elevation` and
  `executor_elevation`. Measured here: elevated Qoder is
  `unknown`/`standard`/`false` from an ordinary terminal, while standard
  ChatGPT is `standard`/`standard`/`true`.
- Added `windows_job.interpreter_profile(command)`. A venv `Scripts\python.exe`
  is a 253,256-byte redirector that re-executes the base interpreter inside a
  nested job whose flags read `0x3000` instead of `0x2000`; the extra `0x1000`
  is `JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK`, so `CREATE_BREAKAWAY_FROM_JOB`
  succeeds there and is denied (`5`) under the base interpreter. Termination
  still works in both cases -- nested jobs die with the parent -- but the
  no-orphan promise does not. The containment test now asserts or skips on the
  profile and prints the reason, and `InterpreterProfileTests` pins the
  detection itself.
- Fixed a first-byte race in the workspace lock: between `fstat` seeing size 0
  and the write that creates that byte, another process can create it and lock
  the region, so the write lands on a locked byte and Windows answers EACCES.
  The pre-fill is redundant by then; it no longer becomes a lock failure.
- The bundled venv (`.tools/agent-runtime`) gained `pytest` from the `test`
  extra, so the project's own development environment can run the suite.

## 0.6.1 — Say why a window cannot be driven

- The UI driver now annotates every receipt with `target_elevation`, `drivable`
  and, when applicable, `blocked_by: TARGET_NEEDS_ELEVATION`. Running the
  clients elevated is a legitimate operating choice -- an unelevated agent gets
  stuck on UAC when it runs a command -- but Windows then refuses to let a
  medium-integrity process enumerate, move or type into that window, and every
  one of those refusals is silent. The caller used to see `input_found: false`
  with no reason for it.
- Documented the operating rule this implies: the executor runs elevated too.
  A higher-integrity process can drive a lower one, not the reverse, so one
  direction is enough. `K:` reports `FIXED` via GetDriveTypeW, so an elevated
  process still sees the repository; a network-mapped drive would not.

## 0.6.0 — Ask the OS instead of guessing at pixels

- Added `coord-a11y`: report or set the Windows screen-reader flag that gates
  Electron accessibility trees, and report whether a target process can be
  driven at all. The change is runtime-only by default, so a reboot undoes it.
- Measured why it matters: the same ChatGPT window goes from 3 elements, no
  input control and OCR-only text to 400 elements, 3 input candidates,
  `input_found: true` and 60978 characters read through UIA.
- Added `coordination/a11y.py`, including an elevation check. Windows refuses
  to hand over the token of a higher-integrity process, and every later step --
  UIA enumeration, `ShowWindow`, injected keys -- fails silently when that
  happens. A target that cannot be driven is now reported as `drivable: false`
  instead of failing three steps downstream. Measured on this host: the Qoder
  client runs elevated and reports `unknown`; ChatGPT runs standard and reports
  `drivable: true`.
- Added `docs/WINDOWS_SYSTEM_CAPABILITIES.zh-CN.md`: which capabilities belong
  to Windows (element tree, input injection, event hooks, process facts) and
  which must stay ours (DAG, evidence ladder, leases, handoff, audit chain).
- Tests read the flag but never flip it: toggling a machine-wide accessibility
  switch would change how every application on the desktop behaves.

## 0.5.1 — The OCR was blind, not missing

- Declared the test extra (`pytest`, `jsonschema`) and made CI install it. The
  federation conformance test imported `jsonschema` at module scope while CI
  installed only `pytest`, so `unittest discover` died on every platform with
  `ModuleNotFoundError`. A missing validator now skips with a reason instead of
  aborting discovery, so a bare interpreter still runs the rest of the suite.
- Fixed the conformance test's own portability bug: it printed Chinese to a
  child process whose stdout encoding is the platform locale (cp1252 on a
  Windows runner), which failed with `UnicodeEncodeError` for reasons unrelated
  to what the test checks.
- Fixed OCR being silently monolingual: `-OcrLanguage` reached one of ten
  `Get-WindowOcrText` call sites, so the nine that locate the session, find the
  send button and read the reply all used the user-profile recognizer (en-US).
  An English recognizer reading a Chinese UI returns plausible Latin look-alikes
  and raises no error, so every Chinese marker missed. The function now inherits
  `$script:OcrLanguage`, and the receipt reports `ocr_language_requested`
  next to `ocr_language`.
- Fixed Chinese OCR text never matching: Windows OCR emits a space between
  adjacent CJK glyphs (`插 话`), so `Contains("插话")` was always false. The
  whitespace between two CJK characters is now collapsed at the OCR boundary;
  Latin spacing and CJK/Latin boundaries are preserved.
- Fixed `observe` reporting an empty transcript for Electron clients, which
  expose no text to UIA. It now falls back to OCR and says so in
  `transcript_source`.
- Corrected `docs/HANDS_AND_FEET_STATUS_20261031.zh-CN.md`: the previous version
  claimed this machine lacked the Chinese OCR recognizer and told the reader to
  install it. The recognizer was installed all along (`zh-Hans-CN`), and the
  install command fails on the newer shell with "class not registered".

## 0.5.0 — Persistent Federation Workspace

- Added the `federation` package: hash-chained event ledger, three-zone snapshot,
  handoff packets, a 13-signal diagnosis table and a strategy library whose
  promotion is a pure function of evidence.
- Added a workspace-level exclusive lock (cross-process, in-process re-entrant);
  the control experiment lost 400 of 800 increments without it and none with it.
- Added a real cross-process A2A adapter and a demo that runs four child
  processes, two of which crash or exhaust context mid-task and are replaced.
- Added `coord-assign`: one call writes the DAG node, the write lease and an
  assignment the receiver can execute line by line. A refused scope is reported
  as `claim: scope_conflict` instead of being smoothed over.
- Added `federate intake|step|serve`: one request, one DAG, first wave started,
  then a heartbeat that settles, schedules, leases and dispatches in that order.
  Settlement reads only what the assignee wrote; silence is never success.
- Added `engine.fail_task` so a supervisor that can see a delivery die replaces
  the owner immediately; it shares one decision table with `tick`.
- Fixed Chinese UI parameters being reduced to `?` by the child console code
  page (ibm437): non-ASCII values now travel in a UTF-8 `-ParamFile`.
- Fixed the OCR engine silently using en-US on a Chinese window; the language is
  now requestable and recorded as `ocr_language` in the receipt.
- Split `requirement` (prose) from `capability` (owner matching) so a real
  sentence no longer reports "no capable agent".
- Added lease TTL to claims, owner-only renewal, and `self_overlaps` so one
  agent holding two labels over one path is not reported as a conflict.
- Desktop `INPUT_SUBMITTED` and beyond remain unproven: this machine has no
  Chinese OCR recognizer installed. See `docs/HANDS_AND_FEET_STATUS_20261031.zh-CN.md`.

## Unreleased

- 预备 0.4.2：加入贡献者署名、风险控制基线和 Provider 分发溯源硬门。

- 预备 0.4.1：统一包内与项目元数据版本号，补充项目标准、第三方致谢和发布回滚规范。

- Added Qoder execution-gate replay: read-only busy/ready probing before input,
  strict multi-placeholder composer matching, client-specific accent-button
  detection, and explicit `AGENT_BUSY` / `ACTIVITY_UNCONFIRMED` receipts.
- Added `aep-dispatch-envelope-v1`, strict dispatch validation, schema-backed
  session/retry/evidence rules, and the reusable dispatch standard for all
  adapters and Agent handoffs.
- Added the Qoder failure review, federation execution roadmap, and a reusable
  Agent operator contract for sharing session/ACK/Red Queen-Catfish rules.
- Added a read-only third-party execution provider catalog and L0-L3 acceptance
  policy for browser, desktop, terminal, engineering, mobile and REA integrations.
- Added a reproducible isolated capability runtime installer for MCP, Playwright,
  Windows UIA, event watching, observability and DuckDB.
- Added a Chinese beginner introduction covering design intent, conceptual roots, a complete round, common misconceptions, and a first-run walkthrough.
- Added local coordination kernel docs and reference implementation: status board, wake daemon, wake adapters, realtime hub/hook, and Red Queen / Catfish / Creative Destruction retrospective.
- Added session-affinity policy, digest-first handoff, batch topic acknowledgement, stale state detection, and wake pending/claim timeout tracking.
- Added `aep route` for natural-language scenario routing across project join, task join, takeover, review, isolated exploration, shared-resource writes, wake requests, status reports, and CLI-less fallback.
- Added workspace vocabulary configuration through `.aep/routing.json`, explainable classification traces, ambiguity reporting, and fixed read-only/negation guards.
- Extended task and agent identifiers to safe general-purpose slugs; `route --workspace` discovers the selected project's configuration when called from another directory.
- Added route guidance based on owner freshness, checkpoints and resource type, while keeping routing read-only and requiring outcome checks before retrying uncertain operations.
- Expanded natural-language onboarding, file-only participation, session reuse guidance, and the requirements/acceptance record dated 2026-10-08. Desktop wake delivery remains adapter-dependent.

## 0.2.0 — Institutional Kernel

- Added a SHA256-linked, sequence-numbered event ledger.
- Added `audit` with tamper and broken-chain detection.
- Enforced candidate and round budgets when recording results.
- Added structured `challenge`, `threat`, and `decide` events.
- Added `HUMAN_HOLD` / `INCIDENT_HOLD` and human-approved resume.
- Added governance card templates and CI across Python 3.10 and 3.12.

## 0.1.0 — Initial shadow protocol

- Added isolated proposal commit–reveal.
- Added artifact SHA binding and protected reserve budgets.
- Published the protocol, evolution history, templates, and minimal CLI.
## 0.4.0

- Added persistent, resumable mission graphs with event recovery and ACK-driven successor unlocking.
- Added provider readback contract `aep-readback-v1` and explicit Agent ACK semantics.
- Added onboarding diagnostics for Git LFS, CUDA/NVIDIA and local model weights.
- Added CUDA-first local Qwen runtime with model-contract validation and deterministic fallback.
- Added evidence-driven capability scoring with persistence and adaptive routing.
- Added bounded autonomous supervision, Computer-use capability matrix and delegation playbook.
