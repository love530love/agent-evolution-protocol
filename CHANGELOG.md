# Changelog

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
