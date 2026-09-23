# CI Coordinator Runtime Specification

Status: active requirement package

Owner: `ci-coordinator.runtime`

## Purpose

This package owns the supported Python runtime contract and its fail-closed
external boundaries.

The runtime contract is intentionally narrower than the product roadmap. It
covers webhook admission, non-enforcing policy evaluation, reconciliation,
persistence, audit replay, dynamic CI evidence ingestion, signed fallback plan
issuance, bootstrap envelope validation, exact interpreter admission, target
artifact compilation, exact-revision provider workflow admission, and the
authenticated repository-scoped workbench snapshot API. It also owns the
authorization-preserving read-only GitHub App organization and repository
inventory boundary and the authorization-first exact-commit workflow discovery
and conservative proposal operation. It also owns a bounded read-only
observation of active rules applying to the current default branch, with exact
repository rebinding and an explicit unbaselined result. An explicitly enabled
control plane adds opaque token-free Keycloak-backed human sessions, bounded
Keycloak workload tokens, exact role admission, one-use proposal-bound GitHub
reviewer attestation, and separately authorized configuration activation with a
current GitHub App permission recheck. It also owns bounded Prometheus
exposition, service correlation, redacted request diagnostics, and
repository-defined alert-rule syntax. Witness-shard planning may additionally consume a
bounded request-local observation of exact-selector-eligible self-hosted
runners; this observation can change parallelism only and is never a
reservation or omission authority. The deployment boundary additionally includes
fenced installation and verification of the exact PostgreSQL runtime object
ACL while leaving role and secret creation to the platform owner.

The package also owns the provider-independent consumer-contract profile and
scenario schemas. Those schemas are structural preflight; the runtime codec
owns exact canonical admission. Their local receipt proves exact
coordinator-to-target control-flow composition only; it is not provider or
production evidence.

The dormant target-authority slice additionally owns a stable Git-object
workflow manifest, exact source-binding provenance, subject-closed target and
provider source groups, and separately attributed registration and observation
producers. It remains in-memory and unactivated; the broader production
successor protocol remains deferred.

## Authority

Recorded-gap recovery extends historical administration with an exact,
actor-bound bounded retry. The [design](../../features/history-gap-recovery.md)
and [plan](../../features/history-gap-recovery-plan.md) distinguish immutable
failure observations, current retained headers and explicit queue admission.
`REQ-CI-RUNTIME-055` owns this relation; no completeness or planning authority
follows from a retry receipt.

The machine-admissible source is `requirements.v1.json`. This overview explains
context only; it does not create additional normative requirements.

Formal authority order for active blocking requirements:

```text
requirements.v1.json
  -> proofkit/requirement-bindings.json
  -> native witness tests and scripts
  -> runtime code
```

A deferred requirement instead binds its owner, risk acceptance, review
condition, expiry, and design evidence in `requirements.v1.json`. It has no
proof binding and makes no implementation claim until one atomic change moves
it to blocking and installs its complete witness and runtime route.

## Non-Claims

This package does not claim production dynamic omission, live PostgreSQL
readiness, distributed tracing, a live
Prometheus or Alertmanager installation, SLO attainment, or deploy approval.
