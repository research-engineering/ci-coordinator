# Run a Non-Authoritative Shadow Pilot

Status: current exploratory procedure

Last verified: 2026-07-29

## Outcome and Boundary

Exercise candidate planning, FullCI fallback, provider observation, and durable
shadow projection without omitting native validation.

The adopted target-workflow contract binds the candidate and its FullCI or
selected static jobs to one authenticated workflow run id and attempt. This
procedure can
therefore produce same-run shadow evidence when the installed target artifacts
and provider observations satisfy that exact identity. It remains
non-authoritative until the deployment, fallback, rollback, stable-gate,
duration, sample, and owner predicates are also proven.

```text
ExploratoryShadow := CandidateComputed and NativeCIUnchanged and EvidenceRetained
AdmittedSameRunShadow := ExploratoryShadow and ExactRunAttemptObserved

ExploratoryShadow does not imply EnforcementReady
```

The evidence gate is owned by [Shadow Mode](../architecture/modules/shadow-mode.md)
and [Production Admission](../architecture/cross-cutting/production-admission.md).

## Preconditions

- an immutable coordinator artifact running in `non_enforcing` mode;
- admitted PostgreSQL schema and runtime principal;
- GitHub App, webhook, OIDC, signing, and operator settings scoped to a sandbox
  repository;
- an unchanged native Full Check with its existing required status;
- named repository, platform, and rollback owners;
- a frozen inventory of triggers, jobs, check identities, credentials, runner
  profiles, services, and fallback commands.

Select the first real pilot only after explicit repository-owner consent and
fresh scope admission. The [three-pilot assessment](../adoption/three-repository-assessment.md)
contains synthetic archetypes, not inherited authorization or executed runs.
Changing pilot order does not change any admission predicate.

## 1. Precommit the Evidence Profile

Create a reviewed profile artifact that names every surface, minimum safe
comparison count, minimum observation duration, maximum evidence age, pilot
repository, and immutable coordinator artifact. Compute its lowercase SHA-256
digest before the first observation and set:

```sh
export CI_COORDINATOR_SHADOW_ROLLOUT_PROFILE_ID='<64-lowercase-hex-digest>'
```

The runtime stores this identity, not the profile contents. Changing thresholds
requires a new identity and a new evidence window.

## 2. Register an Observe-Only Policy

Prepare a repository-specific policy from the frozen inventory. Keep provider
enforcement unchanged and register and activate it with
[Manage Repository Policy Epochs](manage-repository-policy.md).

Stop when any workflow identity, credential profile, runner class, dependency
graph fact, or required check is unknown. Unknown evidence must resolve to
FullCI, not an optimistic default.

## 3. Add Target Wiring Without Removing Full Validation

Use [Universal Workflow Adoption](../features/universal-workflow-adoption.md)
and, for generated witness shards, the
[Bootstrap Workflow Contract](../bootstrap-workflow-contract.md) as inputs to a
repository-owned change. Prefer an in-place `native-job-set` adapter when the
existing workflow already has stable static jobs and an aggregate gate. Bind
the exact repository, installation, workflow ref, OIDC audience, signing key,
plan URL, execution registry, and FullCI route.

Do not add path filters, remove native triggers, change required checks, or
grant the coordinator publication authority. Provider wiring requires a
repository or platform administrator.

## 4. Exercise the Failure Matrix

For pull request, push, merge group when enabled, and manual smoke events,
exercise:

- valid OIDC and signed FullCI fallback;
- missing or unreachable coordinator URL;
- timeout, malformed response, invalid signature, wrong key id, and expired
  envelope;
- GitHub App or JWKS unavailability;
- stale policy, graph, workflow, or runner evidence;
- replayed request and conflicting request identity.

Every case must leave native validation unchanged. A failure that becomes a
green optimized result invalidates the pilot.

## 5. Observe Through Metrics And Durable Read Models

Use current endpoints. Provision the protected metrics header file as
described in [Operate Production Observability](operate-production-observability.md#verify-the-signal-path);
it contains the deployment-owned bearer and must remain outside source and
shell history. A connected metrics request without it is unauthorized:

```sh
curl --fail-with-body "${COORDINATOR_URL}/healthz"
curl --include "${COORDINATOR_URL}/readyz"
curl --fail-with-body --header @/run/secrets/ci-coordinator-metrics-header \
  "${COORDINATOR_URL}/metrics"
```

Inspect `ci_coordinator_shadow_comparisons_total`,
`ci_coordinator_shadow_unsafe_omissions_total`, and
`ci_coordinator_shadow_replay_mismatches_total`. Use the authenticated
repository workbench for bounded plan, reconciliation, override, epoch, audit,
and replay projections, then validate the complete durable audit ledger when
the investigation requires it:

```sh
uv run --project backend --frozen ci-coordinator-audit-replay --all
```

The service registry is process-local while the deployment-owned Prometheus
time series is external. Preserve provider run URLs and pilot receipts
separately; neither metrics nor the bounded workbench prove complete historical
sample retention.

## 6. Close or Abort

Abort on any unsafe omission, replay mismatch, missing expected signal,
identity ambiguity, stale evidence, unavailable fallback, or provider drift.
Remove coordinator routing without reconstructing the FullCI path.

Treat the pilot as production-admission evidence only when the workbench and
durable replay confirm exact run-attempt identity and the external owner also
provides fallback-chaos, stable-gate, rollback, non-vacuous observation-window,
and approval evidence. Local counters or an unscoped provider URL are not a
substitute for those predicates.
