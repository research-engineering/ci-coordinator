# HTTP Surface

Use this summary to find common HTTP operations. It is not an exhaustive
catalog: route families are composed conditionally by the
[HTTP application](../../backend/src/ci_coordinator/api/http/app.py), according
to the configured [runtime mode](../../README.md#runtime-modes).
The [API and HTTP specification](../architecture/modules/api-http.md) owns
transport and authentication boundaries.

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
| `POST` | `/api/v1/overrides/full-ci`                                                              | Create a scoped force-FullCI override                                        |
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

## Related Operations

- [Production authority inspection, staging and activation](../how-to/stage-and-activate-production-authority.md).
- [CI source discovery, measurements, reports and comparisons](../how-to/measure-and-compare-ci.md).
- [Enable and pause repository observation](../how-to/observe-repository-runs.md).
- [Governance observation and comparison](../architecture/modules/governance-comparison.md).
- [Documentation index](../INDEX.md).
