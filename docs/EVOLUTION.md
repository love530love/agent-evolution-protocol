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

## Current maturity

The CLI is shadow tooling. A deployment should first run a historical blind replay, then a low-risk sidecar pilot, and only then connect selected events to its coordination bus. Automated external submission, merging, safety-HOLD removal, reputation adjustment, and unbounded subagent recursion remain out of scope.

