# CI Coordinator Organization Control Plane

Status: normative contract; local runtime implemented; external closure deferred

Owner: `ci-coordinator.control-plane`

## Purpose

This package defines the production identity and provider authority model. Its
machine profile separates eight credential planes, defines finite
administrator roles and operations, constrains GitHub App identity and
permissions, and records external observations without promoting them to
readiness evidence. Caller identity and downstream provider-effect authority
remain separate relations.

The canonical cross-cutting design is
[Organization Control Plane](../../architecture/cross-cutting/organization-control-plane.md).
The full delivery order remains the
[Organization Control Plane Implementation Plan](../../features/organization-control-plane-implementation-plan.md).
The implemented local identity and repository-attestation slice, including
implementation-time corrections, is governed by the
[Control-Plane Identity Authority Cutover](../../features/control-plane-identity-authority-cutover.md)
and its
[implementation plan](../../features/control-plane-identity-authority-cutover-implementation-plan.md).
The configuration lifecycle API slice is governed by
[API-First Configuration Lifecycle](../../features/api-first-configuration-lifecycle.md)
and its
[implementation plan](../../features/api-first-configuration-lifecycle-implementation-plan.md).

## Requirement State

| Requirement          | State    | Meaning                                                                                                                            |
|----------------------|----------|------------------------------------------------------------------------------------------------------------------------------------|
| `REQ-CI-CONTROL-001` | blocking | The static machine profile remains internally consistent and fail-closed.                                                          |
| `REQ-CI-CONTROL-002` | deferred | Local Keycloak browser identity exists; live client, rotation, and deployment evidence remain open.                                |
| `REQ-CI-CONTROL-003` | deferred | Local workload-token admission exists; live workload clients and deployment evidence remain open.                                  |
| `REQ-CI-CONTROL-004` | deferred | Exact GitHub App and installation state gates provider readiness.                                                                  |
| `REQ-CI-CONTROL-005` | deferred | Local one-use attestation exists; live callback, provider, and deployment evidence remain open.                                    |
| `REQ-CI-CONTROL-006` | deferred | Production break-glass authority is safety-increasing only.                                                                        |
| `REQ-CI-CONTROL-007` | deferred | Immutable actor attribution survives provider effects.                                                                             |
| `REQ-CI-CONTROL-008` | deferred | Complete API-first configuration and UI parity use one exact transport contract.                                                   |
| `REQ-CI-CONTROL-009` | blocking | The finite configuration lifecycle API, transport profile, runtime routes, OpenAPI, and generated client remain exact projections. |

Each deferred requirement is conjunctive and remains deferred until its local,
provider, deployment, and operational obligations all have exact witnesses.
Implemented local subsets are governed by blocking runtime requirements; they
do not promote absent external evidence into production readiness.

## Machine Sources

- [Requirements](requirements.v1.json)
- [Control-plane profile](control-plane-profile.v1.json)
- [Configuration lifecycle HTTP profile](config-lifecycle-http-profile.v1.json)
- [Proof route](../../../proofkit/routes/control-plane.v2.json)

## Non-Claims

This package does not configure Keycloak, install a GitHub App, authenticate a
live provider response, grant provider write authority, prove deployment,
establish production readiness, or authorize CI omission.
