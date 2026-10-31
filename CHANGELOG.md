# Changelog

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
