# Red Queen — Catfish — Creative Destruction Protocol

## Roles

- **Leader**: one accountable owner for goals, budgets, deadlines, routing, and integration. Has no monopoly on truth and cannot be the sole evaluator.
- **Exploration cell**: develops one falsifiable route without reading competitors before commit.
- **Catfish**: a rotating challenger with one protected, bounded falsification ticket per round; no veto or safety bypass.
- **Threat monitor**: records external signals with source, timestamp, TTL, confidence, affected assumptions, and cheapest confirmation.
- **Evaluator**: did not design candidates; evaluates anonymized artifacts using preregistered metrics.
- **Integrator**: integrates one primary winner and may preserve one structurally different fallback.
- **Safety guardian**: may pause only for compliance, security, irreversible damage, or contamination.
- **Human**: approves irreversible actions, exceptional rules, and high-cost external execution.

No agent may be candidate author, sole evaluator, and final approver in the same round.

## Candidate lifecycle

```text
DRAFT_PRIVATE -> COMMITTED -> REVEALED -> ELIGIBILITY_CHECKED
-> CHEAP_FALSIFICATION -> SURVIVED -> PAIRED_BENCHMARK
-> PROMOTED | ELIMINATED | INCONCLUSIVE
```

Commit–reveal provides audit integrity, not operating-system confidentiality. Strong isolation requires separate workspaces, conversations, and access controls.

## Catfish triggers

A challenge request is queued, without automatically starting expensive execution, when:

- fewer than three valid independent hypotheses exist;
- candidates are structurally too similar;
- two failures have the same signature without information gain;
- the dominant route consumes more than 60% of budget without evidence;
- local predictions and external results disagree;
- coordination volume rises while hypothesis count does not;
- the leader rejects two dissenting candidates without falsification;
- a final freeze, external submission, or release is approaching;
- a verified external threat becomes high or critical;
- a human explicitly requests a challenge.

Every challenge must include:

```yaml
challenged_assumption:
alternative_hypothesis:
structural_difference:
predicted_signature:
cheapest_falsification:
max_budget:
stop_condition:
```

## Selection

Eligibility requires compliance, artifact identity, hashes, one hypothesis, a stop condition, reproducible commands, contamination disclosure, and budget fit.

Selection order is fixed: correctness and compliance; registered root cause; external coverage; measured primary metric; robustness; prediction calibration; information gain per cost; reproducibility; and only then complexity.

Give every eligible candidate one cheapest falsification test, eliminate failures and no-information experiments, then use successive halving. Finals should be anonymous, paired, same-environment, and same-budget. Results inside the uncertainty margin are `INCONCLUSIVE`.

## Anti-abuse rules

- No fabricated crisis or permanent emergency.
- No unlimited life support for a failing catfish route.
- No suppression of preregistered dissent in the name of leadership.
- Correlated model personas do not count as independent evidence.
- Message counts and acknowledgements are not intelligence metrics.
- Negative experiments cannot be overwritten by persuasive narratives.
- The protocol itself must be removed or simplified if pilots do not improve useful-signal time, independent hypotheses, attribution, or outcomes.

