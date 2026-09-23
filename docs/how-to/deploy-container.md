# Deploy The Container Artifact

Status: supported deployment procedure

Last verified: 2026-07-20

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

## 1. Freeze The Artifact

Dispatch the repository-owned `Release Artifact` workflow on exact `master`
after its push-triggered `Full Check` succeeds. The workflow builds the root
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
deployment secret and configuration owners. `CI_COORDINATOR_DATABASE_DSN` must
identify the restricted runtime principal, not the migration principal.

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

```sh
docker run --detach \
  --init \
  --name ci-coordinator \
  --publish 3000:3000 \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges=true \
  --pids-limit 256 \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777 \
  --env-file /deployment-owned/ci-coordinator.env \
  "${CI_COORDINATOR_IMAGE}"
```

Keep `CI_COORDINATOR_RUNTIME_MODE=non_enforcing` for initial deployment. This
mode cannot construct selected-execution authority.

## 5. Probe And Admit The Instance

```sh
curl --fail-with-body http://127.0.0.1:3000/healthz
metrics_token="$(cat /deployment-owned/metrics-bearer-token)"
curl --fail-with-body \
  --header "Authorization: Bearer ${metrics_token}" \
  http://127.0.0.1:3000/metrics
unset metrics_token
curl --fail-with-body http://127.0.0.1:3000/readyz
curl --fail-with-body http://127.0.0.1:3000/workbench
```

Liveness proves only process responsiveness. Readiness becomes successful only
after database attestation, JWKS availability, and one complete reconciliation
round. The public readiness body intentionally does not identify the failed
dependency. Keep `/metrics` on a restricted observability network even though
connected runtime also requires its distinct bearer. Authenticate configuration
and control-plane commands separately; use
[Manage Repository Policy](manage-repository-policy.md) for epoch registration
and activation without process restart.

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
CI_COORDINATOR_PRODUCTION_ADMISSION_PUBLIC_KEY_PEM=<receipt-ed25519-public-key-pem>
CI_COORDINATOR_DEPLOYED_ARTIFACT_DIGEST=sha256:<exact-oci-manifest-digest>
CI_COORDINATOR_ENVIRONMENT_ID=<canonical-environment-id>
CI_COORDINATOR_ENFORCEMENT_SCOPE_ALLOWLIST=<installation:repository,...>
```

The enforcement scope must be a subset of
`CI_COORDINATOR_CONTROL_PLANE_SCOPE_ALLOWLIST`. Mount the receipt read-only and
restart the exact image digest. The `45`-second example below assumes the
documented `30`-second application shutdown budget; deployment owners must keep
the container grace period strictly above the configured application budget:

```sh
docker stop --time 45 ci-coordinator
docker rm ci-coordinator
docker run --detach \
  --init \
  --name ci-coordinator \
  --publish 3000:3000 \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges=true \
  --pids-limit 256 \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777 \
  --env-file /deployment-owned/ci-coordinator-enforcing.env \
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
planning evidence. Do not grant write permissions: this release does not own a
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
`Authorization` headers exactly. A successful internal probe does not prove
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
`CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS`; the example below assumes the
documented default of `30` seconds:

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
- GitHub App installation, OIDC, bootstrap, fallback, and stable-gate receipts;
- rollback exercise and database compatibility result.
