# Deploy The Container Artifact

Status: as-built deployment procedure; artifact publication and production admission required separately

Source reviewed: 2026-09-25; live deployment qualification not claimed

## Outcome

Deploy one digest-addressed CI Coordinator image, migrate PostgreSQL with a
separate principal, start safely in `non_enforcing`, and optionally restart in
receipt-gated `enforcing` mode after external admission while retaining a
bounded FullCI rollback path.

```text
DeploymentProcedureComplete does not imply ProductionAdmissionComplete
```

Provider wiring, live fallback exercises, shadow evidence, stable required
checks, and deployment SLOs remain governed by
[Production Admission](../architecture/cross-cutting/production-admission.md).

The public [Releases API](https://api.github.com/repos/research-engineering/ci-coordinator/releases)
returned no published releases on 2026-09-25. Consult
[Releases](https://github.com/research-engineering/ci-coordinator/releases) and
the exact publication receipt before proceeding; neither an example GHCR path
nor an empty release list proves what exists in a registry. For source-based
evaluation without an admitted artifact, use the [local guide](evaluate-locally.md).
Configure [provider identity](configure-provider-identity.md) before normal
authenticated operation; keep [emergency controls](emergency-controls.md)
available to the deployment owner.

## 1. Freeze The Artifact

Dispatch the repository-owned `Release Artifact` workflow on exact `master`
after an explicitly dispatched `Full Check` succeeds for that same commit.
Both events must satisfy the current publication contract; an older push or
PR result is not a substitute. The release workflow builds the root
`Dockerfile`, embeds the production build identity, publishes maximum BuildKit
SLSA v1 provenance and an SPDX 2.3 SBOM, validates their exact registry bytes
against the pinned release profiles, and verifies GitHub attestations. Record
its GHCR manifest reference by digest:

```sh
export CI_COORDINATOR_IMAGE='ghcr.io/research-engineering/ci-coordinator@sha256:<digest>'
docker pull "${CI_COORDINATOR_IMAGE}"
```

This is a destination example, not a publication receipt. Resolve an admitted
image and digest only after the new publisher has been independently qualified.

Do not deploy a mutable tag as artifact identity. The same digest must be used
for migration, runtime, rollback evidence, and external admission receipts.

The authoritative publication procedure is specified by
[Release Artifact Publication](../architecture/cross-cutting/release-artifact-publication.md).
A local `docker build` remains useful for development but does not establish a
registry digest, provider attestation, or production artifact. Enforcing
startup rejects a development build identity even when all process settings
are present.

When using Swarm with a declarative stack manager such as Portainer, observe
both the live service image/configuration and the saved stack declaration
before an update, redeploy or rollback. An image-only service update does not
update the declaration and a later full-stack redeploy can restore its old
image. Reconcile the intended digest, environment, secret references, networks
and placement with the deployment owner; do not overwrite unexplained drift.
Afterwards verify the saved declaration and the actually running task digest
agree. Record the same checks for rollback; an updated service alone does not
prove declarative synchronization.

## 2. Migrate With The Migration Principal

Supply the migration DSN through the deployment secret provider. It must use
the `postgresql+psycopg` driver and a principal authorized for the migration
protocol.

```sh
export CI_COORDINATOR_MIGRATION_DATABASE_DSN='postgresql+psycopg://...'
docker run --rm \
  --env CI_COORDINATOR_MIGRATION_DATABASE_DSN \
  --entrypoint alembic \
  "${CI_COORDINATOR_IMAGE}" upgrade head
```

The default image configuration points to an invalid placeholder and cannot
migrate without the explicit deployment secret. Migration acquires the
exclusive compatibility fence and commits DDL, declaration, and Alembic head
movement atomically.

Before the first product release, an earlier development database is not a
supported migration origin. If it was created from different bytes under an
existing revision identifier, delete and recreate that development database
before running `upgrade head`; Alembic cannot replay a revision already recorded
as applied. The first release freezes the supported migration baseline and all
later schema changes use forward revisions.

If this release changes the exact runtime object allowlist, stop every old
runtime replica and prevent automatic restart before Step 3. For one shared
runtime role, the old and new exact grant sets cannot both hold at once. A
zero-downtime mixed-version rollout requires separate versioned runtime roles;
it must not be simulated by accepting privilege supersets.

## 3. Install The Runtime Object ACL

The PostgreSQL platform owner creates a dedicated login and its password
through the platform secret workflow. The role must be `LOGIN NOINHERIT
NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`, must own no
application object, and must participate in no role membership in either
direction. CI Coordinator does not create or alter that identity.

After the migration succeeds, install the exact application-object allowlist
with the same image digest:

```sh
export CI_COORDINATOR_RUNTIME_DATABASE_ROLE='ci_coordinator_runtime'
docker run --rm \
  --env CI_COORDINATOR_MIGRATION_DATABASE_DSN \
  --entrypoint ci-coordinator-database-access \
  "${CI_COORDINATOR_IMAGE}" apply \
  --runtime-role "${CI_COORDINATOR_RUNTIME_DATABASE_ROLE}"
```

The command acquires the exclusive compatibility fence and then proves that no
other database session is authenticated as the target runtime role before its
first ACL mutation. It revokes stale direct grants, installs the current table
and column allowlist, verifies the effective ACL, and commits atomically. It
rejects a live predecessor, unsafe role attributes, membership, ownership, or
platform-owned default ACLs. Keep predecessor restart disabled until the new
runtime is started with the new artifact. Re-run the non-mutating check after
any database or deployment-policy change:

```sh
docker run --rm \
  --env CI_COORDINATOR_MIGRATION_DATABASE_DSN \
  --entrypoint ci-coordinator-database-access \
  "${CI_COORDINATOR_IMAGE}" check \
  --runtime-role "${CI_COORDINATOR_RUNTIME_DATABASE_ROLE}"
unset CI_COORDINATOR_MIGRATION_DATABASE_DSN
```

Unset the migration DSN before starting the service. The runtime container
must never receive it.

## 4. Start Non-Enforcing Runtime

Inject the variables listed in [`.env.example`](../../.env.example) from the
deployment secret and configuration owners. The resolved
`CI_COORDINATOR_DATABASE_DSN` must identify the restricted runtime principal,
not the migration principal.

If the deployment requires a corporate egress proxy, set the one canonical
credential-free `CI_COORDINATOR_OUTBOUND_PROXY_URL` shown in `.env.example`.
Do not rely on ambient `HTTP_PROXY`, `HTTPS_PROXY`, or `NO_PROXY`: the runtime
intentionally ignores them. The proxy must permit TLS tunnelling to GitHub API,
GitHub reviewer OAuth, deployment-owned Keycloak, and the GitHub Actions token issuer.
Readiness remains false when a required JWKS retrieval cannot traverse that
route.

The image build and the running service are separate network-authority phases.
On a builder without direct egress, supply Docker's predefined `HTTP_PROXY` and
`HTTPS_PROXY` build arguments through the deployment-owned builder policy. The
operator-UI build stage enables Node's environment-proxy transport so Corepack
and pnpm can use those provider-supplied arguments; the repository does not
admit their syntax, secrecy, or provenance. The final Python image does not
inherit that switch or either proxy variable. Never declare proxy values with a
Dockerfile `ARG` or `ENV`, because that would retain deployment topology in
image metadata or layers.

### Mount Secret Files

Docker [`--env-file`](https://docs.docker.com/reference/cli/docker/container/run/#set-environment-variables--e---env---env-file)
uses one variable per line. Do not place multiline PEM values there or replace
their newlines with literal `\n`: the runtime does not unescape them. Put only
non-secret settings and container file paths in the env file, for example:

```dotenv
CI_COORDINATOR_RUNTIME_MODE=non_enforcing
CI_COORDINATOR_DATABASE_DSN_FILE=/run/secrets/database-dsn
CI_COORDINATOR_WEBHOOK_SECRET_FILE=/run/secrets/webhook-secret
CI_COORDINATOR_GITHUB_PRIVATE_KEY_FILE=/run/secrets/github-private-key.pem
CI_COORDINATOR_PLAN_SIGNING_PRIVATE_KEY_FILE=/run/secrets/plan-signing-private-key.pem
CI_COORDINATOR_BREAK_GLASS_BEARER_TOKEN_FILE=/run/secrets/break-glass-bearer-token
CI_COORDINATOR_METRICS_BEARER_TOKEN_FILE=/run/secrets/metrics-bearer-token
```

This is the secret-wiring portion, not a complete connected configuration.
Add the required non-secret settings from `.env.example`. With Keycloak enabled,
also use `CI_COORDINATOR_KEYCLOAK_BROWSER_CLIENT_SECRET_FILE`,
`CI_COORDINATOR_CONTROL_PLANE_SESSION_KEY_FILE`, and
`CI_COORDINATOR_GITHUB_APP_CLIENT_SECRET_FILE` as described in
[provider onboarding](configure-provider-identity.md). The tenth admitted name,
`CI_COORDINATOR_PRODUCTION_ADMISSION_PUBLIC_KEY_PEM_FILE`, is enforcing-only.
There is no generic `_FILE` expansion for other variables, including the
separate Alembic migration DSN.

Provision `/deployment-owned/runtime-secrets` outside the checkout with only
this service's runtime files; never include the migration credential. Mount it
read-only at `/run/secrets` as below. The image runs as `10001:10001`; arrange
directory traversal and file read access for that identity, for example files
owned by that UID with mode `0400`. The
[environment owner](../../backend/src/ci_coordinator/runtime/environment.py)
requires an absolute normalized path to a readable regular file, no group or
other write bits, bounded UTF-8 content and no NUL. It removes at most one final
LF and retains embedded PEM newlines. Limits are 64 KiB for DSN/webhook/private
keys, 4 KiB for bearer/client secrets, 64 bytes for the session-key text, and
16 KiB for the admission public key; the resolved setting is validated again.

Choose exactly one direct or `_FILE` form for each setting, even if a direct
value would be empty. Direct environment injection remains supported, but file
mounts avoid putting PEM bytes in the container's configured environment.
File mounts do not protect secrets from a privileged host operator. Do not
print file contents, commit them, or bake them into the image. Rotation requires
restarting the service; these files are startup inputs, not a hot-reload API.

The example publishes only to host loopback for a host-local ingress proxy.
Do not use `--publish 3000:3000`: Docker's
[default publication](https://docs.docker.com/engine/network/port-publishing/)
exposes all host interfaces. If the ingress runs in a separate container, omit
host publication and use a deployment-owned isolated network accessible only
to the approved ingress and probes. Before exposing any public endpoint,
satisfy [public ingress admission](#public-ingress-admission) below; loopback
publication alone is not proof that every bypass path is closed.

```sh
docker run --detach \
  --init \
  --name ci-coordinator \
  --publish 127.0.0.1:3000:3000 \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges=true \
  --pids-limit 256 \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777 \
  --env-file /deployment-owned/ci-coordinator.env \
  --mount type=bind,src=/deployment-owned/runtime-secrets,dst=/run/secrets,readonly \
  "${CI_COORDINATOR_IMAGE}"
```

Keep `CI_COORDINATOR_RUNTIME_MODE=non_enforcing` for initial deployment. This
mode cannot construct selected-execution authority.

## 5. Probe And Admit The Instance

Have the deployment secret owner provision a private header file outside the
checkout containing exactly one `Authorization: Bearer <metrics-token>` line,
using the distinct metrics credential. The operator must own the file with
mode `0600` in a mode `0700` directory. Do not type the token into shell commands,
export it, print the file, or enable shell/curl tracing. Header-file access does
not isolate credentials from the same host account or privileged operators.

[`curl --disable`](https://curl.se/docs/manpage.html#-q) is the first argument to
prevent ambient curl configuration from enabling tracing, redirects or retries;
[`--header @file`](https://curl.se/docs/manpage.html#-H) keeps token bytes out of
argv. Use the deployment-owned file path, not the token-only runtime secret:

```sh
export METRICS_HEADER_FILE=/deployment-owned/metrics-authorization.header
curl --disable --fail-with-body http://127.0.0.1:3000/healthz
curl --disable --fail-with-body \
  --header "@${METRICS_HEADER_FILE}" \
  http://127.0.0.1:3000/metrics
curl --disable --fail-with-body http://127.0.0.1:3000/readyz
curl --disable --fail-with-body http://127.0.0.1:3000/workbench
```

Liveness proves only process responsiveness. Readiness becomes successful only
after database attestation, JWKS availability, and one complete reconciliation
round. The public readiness body intentionally does not identify the failed
dependency. Keep `/metrics` on a restricted observability network even though
connected runtime also requires its distinct bearer. Authenticate configuration
and control-plane commands separately; use
[Manage Repository Policy](manage-repository-policy.md) for epoch registration
and activation without process restart.

The container health probe intentionally also accepts a pre-ASGI `503` as a
responsive process, avoiding overload-driven restart amplification. Container
`healthy` therefore proves neither readiness nor available request capacity.
Retain separate readiness and edge admission/saturation observations; do not
change this liveness contract to treat every overload as a restart condition.

## 6. Promote To Receipt-Gated Enforcement

Perform this step only after the external conjunction in
[Production Admission](../architecture/cross-cutting/production-admission.md)
is satisfied for the same artifact, source commit, environment, rollout
profile, config epoch, workflow identities, target registry, and exact
repository scopes.

The production-admission owner signs the canonical envelope outside the
service. Store the receipt as a read-only regular file and inject its Ed25519
public key and exact binding inputs through the deployment configuration owner:

```text
CI_COORDINATOR_RUNTIME_MODE=enforcing
CI_COORDINATOR_PRODUCTION_ADMISSION_RECEIPT_PATH=/var/run/ci-coordinator/production-admission.json
CI_COORDINATOR_PRODUCTION_ADMISSION_KEY_ID=<receipt-key-id>
CI_COORDINATOR_PRODUCTION_ADMISSION_PUBLIC_KEY_PEM_FILE=/run/secrets/production-admission-public-key.pem
CI_COORDINATOR_DEPLOYED_ARTIFACT_DIGEST=sha256:<exact-oci-manifest-digest>
CI_COORDINATOR_ENVIRONMENT_ID=<canonical-environment-id>
CI_COORDINATOR_ENFORCEMENT_SCOPE_ALLOWLIST=<installation:repository,...>
```

The enforcement scope must be a subset of
`CI_COORDINATOR_CONTROL_PLANE_SCOPE_ALLOWLIST`. Provision the real multiline
public-key PEM at the file path above, without also configuring its direct
environment form. Mount the receipt read-only and restart the exact image
digest. `CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS` is required and has no default;
the `45`-second example below assumes it is configured to `30` as in
[`.env.example`](../../.env.example). Deployment owners must keep the container
grace period strictly above the configured application budget:

```sh
docker stop --time 45 ci-coordinator
docker rm ci-coordinator
docker run --detach \
  --init \
  --name ci-coordinator \
  --publish 127.0.0.1:3000:3000 \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges=true \
  --pids-limit 256 \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777 \
  --env-file /deployment-owned/ci-coordinator-enforcing.env \
  --mount type=bind,src=/deployment-owned/runtime-secrets,dst=/run/secrets,readonly \
  --mount type=bind,src=/deployment-owned/production-admission.json,dst=/var/run/ci-coordinator/production-admission.json,readonly \
  "${CI_COORDINATOR_IMAGE}"
```

Startup must fail if the build identity, artifact, signature, time, environment,
rollout profile, scope set, or durable authority registration differs. Receipt
replacement is not hot reload: renew or rotate it through a controlled restart.
Request-time expiry, epoch drift, or a safety latch prevents selected issuance
even if the process remains otherwise ready.

A successful restart and v2 authority registration do not activate a scope
generation. Complete [Stage And Activate Production Authority](stage-and-activate-production-authority.md)
under its separate qualification prerequisites: stage the exact evidence,
begin the FullCI-latched cutover, establish the required external drain and
activate the retained successor. If any premise is unavailable, stop at the
non-selected state; do not treat process readiness as omission authority.

## 7. Connect A Target Repository

Install the GitHub App only on the admitted repositories. Configure the exact
permissions below; `Metadata: read` is GitHub's automatic minimum, and every
additional permission is justified by an owned read or event.

| GitHub App permission            | Level | Owned use                                                   |
|----------------------------------|-------|-------------------------------------------------------------|
| Actions                          | read  | workflow catalog, runs, attempt jobs, and `workflow_run`    |
| Administration                   | read  | repository self-hosted runner inventory                     |
| Checks                           | read  | check-run provenance                                        |
| Contents                         | read  | contents, Git objects, comparisons, and `push`              |
| Merge queues                     | read  | `merge_group`                                               |
| Metadata                         | read  | repository identity and installation catalog                |
| Organization self-hosted runners | read  | repository-visible runner groups and exact group membership |
| Pull requests                    | read  | pull-request identity, file listing, and `pull_request`     |

Subscribe only to `push`, `pull_request`, `merge_group`, and `workflow_run`.
The runtime profile further admits only `opened`, `reopened`, `synchronize`,
and `ready_for_review` pull-request actions; `checks_requested` merge-group
actions; and `requested`, `in_progress`, and `completed` workflow-run actions.
GitHub configures event subscriptions at event granularity, so other actions
within those events are rejected by the runtime profile rather than treated as
planning evidence. Do not grant write permissions: the current runtime does not own a
provider-write capability.

The matrix follows GitHub's endpoint and webhook contracts for
[Actions](https://docs.github.com/en/rest/actions/workflow-runs),
[repository runners](https://docs.github.com/en/rest/actions/self-hosted-runners),
[organization runner groups](https://docs.github.com/en/rest/actions/self-hosted-runner-groups),
[checks](https://docs.github.com/en/rest/checks/runs),
[contents](https://docs.github.com/en/rest/repos/contents),
[pull requests](https://docs.github.com/en/rest/pulls/pulls), and
[webhook events](https://docs.github.com/en/webhooks/webhook-events-and-payloads).
The platform administrator must verify the live App configuration against this
matrix; repository code and local tests cannot prove provider-side settings.

### Public Ingress Admission

Public exposure requires deployment-owned admission **before ASGI**. The
current Uvicorn h11 path counts open connections against the shared limit of
128 and has no first-request header-read deadline; its keep-alive timeout is
not that deadline. Application authentication and body limits cannot protect
this earlier connection phase.

The deployment owner must configure and qualify all of the following:

- Finite first-byte, total header-completion and idle-connection deadlines;
  incremental header bytes must not extend the total deadline indefinitely.
  Complete header admission at the edge before forwarding to the backend.
  Incomplete client requests must not reserve backend connections.
- Finite global and per-client connection, request-concurrency and pending-work
  bounds, with overload rejection. Define client identity at the trusted edge,
  not from arbitrary caller-supplied forwarding headers. Bound the aggregate
  across edge replicas and upstream pools, including idle upstream connections,
  with headroom for backend admission and operational probes.
- No public bypass to the backend through a published port, container address,
  alternate interface or IPv6 route. Only the approved edge and authorized
  operational paths may reach it; verify effective network policy, not merely
  the intended TLS hostname or a saved proxy declaration.
- Separate edge rejection/timeout and availability evidence. Pre-ASGI rejections
  can occur without application request metrics, and healthy container status
  is not a substitute for these observations.

Choose explicit values against the owned capacity and provider latency budgets;
this guide supplies no qualified universal thresholds or proxy configuration.
Before production exposure, retain exact edge configuration/version and an
authorized external witness of blocked bypass, bounded empty/slow-header
connections, overload recovery and valid provider/browser traffic. These are
separate deployment receipts. This documentation does not close the runtime
TCP-flood finding or establish that an actual edge enforces these controls.

The edge proxy owns accepted public hostnames, TLS termination, source-wide
rate controls, and forwarding metadata. The application runtime does not use
`Host` or forwarded headers as authorization evidence and starts Uvicorn with
proxy-header trust disabled. Validate the public host before forwarding and do
not rely on application access logs for request accounting; structured route
observations intentionally omit OAuth query values.

Publish browser and machine traffic as separate ingress classes. The operator
UI uses its application-owned Keycloak BFF; an additional ingress perimeter
cannot substitute for its identity and role checks. The exact
GitHub webhook and Actions plan endpoints must instead be reachable from their
provider callers without an interactive redirect or source-IP policy that
excludes GitHub. They remain fail-closed behind their application-owned HMAC
and GitHub Actions OIDC admission respectively. The reverse proxy must preserve
the request body and the `X-GitHub-*`, `X-Hub-Signature-256`, and
`Authorization` headers exactly. Preserve browser `Cookie`, `Origin` and
`X-CSRF-Token`, the admitted `Content-Type`, and exact callback query values;
do not log credentials or OAuth query secrets. Keycloak back-channel logout
also requires a non-interactive provider path. A successful internal probe does not prove
public provider reachability; retain one signed webhook delivery and one
OIDC-authenticated plan request as separate external receipts.

Copy the
[bootstrap workflow](../../fixtures/target-repository/.github/workflows/ci-coordinator-bootstrap.yml)
through repository-owner review, configure its OIDC-bound coordinator URL and
public signing key, and preserve the repository-owned FullCI command. A
coordinator timeout or invalid plan must execute FullCI automatically.

Follow the [Target Repository Adoption Playbook](../target-repository-migration.md)
before treating any provider observation as rollout evidence.

## 8. Rotate Or Revoke The Break-Glass Bearer

Use the request and response examples in [emergency controls](emergency-controls.md)
for the admitted emergency request below. Do not use an enable command to test
the break-glass credential: that principal is forbidden from releasing the latch.

The break-glass bearer is a deployment-owned emergency credential, not a
durable session or administrator login. It has no in-band expiry or overlap
window and is restricted to `force_full_ci` and `disable_omission`. Rotate it
by replacing the deployment secret and performing a controlled restart of every
replica. Prove the transition with one admitted emergency request using the new
value and one `401` response using the old value.

For emergency revocation, first isolate emergency mutation routes at ingress,
replace the secret, restart every replica, prove that the old value is rejected,
and then restore ingress. A partial replica rollout leaves both values live and
is therefore not an admitted rotation. Provider tokens, Keycloak sessions, and
the break-glass bearer have independent lifecycles; rotating one does not revoke
the others.

## 9. Stop Or Roll Back

Set the container grace period strictly above
`CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS`. This setting is required, with no
default; the example below assumes it is configured to `30` seconds:

```sh
docker stop --time 45 ci-coordinator
```

For code rollback, start the previously admitted image digest only when the
current database capability profile proves that artifact compatible. Do not
downgrade retained production data or rewrite an applied migration. If
compatibility is not proved, keep the current artifact in non-enforcing mode
and perform a forward repair.

For immediate CI safety, apply a repository `disable_omission` latch or restart
the current compatible artifact with `CI_COORDINATOR_RUNTIME_MODE=non_enforcing`.
Neither action requires deleting retained authority evidence. Re-enabling a
latch requires an audited exact-target `enable_omission` command plus
still-valid production admission.

## Evidence To Retain

- image digest and build provenance;
- migration start/end, revision, and principal identity without secret values;
- runtime settings identity with secrets redacted;
- liveness/readiness receipts and shutdown duration;
- exact edge bounds, backend isolation and pre-ASGI admission receipts;
- GitHub App installation, OIDC, bootstrap, fallback, and stable-gate receipts;
- rollback exercise and database compatibility result.
