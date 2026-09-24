# Verification Core Module Specification

Status: module specification

Date: 2026-09-25

## 1. Owned Invariant

`verification_core` owns independent deterministic-plan admission, proof
validation, and monotonic coverage semantics. It cannot create a smaller plan
than the supplied deterministic candidate.

## 2. Public API

```text
verify(input, policy, deterministic_plan, agent_advice?) -> VerifiedPlan
admit_deterministic_plan(input, policy, deterministic_plan) -> rejection_reason?
compare_coverage(
  baseline_obligations,
  baseline_witnesses,
  candidate_obligations,
  candidate_witnesses,
) -> CoverageComparison
validate_omission_proof(input, policy, omitted_obligation) -> bool
```

## 3. Verifier Laws

```text
Current coordinates and a complete catalog partition are required.
Every omission must pass independently derived impact and proof predicates.
FullCI candidates must select the complete catalog at full depth.
Stale, incomplete, or tampered deterministic_plan => FullCI fallback.
AcceptedAgentAdvicePlan >= DeterministicPlan.
Rejected advice must not mutate deterministic proof.
```

The admission implementation must not call or import planner orchestration,
the planner impact analyzer, the omission-proof builder, or planner witness
closure. It may share canonical immutable value contracts, hashing, ordering,
and path matching. Selecting more obligations than the minimum safe set is
admissible; weakening selected coverage or emitting an unproved omission is
not.

Fallback and post-advice construction use verifier-owned witness closure,
not planner builders. `VerifiedPlan` independently requires a complete catalog
partition and exact catalog witness closure; a triggered fallback selects every
obligation at least at its full depth. Advice may strengthen that depth within
catalog support. A structurally consistent but partial planner fallback cannot
become a verified fallback. Canonical plan value types and hashing remain shared;
this is mechanism independence, not isolation of Python package loading.

The public `validate_omission_proof` also checks current config epoch, compiled
policy and policy hashes, and recomputes input/diff identities. Calling this
entrypoint directly cannot bypass context checks owned by `verify`.

## 4. Agent Advice Admission

Accepted advice may:

- select known obligations.
- increase depth within catalog support.
- recommend FullCI.
- add bounded risk findings mapped to known obligations.

After witness closure, the complete runner, permission, credential, fixture,
service, and capacity-class authority set must remain a subset of the
deterministic plan's authority set. Expansion produces an audited FullCI
fallback.

Rejected advice includes any attempt to:

- omit checks.
- remove checks.
- lower depth.
- broaden credentials.
- disable fallback.
- reference unknown checks, fixtures, models, prompts, or risk classes.
- provide stale or self-asserted execution provenance.

## 5. Proof Obligations

| Obligation                              | Falsifier                                                |
|-----------------------------------------|----------------------------------------------------------|
| Independent admission is authoritative. | A planner defect reproduced during replay is accepted.   |
| Classification is complete.             | A catalog obligation is neither selected nor omitted.    |
| Agent monotonicity holds.               | Advice lowers selected depth.                            |
| Agent authority is bounded.             | Added coverage introduces a new execution profile tuple. |
| Proof validation is input-bound.        | Proof from another graph is accepted.                    |
| Failure is conservative.                | Invalid proof produces optimized plan.                   |

## 6. Implementation Mapping

Current pure-core files:

```text
ci_coordinator/verification_core/coverage.py
ci_coordinator/verification_core/deterministic_admission.py
ci_coordinator/verification_core/model.py
ci_coordinator/verification_core/verifier.py
ci_coordinator/verification_core/witnesses.py
```

## 7. Acceptance Tests

- requirement-bound unit falsifiers for current verifier behavior.
- same-defect planner replay rejection.
- incomplete partition and witness-closure rejection.
- stale deterministic plan rejection.
- advice downgrade rejection matrix.
- proof tamper and graph mismatch tests.
