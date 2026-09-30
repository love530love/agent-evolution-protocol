# Changelog

## Unreleased

- Added a Chinese beginner introduction covering design intent, conceptual roots, a complete round, common misconceptions, and a first-run walkthrough.
- Added local coordination kernel docs and reference implementation: status board, wake daemon, wake adapters, realtime hub/hook, and Red Queen / Catfish / Creative Destruction retrospective.
- Added session-affinity policy, digest-first handoff, batch topic acknowledgement, stale state detection, and wake pending/claim timeout tracking.

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
