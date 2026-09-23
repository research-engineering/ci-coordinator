# Shadow Mode Module Specification

Status: module specification

Date: 2026-07-13

## 1. Owned Invariant

Candidate omissions are measured against actual validation outcomes without
changing required validation behavior.

## 2. Public API

```text
record_candidate(plan, actual_execution) -> ShadowCandidate
compare_full_ci(candidate, observation) -> ShadowComparison
classify_unsafe_omission(comparison) -> UnsafeOmissionFinding | SafeObservation
```

## 3. Required Shadow Record

```text
repo
event
base_sha
head_sha
config_epoch
policy_hash
diff_hash
graph_hash
baseline_plan
candidate_plan
coverage_relation
actual_full_ci_result
unsafe_candidate
```

`event` is the immutable source-decision identity within `repo`; it is not a
display label or an arrival timestamp. A durable evidence key is therefore:

```text
ShadowEvidenceKey = (profile_id, repo, event, surface)
```

The key intentionally excludes policy, graph, plan, and outcome values. A
replay with those same values is a duplicate; a replay with any different
semantic value is a conflict. Treating a changed plan or outcome as another
sample for the same source decision would let retries hide a contradiction or
inflate the evidence count.

Only terminal comparisons are retained as rollout evidence:

```text
Recordable(comparison) iff comparison.classification != unknown
```

`unknown` remains a fail-closed live outcome, but has no final FullCI fact to
retain. Persisting it as a blocked historical record would make a later
completed FullCI comparison permanently unusable without proving an unsafe
omission. `unsafe` and `replay_mismatch` are terminal blockers and are retained
under their same immutable key; a later incompatible write is a conflict, not
an overwrite.

## 4. Gate Before Enforcement

```text
EnforcementGate :=
  ProductRequirementsAdmitted
  and NativeWitnessesPassedAtArtifact
  and ShadowRolloutProfileSatisfied
  and ProviderIntegrationBoundToArtifact
  and RollbackExercisePassed
  and StableFullCIFailoverProven
```

The conjunction is indivisible: local proof cannot substitute for provider,
rollback, or failover evidence. `ShadowRolloutProfileSatisfied` requires the
precommitted profile identity, positive per-surface sample counts, observation
duration, and evidence freshness owned by this module.

For each enabled surface `s`, the rollout profile owns positive values
`minimumComparisonCount(s)`, `minimumObservationSeconds(s)`, and
`maximumEvidenceAgeSeconds(s)`. Evidence is sufficient iff it has the same
profile identity, contains no non-safe comparison for `s`, has at least the
required safe count, spans the required duration, and its newest safe record is
not older than the maximum age. Evidence from another profile never contributes
to the count. This makes a zero-traffic window and a post-hoc threshold change
insufficient by construction.

The runtime receives the lowercase SHA-256 `profile_id` of that precommitted
profile as immutable process wiring before observations begin. It stores the id
in every candidate reconciliation contract and every durable evidence key.
`config_epoch` remains a separate replay fact and must never substitute for the
rollout profile id:

```text
Evidence.profile_id = PrecommittedProfile.profile_id
and Evidence.config_epoch = ActivePolicy.epoch_id

Evidence.profile_id = Evidence.config_epoch => invalid unless equality is
independently established by the profile owner
```

## 5. Failure Behavior

```text
MissingFullCiObservation => shadow unknown
ReplayMismatch => block enforcement
UnsafeOmissionCandidate => block enforcement for that surface
StaleConfigEpoch => ignore or classify separately
```

## 6. Proof Obligations

| Obligation                             | Falsifier                                  |
|----------------------------------------|--------------------------------------------|
| Shadow mode does not skip real checks. | Candidate omission alters workflow matrix. |
| Unsafe candidate blocks enforcement.   | Known unsafe surface is enabled.           |
| Missing evidence is unknown.           | Missing FullCI is counted as safe.         |
| Shadow record is replayable.           | Candidate lacks policy or graph hash.      |

## 7. Implementation Mapping

Target files:

```text
ci_coordinator/shadow_mode/candidate.py
ci_coordinator/shadow_mode/comparator.py
ci_coordinator/shadow_mode/unsafe_omission.py
ci_coordinator/shadow_mode/metrics.py
ci_coordinator/shadow_mode/use_cases.py
```

## 8. Acceptance Tests

- safe and unsafe omission fixtures.
- missing FullCI evidence tests.
- replay mismatch enforcement-blocking tests.
- non-mutating shadow execution tests.
- exact shadow-evidence replay, semantic conflict, and timestamp non-inflation
  tests over a real PostgreSQL restart.
- runtime registration test proving persisted evidence uses the configured
  rollout-profile identity rather than the active config epoch identity.
