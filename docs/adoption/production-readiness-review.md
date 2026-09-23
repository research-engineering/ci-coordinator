# Production Readiness Evidence Contract

Status: active admission contract

Date: 2026-07-20

## 1. Decision

Repository correctness is necessary but insufficient for production
enforcement. CI Coordinator is production-ready only when independently owned
repository, deployment, provider, and pilot evidence all refer to compatible
immutable identities.

```text
ProductionReady :=
  RepositoryHeadGreen
  and DeployableArtifactProven
  and DeploymentEnvironmentProven
  and ProviderAuthorityProven
  and PilotSafetyProven
  and OmissionEnablementApproved
```

Unknown is never success. Until every predicate is true, the admissible mode is
local evaluation or non-authoritative shadow planning with native FullCI
fallback.

## 2. Evidence Levels

| Level                | Owner                         | Required evidence                                                                                                                                        | Does not prove                                               |
|----------------------|-------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------|
| L0: local repository | developer workstation         | exact locks, generated-artifact equality, requirements admission, static checks, tests, mutation suites, package and container witnesses                 | remote head, provider execution, deployment                  |
| L1: branch head      | GitHub Actions                | green required `Pull Request Gate` for the exact immutable head                                                                                          | environment credentials, production runtime, omission safety |
| L2: deployment       | platform owner                | digest-addressed image, PostgreSQL migration and ACL installation, probes, metrics scrape, alert loading/delivery, shutdown, recovery, rollback exercise | GitHub ruleset authority or target behavior                  |
| L3: pilot target     | repository owner              | retained native workflows, same-run shadow comparison, timeout fallback exercise, zero unsafe omissions over an approved observation window              | organization-wide rollout                                    |
| L4: enforcement      | organization/repository admin | GitHub App permissions, credentials, ruleset, stable required checks, approved policy epoch, rollback owner                                              | future SLO attainment                                        |

No lower level may be promoted into a higher one. In particular, mocks and
local containers cannot authenticate GitHub administration or a production
deployment.

## 3. Immutable Join Keys

Evidence can be composed only when it binds the same release and target facts:

```text
RepositoryEvidence.branchHead = ProviderEvidence.branchHead
DeploymentEvidence.imageDigest = ReleaseArtifact.imageDigest
DeploymentEvidence.schemaRevision = ReleaseArtifact.schemaRevision
PilotEvidence.repository = ApprovedTarget.repository
PilotEvidence.policyEpoch = ApprovedPolicy.epoch
PilotEvidence.providerRun = NativeFallback.providerRun
```

A missing or mismatched key invalidates the composed claim rather than
degrading it to partial success.

## 4. Repository Release Gate

For a candidate head, the repository owner must prove:

- clean exact dependency locks for the admitted CPython 3.13.15 runtime plus
  the frontend;
- admitted requirements, Proofkit graph, generated artifacts, JSON, and text;
- Ruff, strict mypy, import-boundary, TypeScript, Biome, unit, integration,
  browser, coverage, and mutation witnesses;
- PostgreSQL upgrade/downgrade and runtime-principal boundaries;
- package and container execution without source-tree dependency;
- an immutable remote head with the complete provider gate green.

These witnesses establish candidate integrity. They do not make the candidate
production-ready without L2-L4 evidence.

## 5. Deployment Admission

The platform owner must exercise the published artifact, not a source checkout:

1. verify image digest and package inventory;
2. migrate an empty PostgreSQL database and install the exact runtime ACL;
3. start with production-mode adapters and secret separation;
4. verify liveness, readiness, metrics scrape, alert-rule loading, and alert
   delivery;
5. exercise bounded shutdown, restart recovery, and the documented forward
   rollback procedure;
6. retain redacted evidence bound to artifact digest, schema revision,
   environment, and execution time.

Measured SLOs, retention, capacity, multi-replica behavior, and provider
availability remain deployment-owned facts.

## 6. Provider And Pilot Admission

An administrator must verify the actual GitHub App permission and webhook
matrix from the [container deployment procedure](../how-to/deploy-container.md),
installation scope, credentials, workflow wiring, branch ruleset, and stable
required-check names. A pilot repository must retain its native FullCI path
while shadow evidence proves:

- coordinator and native observations share the exact provider run identity;
- unavailable, stale, malformed, ambiguous, or timed-out coordination selects
  FullCI;
- dynamic omission never suppresses a required witness;
- fallback is exercised, not merely configured;
- rollback ownership and response time are explicit.

Omission enablement requires a separately approved observation window and risk
budget. The coordinator cannot self-authorize this transition.

## 7. Current-State Rule

This document intentionally stores no branch names, transient percentages,
closed defect ledger, or mutable line counts. Current state must come from
fresh receipts for L0-L4. Absent such receipts, the corresponding predicate is
`unknown`, not `passed`.

## 8. Falsifiers

Production readiness is rejected if any of the following is observed:

- a required gate is green for a different commit;
- a deployment runs an unbound image or schema revision;
- provider rules or permissions are inferred from repository files;
- a pilot compares results from different workflow runs or attempts;
- fallback is missing, stale, or unexercised;
- production omission is enabled from local or mocked evidence;
- an evidence owner cannot reproduce or revoke its approval.
