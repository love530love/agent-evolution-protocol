# Agent Evolution Protocol

An evidence-driven governance protocol for multi-agent systems that need diversity without coordination collapse.

**New here?** Start with the plain-language [Chinese introduction](docs/INTRODUCTION.zh-CN.md), which explains the failure mode, design rationale, roles, one complete round, and a beginner walkthrough.

The design combines three forces:

- **Red Queen pressure** — respond to verified external change instead of manufacturing urgency.
- **Catfish challenges** — keep a bounded, event-triggered dissenter that can fund one cheap falsification test.
- **Creative destruction** — select candidates through preregistered evidence, not authority or majority vote.

## Why

Connecting more agents often improves message throughput while reducing cognitive diversity. Shared context creates anchoring, imitation, premature consensus, and compromise solutions that are difficult to attribute or falsify.

This project separates:

- a **coordination plane** for public facts, ownership, budgets, hashes, and verified results;
- a **competition plane** for isolated exploration, commit–reveal, falsification, and evidence-based selection.

## Core rules

1. The leader owns execution order, not technical truth.
2. Candidate hypotheses remain isolated until every cell commits.
3. A changed artifact or prediction creates a new candidate ID.
4. A catfish challenge must state an alternative, predicted signature, cheapest falsification, budget, and stop condition.
5. Safety vetoes are limited to compliance, security, irreversible damage, and data contamination.
6. Technical winners are chosen by reproducible experiments, never by vote count.
7. Ordinary messages never wake a model. Execution requires an explicit event or human action.

## Shadow CLI

The included standard-library CLI records local commit–reveal rounds. It does **not** call models, poll, use accelerators, modify candidate code, merge changes, or submit to external platforms.

```powershell
python -m agent_evolution_protocol init --round-id demo --task example --leader lead --cells alpha beta catfish --budget 10
python -m agent_evolution_protocol commit --round-id demo --card alpha.json
python -m agent_evolution_protocol status --round-id demo
python -m agent_evolution_protocol audit --round-id demo
```

Version 0.2 adds a tamper-evident event hash chain, ledger audit, enforced result budgets, explicit challenge/threat/decision events, and human-gated HOLD/resume.

## Coordination kernel

This repository also includes a local reference implementation for event-driven
multi-agent coordination:

- [collaboration evolution record](docs/coordination/MULTI_AGENT_COLLABORATION_EVOLUTION.md)
- [status board protocol](docs/coordination/MULTI_AGENT_STATUS_BOARD.md)
- [wake kernel](docs/coordination/MULTI_AGENT_WAKE_KERNEL.md)
- [session affinity](docs/coordination/MULTI_AGENT_SESSION_AFFINITY.md)
- [anti-collapse protocol](docs/coordination/MULTI_AGENT_ANTI_COLLAPSE_PROTOCOL.md)
- [Red Queen / Catfish / Creative Destruction retrospective](docs/coordination/RED_QUEEN_CATFISH_CREATIVE_DESTRUCTION_RETROSPECTIVE_20260928.md)

Reference code lives under `src/agent_evolution_protocol/coordination/`. It is
designed for local, event-driven wakeups: ordinary messages do not start models;
explicit wake requests are audited and routed through adapters.

## Unified local coordination CLI

Install locally and initialize a coordination root:

```powershell
pwsh.exe -File .\scripts\bootstrap.ps1 -CoordRoot coordination
```

The `aep` command is an alias for `agent-evolution` and exposes both planes:

- governance plane: `init`, `commit`, `reveal`, `challenge`, `threat`, `decide`, `audit`;
- coordination plane: `doctor`, `workspace-init`, `inspect`, `route`, `join`, `coord-status`, `coord-digest`, `coord-send`, `coord-wake`, `coord-wake-status`, `coord-archive-stale`, `coord-session-set`, `coord-claim`, `coord-release`, `coord-task-state`, `coord-onboarding`, `coord-guided-retry`;
- recovery kernel: `kernel-status`, `kernel-lease-acquire`, `kernel-lease-release`, `kernel-op-reserve`, `kernel-op-transition`, `kernel-session-bind`, `kernel-continuation-plan`, `kernel-checkpoint`.

The coordination CLI is local-file based and does not call models. It is meant
to make onboarding deterministic: a new agent reads the current digest and
sticky-session policy before doing work, and missing session IDs become a
visible handoff problem instead of silently spawning new chats.

See the Chinese [coordination CLI quick guide](docs/COORDINATION_CLI.zh-CN.md), [agent onboarding contract](docs/AGENT_ONBOARDING_CONTRACT.zh-CN.md), [guided retry policy](docs/GUIDED_RETRY_POLICY.zh-CN.md), [`aep inspect` runbook](docs/RUNBOOK_INSPECT.zh-CN.md), [joining any task](docs/JOINING_ANY_TASK.zh-CN.md), [natural-language join](docs/NATURAL_LANGUAGE_JOIN.zh-CN.md), and [`aep route` requirements and development plan](docs/ROUTE_DEVELOPMENT_PLAN.zh-CN.md).

See [the beginner introduction](docs/INTRODUCTION.zh-CN.md), [the protocol](docs/PROTOCOL.md), [the evolution history](docs/EVOLUTION.md), and [the Chinese README](README.zh-CN.md).

## Join a workspace in plain language

Initialize the shared project once:

```powershell
aep workspace-init --workspace 'K:\PythonProjects5\MyProject' --agents-md
```

Then tell a new agent: “Join this workspace and work on T125.” An agent that reads
`AGENTS.md` follows `AEP_JOIN.md`; otherwise point it to that file. A command-capable
agent can translate the original request into a read-only plan:

```powershell
aep route --agent hermes --workspace 'K:\PythonProjects5\MyProject' --task T125 --intent 'Join this task' --markdown
```

The router covers orientation, task joining, takeover, review, independent
exploration, shared-resource operations, recovery, wake requests, reporting and
file-only participation. Task IDs also support general work such as `BUG-42` and
`DOCS-INTRO`. Optional `.aep/routing.json` keywords adapt it to project vocabulary;
decision traces explain matches and ambiguity. Routing does not acquire ownership,
execute suggested commands or wake another model.

Existing sessions are preferred, with digest-first and checkpoint-based handoff.
An adapter must support actual session resumption or wake delivery; accepting a
new agent name does not provide that adapter. Agents without a CLI can read the
workspace entry and available status files, then request command-capable help.
See the [scenario router guide](docs/SCENARIO_ROUTER.zh-CN.md) for examples and limits.

## Status

Experimental shadow tooling. Keep irreversible operations and production automation under human approval until your own pilots pass.

## License

Apache-2.0.
