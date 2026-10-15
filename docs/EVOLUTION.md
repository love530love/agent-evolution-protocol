# Evolution history

## V0 — isolated conversations

Humans manually copied messages between agents. Delivery, reading, acceptance, execution, and verification were conflated.

## V1 — auditable mailbox

Append-only messages, acknowledgements, and ownership claims made delivery and responsibility inspectable, but agents still needed to check their inboxes.

## V2 — event-driven coordination

A local event stream and explicit wake commands removed model polling. Ordinary messages entered the timeline without starting inference; only an explicit execution event could start an independent worker.

The main lesson was that a healthy service or successful hook exit is not end-to-end proof. Delivery must be verified at the receiving event ledger.

## V3 — anti-collapse governance

Once communication became easy, agents began sharing hypotheses too early and converging on variants of the same route. The architecture split public coordination facts from private candidate exploration, then added commit–reveal, bounded dissent, external threat evidence, preregistered falsification, and experimental selection.

## V4 — frozen challenger gate

Adapted the evidence-first governance loop from [love530love/3D](https://github.com/love530love/3D): a mechanism regression can generate a deduplicated, SHA-256-frozen challenger proposal with an explicit rollback; a later independent task is compared with the frozen baseline. Passing the metric gate means expert review, never automatic promotion. See [3D integration](coordination/3D_EVOLUTION_INTEGRATION.md).

## Current maturity

v0.3.0 keeps Red Queen / Catfish / Creative Destruction as the umbrella system. The collaborative reasoning workflow is its operating guide. A frozen task contract now records acceptance, declared budgets, interaction mode and stop conditions through `coord-reasoning create|status`; it does not start execution or enforce budgets across adapters. See [system overview](SYSTEM_OVERVIEW.zh-CN.md).

The CLI is shadow tooling. A deployment should first run a historical blind replay, then a low-risk sidecar pilot, and only then connect selected events to its coordination bus. Automated external submission, merging, safety-HOLD removal, reputation adjustment, and unbounded subagent recursion remain out of scope.

