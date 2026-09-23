# Containing Run Time For Measurement Sources

Status: proposed source-resolution correction.
Owner: `ci_economics`; execution: [plan](run-source-time-plan.md).
Refines the run-created population in
[measured comparisons](ci-economics-measured-comparisons.md#5-independent-sources-and-durable-evolution).

## Decision

Resolve exact attempt identity from the attempt endpoint and containing run
creation from the run endpoint. Admit their relationship before constructing
one canonical `ProviderRunCollectionSource`. Reuse the existing source encoder
for discovery and explicit resolution, preserving its digest and conflict law.
The existing installation transport gains one closed read-only operation.

## Defect And Protected Behavior

The original resolver used the attempt endpoint's `created_at` as containing
run creation. GitHub can return a different run timestamp even for attempt 1;
later reruns make equality still less plausible. Discovery instead reads the
run-list timestamp. Equal source IDs with different full provenance conflict at
registration; a late attempt can also incorrectly renew run-created eligibility.

The owner explicitly excludes reruns of runs outside the source-created window.
This correction restores that boundary. It does not broaden the active window,
change source IDs, ignore conflicts or reinterpret previously stored records.
Previously admitted inconsistent sources remain conflicts until their existing
lifecycle resolves them; no automatic overwrite or first-import reset is allowed.
This compatibility boundary is deliberate and must remain visible to operators.

## Admission Relation

Let A be the exact requested attempt, R a fresh containing-run response, C its
creation time, and W the existing active collection window.

```text
Admit(A, R) => A.repository = R.repository
              and A.runId = R.runId
              and A.headSha = R.headSha
              and 1 <= A.attempt <= R.latestAttempt
Source.attempt = A.identity
Source.runCreatedAt = C
Eligible(Source, now) = C <= now < C + W
```

Both endpoint bodies independently require bounded, noncoerced identity and
timestamp fields. Their request operation, method, path, query, body and API
version remain bound to the exact client operation. Missing, contradictory,
malformed or unavailable evidence yields the existing typed deferral.

The latest run may be nonterminal because of a newer rerun; that does not
change the requested older attempt's identity. Status, conclusion, updated
time and arbitrary provider fields still do not enter source identity. A
changed head is a contradiction, not a request to use the latest attempt.

## Why This Path

Unlike archive collection, explicit registration/report ingestion has no
independently admitted run creation input. A caller timestamp would create
untrusted retention authority. A tolerance has no justified maximum, and
relaxing full-source equality hides contradictions. Reading only the current
run loses exact older-attempt evidence. Searching a complete run list is more
expensive and less direct than its exact resource.

One additional bounded GET is therefore selected under this source contract.
The existing source-construction function remains the only digest owner; no
cache, new service, source DTO, database migration or provider write is added.
Existing enclosing deadlines are unchanged, so slow providers can still defer.
Revisit the extra request if a caller later supplies a separately admitted,
identity-bound run source; a cache requires its own freshness proof.

The [GitHub API](https://docs.github.com/en/rest/actions/workflow-runs#get-a-workflow-run)
documents the separate resource. Provider timestamp accuracy, global availability,
old persisted-data qualification and production latency remain external evidence.
