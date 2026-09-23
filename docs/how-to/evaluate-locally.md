# Run CI Coordinator Locally

Status: as-built how-to

Updated: 2026-09-08

## Outcome

This procedure installs the locked toolchain, starts one isolated connected
instance, opens the operator workbench, and verifies the local dataflow.

```text
LocalEvaluation := LockedTools
                   and LockedDependencies
                   and ConnectedStack
                   and LocalWitnesses

LocalEvaluation does not imply ProductionAdmission or ProviderReadiness
```

## Start The Connected Stack

Supported hosts are x86-64 or ARM64 Linux, Apple Silicon macOS, or those Linux
architectures under WSL2 with Docker integration. Native Windows and Intel
macOS are not supported. Prerequisites are Docker Engine 29.6.1 or compatible,
Docker Compose 2.39.4 or newer, and mise 2026.9.0 or newer. Run from the
repository root:

```sh
mise trust
mise run install:backend
mise run dev:up
```

When the host requires the same explicit corporate egress route as a remote
deployment, supply the CI Coordinator setting on the first `prepare`, `up`, or
`watch` operation:

```sh
CI_COORDINATOR_OUTBOUND_PROXY_URL=http://proxy.example.test:3128 mise run dev:up
```

The lifecycle validates and canonicalizes this value, then persists it in the
private per-worktree instance state. Later lifecycle commands reuse that exact
value without relying on `HTTP_PROXY`, `HTTPS_PROXY`, or `NO_PROXY`. A different
explicit value fails closed; run `mise run dev:reset` before deliberately
changing the route.

`dev:up` prepares the frozen backend Python graph, provisions PostgreSQL 18.6
with separate migration and runtime principals, applies Alembic and exact ACLs,
then starts the backend and browser UI. It prints JSON containing the actual
loopback endpoints selected atomically by Docker. The frontend dependency graph is
installed inside its image; host Node/pnpm installation is only needed for frontend
work and full verification. See [developer workflows](development-workflows.md) for
diagnostics, synthetic demos, debugging, cancellation and adoption guidance.

Open the reported `ui` endpoint at `/workbench` to verify static asset delivery.
The frontend container receives no credential and the development proxy adds no
`Authorization` header. Without an explicitly configured Keycloak identity
block, authenticated catalog and workbench routes return `401`; this is a
truthful provider-free state, not a simulated administrator session.

Fresh generated development state selects App-wide inventory but does not
create an external Keycloak identity or a real App credential. Authentication
still stops catalog reads before provider I/O. Existing state retains its
previous explicit or default inventory policy. End-to-end authenticated UI use
requires the separate Keycloak and GitHub App deployment procedure; `dev:up`
does not silently create or impersonate those external authorities.
See [installation discovery](discover-installed-organizations.md) for explicit
connected configuration.

## Verify And Operate The Instance

```sh
mise run dev:smoke
mise run dev:status
mise run dev:logs
```

`dev:smoke` verifies backend health, the SPA document, fail-closed anonymous
responses from protected workbench routes, proxy rejection of ambiguous query
forms, and absence of secret values from the rendered Compose model.

For an active edit/reload loop, run this instead of `dev:up`:

```sh
mise run dev:watch
```

It first reaches the same healthy postcondition and prints the same dynamic
endpoints, then applies the declared backend restart and frontend sync rules.
Stopping the watcher leaves the isolated stack and its data running.

Stop containers while retaining the PostgreSQL volume:

```sh
mise run dev:down
```

Remove only the current worktree's containers, network, volume, and local
credentials:

```sh
mise run dev:reset
```

Run `dev:reset` before deleting a worktree. Automatic discovery or deletion of
state whose canonical worktree root no longer exists is intentionally not part
of this lifecycle surface.

## Run Concurrent Worktrees

Run the same commands from each worktree. No port or project-name override is
required.

```text
Project(R) := "ci-coordinator-" + prefix12(SHA256(canonical(R)))

R1 != R2
=> Project(R1) != Project(R2), except a detected hash-prefix collision
```

Containers, networks, volumes, metadata, credentials, and Docker-assigned
loopback ports are instance-owned. Full root digests and canonical roots are
checked before lifecycle mutation, so a short-id collision fails closed.

State is outside the worktree under the first available location:

1. `$CI_COORDINATOR_DEV_STATE_HOME`;
2. `$XDG_STATE_HOME/ci-coordinator`;
3. `~/.local/state/ci-coordinator`.

An explicit state home must be absolute and outside every registered worktree;
canonical symlink aliases into a worktree are rejected. Do not share it across
host accounts.

## Use The Dev Container

The default Dev Container installs the locked mise toolchain and dependencies
without mounting the host Docker socket. This is deliberate: Docker daemon
access is root-equivalent authority over unrelated host resources.

Platform-specific tool and dependency installations use Dev Container identity
volumes mounted over mise data, `backend/.venv`, pnpm's observed Linux store
path, and both `node_modules` directories. The store volume is mounted at the
path pnpm selects for this multi-volume workspace. The container can therefore provision
Linux artifacts without replacing the host installation, exposing cache bytes
to repository proof, or sharing mutable dependency state with another worktree.

Inside the container, run the provider-free proof projection:

```sh
mise run check:portable
```

`check:portable` is an offline proof over an already provisioned locked
dependency graph. It never installs dependencies. The Dev Container
`postCreateCommand` performs that bootstrap before the task is available.
The CI Dev Container witness removes every container network and proves the
absence of a Docker socket before invoking this task. A direct host invocation
does not claim an operating-system network sandbox.

Run `dev:up`, `dev:smoke`, and provider-backed full proof on the trusted host.
The repository does not maintain a second application topology for the editor
container.

## Run Full Repository Proof

```sh
mise run install
mise run check
mise run browser:install
backend/.venv/bin/python -m scripts.dependency_audit
backend/.venv/bin/python -m scripts.container_runtime_smoke
CI=true PROOFKIT_BASE_REF="$BASE_SHA" PROOFKIT_HEAD_REF="$HEAD_SHA" \
  mise exec -- backend/.venv/bin/python -m scripts.proofkit_branch_head_quality
```

`browser:install` explicitly provisions the admitted Chromium provider before
the branch-head browser witness. The full plan requires Docker and external
registries. `dependency_audit` is a separate network-dependent advisory
witness and must be reported unavailable, not silently omitted, when its
provider cannot be reached.

Set `BASE_SHA` to the exact reviewed-base commit and `HEAD_SHA` to the exact
current commit. `CI=true` is an explicit acknowledgement that this gate may
create and remove isolated Docker resources; it is not a readiness claim.

## Direct Command Fallback

When mise cannot be installed, invoke the same owners directly; do not create a
second task facade:

```sh
uv sync --project backend --frozen --all-groups
pnpm install --frozen-lockfile
backend/.venv/bin/python -m scripts.dev_environment up
backend/.venv/bin/python -m scripts.dev_environment smoke
```

The production evidence boundary remains owned by
[Production Admission](../architecture/cross-cutting/production-admission.md).
