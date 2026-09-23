# History Time Authority

> Public-export boundary: historical source, PR, run, provider and rollout
> observations retained below are design context only, not acceptance evidence
> for `research-engineering/ci-coordinator`. Former private receipts are revoked.
> Synthetic pilot archetypes are proposed examples, not renamed executions.
> Requalify applicable requirements and open tasks against the new exact source.

Status: proposed correction of the archive provider boundary.
Owner: `ci_economics`; execution: [plan](history-time-authority-plan.md).
Refines [history population](actions-history-population.md) and its
[recent recovery](actions-history-recent-recovery.md) source binding.

## Decision

Keep workflow-run creation and attempt creation separate. The admitted
discovery source or event hint owns the run creation instant. Pass that instant
explicitly through `HistoryAttemptProvider`; do not obtain it from the attempt
endpoint. The provider's private decoded header retains the attempt creation
instant independently, so bracketing still compares both exact attempt headers.
Persist only the existing public archive statistics, with their correct
`runCreatedAt`. No wire schema or database migration is needed.

## Counterexample

A synthetic counterexample gives the run endpoint and attempt endpoint start
timestamps differing by one second while repository, run, head, workflow and
completed jobs agree. Treating those endpoint timestamps as one authoritative
field can exhaust retries through `provider_binding_mismatch`. This is a
contract scenario, not an observation that a synthetic pilot actually ran.

[GitHub documents separate run and attempt resources](https://docs.github.com/en/rest/actions/workflow-runs#get-a-workflow-run-attempt).
Neither that API distinction nor the observations justify a universal equality
between their creation timestamps. Later attempts can be created much later.

## Invariants

Let S be the admitted run source bound to repository/run identity, H1/H2 the
two exact attempt observations, and A the resulting archive contribution.

```text
Admit(A) => CurrentSource(S) and ExactAttemptIdentity(H1, S)
            and H1 = H2 and ExistingJobPopulationGuards
A.runCreatedAt = S.runCreatedAt
H1.attemptCreatedAt = H2.attemptCreatedAt
S.runCreatedAt = H1.attemptCreatedAt is not a required premise
```

`CurrentSource` means the existing durable page/hint and current claim, not an
arbitrary caller timestamp. Application admission still compares returned
scope, run, attempt, workflow and run creation against that source; persistence
still checks the current lease, generation, revision and source relations.
Changing any of those independently remains a rejection. Provider input must
be an exact aware datetime, normalized by the existing UTC value admission
before any I/O. Invalid attempt timestamps remain malformed provider evidence.

The private immutable decoded header is justified by the different temporal
authorities: it keeps attempt stability evidence out of the public run-time
projection. Its removal would either conflate the two meanings or lose the
existing attempt-time stability guard. No new domain layer is introduced.

## Alternatives And Limits

- Exact time equality rejects the witnessed valid source combination.
- A tolerance invents an unsupported bound and fails delayed reruns.
- Removing the application check admits source-time substitution by an adapter.
- Fetching the run again can corroborate its time, but adds an HTTP request and
  failure path per attempt for a value already admitted by the source owner.
- Reusing that owner value preserves the current request count and keeps the
  trust transition explicit. It is preferred under the existing source contract,
  not claimed universally optimal for arbitrary providers or untrusted callers.

Revisit if the source no longer owns run creation, sources become revisioned or
incomparable, or the provider reassigns run identities. This change does not
resolve active-evidence source-time semantics outside the archive, retroactively
erase gap records, extend retention, or prove full history/production capacity.
After qualified deployment, existing non-destructive rescan may revisit exhausted
work while preserving generation, contributions and first-detail-import clocks.
