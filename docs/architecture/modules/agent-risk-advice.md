# Agent Risk Advice Module Specification

Status: pure admission and monotonic application implemented

Date: 2026-07-20

## 1. Decision

Agent output is untrusted advisory evidence. It may only preserve or increase
the deterministic validation plan. It cannot remove proof, expand execution
authority, mutate policy, or disable fallback.

```text
AdmittedEffect(D, A) => Coverage(Apply(D, A)) >= Coverage(D)
AdmittedEffect(D, A) => Authority(Apply(D, A)) subseteq Authority(D)
```

Any advice path that cannot establish both predicates is rejected or converted
to FullCI. Therefore model failure can increase cost but cannot create an
unsafe omission.

This is the maximal safe authority under the deterministic-floor invariant:
semantic analysis may discover additional risk, but permission to remove
coverage or expand execution authority would let one incorrect answer weaken
proof or privilege boundaries.

## 2. Trust Boundary

| Fact                                          | Authority                            |
|-----------------------------------------------|--------------------------------------|
| diff, policy, catalog, deterministic plan     | deterministic coordinator input      |
| raw output, model id, prompt hash, input hash | trusted model executor envelope      |
| evaluator id, policy hash, verdict            | trusted evaluation executor envelope |
| requested validation increases and rationale  | untrusted model JSON                 |
| final coverage and execution authority        | deterministic verifier               |

The model output cannot self-assert its model, prompt, input, or evaluation
identity. `AdviceExecutionEnvelope` carries those facts outside the model JSON
and binds evaluation evidence to `sha256(raw_output)`.

The current repository provides the typed envelope and verifier, but no model
executor. Consequently local tests prove admission behavior, not that a future
executor or evaluator is independent, authentic, available, or durable.

## 3. Public API

```text
build_advice_input(input, deterministic_plan) -> AdviceInputPackage
admit_advice(envelope, input=..., policy=...) -> AdmittedAdvice | RejectedAdvice
verify(input, policy, deterministic_plan, advice_envelope?) -> VerifiedPlan
```

## 4. Admission Contract

Advice is admitted only when all terms hold:

```text
Enabled(policy)
and StrictJson(raw_output)
and Size(raw_output) <= 64 KiB
and SchemaVersion = agent-risk-advice/v2
and EnvelopeInputHash = PlanningInputHash
and ModelId in ModelAllowlist
and PromptHash in PromptAllowlist
and Confidence >= MinimumConfidence
and RequiredEvaluationPassed
and EveryReferencedObligationKnown
and EveryRequestedDepthSupported
```

Strict JSON rejects duplicate keys and non-finite numbers. Text and collections
have explicit byte or cardinality bounds, and set-like arrays must be unique and
UTF-16 canonically ordered.

## 5. Allowed Effects

- select a known omitted obligation at its policy-defined default depth;
- increase a known obligation to a supported depth;
- attach a bounded risk finding to a known obligation, thereby selecting it;
- recommend FullCI.

The verifier closes required witnesses after applying advice. It then compares
the complete execution-authority tuple:

```text
(runner, permissions, credentials, fixtures, services, capacity class)
```

The resulting tuple set must be a subset of the deterministic tuple set.
Otherwise the result is an audited FullCI fallback, not an expanded plan.

## 6. Rejected Effects

- omit or remove checks;
- lower validation depth;
- reference unknown obligations or unsupported depths;
- introduce fields for credentials, fixtures, policy mutation, or fallback
  suppression;
- rely on stale input, unapproved model or prompt identity, failed evaluation,
  malformed JSON, or an oversized response;
- produce less or incomparable coverage after witness closure.

## 7. Failure Semantics

Malformed or policy-rejected advice leaves the independently admitted deterministic plan
unchanged and records canonical reason codes. A post-admission authority or
coverage violation produces FullCI and records the advice as rejected. Advice
absence also leaves the deterministic plan unchanged.

## 8. Implementation And Proof

```text
ci_coordinator/agent_risk_advice/model.py
ci_coordinator/agent_risk_advice/admission.py
ci_coordinator/verification_core/verifier.py
```

Required falsifiers cover strict parsing, byte and collection bounds,
input/model/prompt/evaluation mismatch, unknown obligations, unsupported depth,
coverage downgrade, authority expansion, FullCI recommendation, audit identity,
and deterministic behavior without advice.

## 9. Deferred Capability

The following work is required before model execution can be enabled:

1. define a bounded, versioned request projection over the diff, impact graph,
   deterministic plan, and policy vocabulary;
2. implement a trusted executor that supplies provenance independently of model
   output and enforces timeout, retry, byte, and cost budgets;
3. implement an independently governed injection and downgrade evaluator bound
   to the exact output bytes;
4. persist accepted and rejected envelopes, reason codes, hashes, and replay
   linkage to the deterministic plan;
5. expose redacted explanations only as non-authoritative read models; and
6. validate outage, stale input, malformed output, evaluator failure, replay,
   and model or prompt drift.

```text
Agent disabled or absent          -> deterministic plan
Future executor unavailable       -> deterministic plan
Advice rejected by admission      -> deterministic plan + rejection evidence
Authority or coverage violation   -> FullCI + rejection evidence
Safe advice admitted              -> monotonic verified plan
```

A future policy that requires model execution must separately specify and
witness its timeout and FullCI fallback. Model invocation, trusted evaluator
orchestration, durable evidence retention, provider retry policy, and operator
explanations remain outside the current module and must not be inferred from
its tests.
