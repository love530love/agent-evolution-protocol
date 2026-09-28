# Agent Evolution Protocol

An evidence-driven governance protocol for multi-agent systems that need diversity without coordination collapse.

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

See [the protocol](docs/PROTOCOL.md), [the evolution history](docs/EVOLUTION.md), and [the Chinese README](README.zh-CN.md).

## Status

Experimental shadow tooling. Keep irreversible operations and production automation under human approval until your own pilots pass.

## License

Apache-2.0.
