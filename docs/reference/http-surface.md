# HTTP Surface

Use this summary to find common HTTP operations. It is not an exhaustive
catalog: route families are composed conditionally by the
[HTTP application](../../backend/src/ci_coordinator/api/http/app.py), according
to the configured [runtime mode](../../README.md#runtime-modes).
The [API and HTTP specification](../architecture/modules/api-http.md) owns
transport and authentication boundaries.

The retained [workbench OpenAPI](../../frontend/openapi/workbench.openapi.json)
is a generated machine-readable **UI-only projection**, not the complete
public API or deployment configuration. It includes deliberately weakened
recursive-value schemas for the TypeScript consumer; Python runtime admission
remains authoritative. The [exporter](../../scripts/frontend_contract.py)
uses the same application composition root. Runtime `/openapi.json`, Swagger
and ReDoc are deliberately disabled; downloading this file does not require
enabling executable documentation on a session-bearing origin.

For operations outside that projection, use the table below and the
[plan issuance](../architecture/modules/plan-issuance.md),
[webhook ingestion](../architecture/modules/github-ingestion.md),
[operator control](../architecture/modules/operator-controls.md) and
[production authority](../how-to/stage-and-activate-production-authority.md)
contracts. Schema presence does not establish credentials, provider wiring or
production readiness.

| Method | Path                                                                                     | Purpose                                                                      |
|--------|------------------------------------------------------------------------------------------|------------------------------------------------------------------------------|
| `GET`  | `/healthz`, `/api/v1/health`                                                             | Process liveness                                                             |
| `GET`  | `/readyz`, `/api/v1/ready`                                                               | Dependency-derived readiness                                                 |
| `GET`  | `/metrics`                                                                               | Bounded Prometheus text exposition                                           |
| `POST` | `/webhooks/github`                                                                       | HMAC-authenticated delivery ingestion                                        |
| `POST` | `/api/v1/dynamic-ci/plan`                                                                | OIDC-bound signed FullCI-safe plan issuance                                  |
| `POST` | `/api/v1/config/validations`                                                             | Validate policy source without persistence or provider effects               |
| `POST` | `/api/v1/config/epochs`                                                                  | Register an immutable policy epoch                                           |
| `POST` | `/api/v1/config/activations`                                                             | Activate an admitted epoch                                                   |
| `POST` | `/api/v1/config/rollbacks`                                                               | Roll back the active epoch                                                   |
| `GET`  | `/api/v1/config/repositories/{installation_id}/{repository_id}/status`                   | Read active state and one bounded epoch page                                 |
| `GET`  | `/api/v1/config/repositories/{installation_id}/{repository_id}/epochs/{epoch_id}/source` | Export exact re-admitted source bytes                                        |
| `POST` | `/api/v1/overrides/full-ci`                                                              | Force one subject to FullCI, latch omission off, or release an exact latch   |
| `POST` | `/api/v2/economics/observation`                                                          | Configure or pause revision-bound continuous observation                     |
| `GET`  | `/api/v2/economics/repositories/{installation_id}/{repository_id}/observation`           | Read saved configuration, bounded scan progress and capacity                 |
| `GET`  | `/api/v2/economics/repositories/{installation_id}/{repository_id}/observation/gaps`      | Read a bounded retained gap page                                             |
| `GET`  | `/api/v2/economics/repositories/{installation_id}/{repository_id}/observation/workflows` | Read one authorized provider workflow-choice page                            |
| `GET`  | `/api/v1/auth/keycloak/start`                                                            | Start exact-callback Keycloak browser authorization                          |
| `GET`  | `/api/v1/auth/keycloak/callback`                                                         | Complete Keycloak authorization and create an opaque session                 |
| `GET`  | `/api/v1/auth/session`                                                                   | Read the bounded same-origin browser session projection                      |
| `POST` | `/api/v1/auth/keycloak/logout`                                                           | Delete the current session with origin and CSRF proof                        |
| `POST` | `/api/v1/auth/keycloak/backchannel-logout`                                               | Consume one verified Keycloak logout token                                   |
| `GET`  | `/api/v1/workbench/installations`                                                        | List the control-plane-authorized GitHub App organization catalog            |
| `GET`  | `/api/v1/workbench/installations/{installation_id}/repositories`                         | List one authorized installation's bounded repository page                   |
| `GET`  | `/api/v1/workbench/repositories/{installation_id}/{repository_id}`                       | Read one authorized bounded proof snapshot                                   |
| `GET`  | `/api/v1/workbench/repositories/{installation_id}/{repository_id}/workflow-discovery`    | Read exact-commit workflow evidence and an observe-only proposal or blockers |
| `POST` | `/api/v1/repository-attestations/github/start`                                           | Bind one proposal to a Keycloak session and start GitHub reviewer step-up    |
| `GET`  | `/api/v1/repository-attestations/github/callback`                                        | Consume the one-use step-up and retain one exact review receipt              |

Raw-body limits run before FastAPI parsing or authentication. HTTP DTOs do not
cross into the domain model.

## Operator Override Requests

`POST /api/v1/overrides/full-ci` accepts a closed `kind` union, not a generic
override object. Every request has `schemaVersion: "operator-override/v1"`,
positive JSON-safe integer `installationId` and `repositoryId`, and nonempty
`operationId` and `reason` (each at most 512 UTF-8 bytes). The body limit is
16 KiB. Unknown fields, including caller-supplied `actor`, are forbidden.

| `kind` | Additional required fields | Authority and lifetime |
| --- | --- | --- |
| `force_full_ci` | `subjectId` (1--512 UTF-8 bytes), `expiresAt` (ISO-8601 text with timezone) | `override` role or break-glass; expiry must be future for a new command |
| `disable_omission` | None; omit `subjectId`, `overrideId`, `expiresAt` | `override` role or break-glass; repository-wide, no expiry |
| `enable_omission` | `overrideId`, matching `^override_[0-9a-f]{32}$` | Both `activate` and `override`; exact latest retained disable, no expiry; never break-glass |

Expiry text uses `YYYY-MM-DDTHH:MM:SS`, optional fractional seconds (1--6
digits), and `Z` or a `+HH:MM`/`-HH:MM` offset. Numeric timestamps and
timezone-free values are not admitted commands.

All three also require independently admitted repository scope. Exactly one
credential plane is allowed: browser session with configured `Origin` and
`X-CSRF-Token`, or a Keycloak workload/emergency bearer without any `Cookie`
or `Origin`. Mutations require exactly `Content-Type: application/json`.

`202` returns `{"ok":true,"overrideId":"override_<32 lowercase hex>","duplicate":false}`.
An exact authorized replay returns the same ID and `duplicate:true`; changing
actor or command facts under the same scope/operation ID conflicts. A replay
does not renew an expired force override. See the
[emergency runbook](../how-to/emergency-controls.md) for exact-subject selection,
requests, failure handling and latch release. The
[operator-controls contract](../architecture/modules/operator-controls.md)
owns transitions; no successful override creates production authority.

## Related Operations

- [Production authority inspection, staging and activation](../how-to/stage-and-activate-production-authority.md).
- [Configure provider identity](../how-to/configure-provider-identity.md).
- [CI source discovery, measurements, reports and comparisons](../how-to/measure-and-compare-ci.md).
- [Enable and pause repository observation](../how-to/observe-repository-runs.md).
- [Governance observation and comparison](../architecture/modules/governance-comparison.md).
- [Documentation index](../INDEX.md).
