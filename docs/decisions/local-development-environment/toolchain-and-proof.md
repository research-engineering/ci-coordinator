# Local Toolchain And Proof Contract

Status: current decision companion

The [local-development decision](../local-development-environment.md) owns
the environment choice, identity, isolation and secret boundaries. This
companion owns the detailed container/tool-cache and proof-execution
projection; changes follow those lifecycle owners. For an interactive
starting procedure use [Evaluate Locally](../../how-to/evaluate-locally.md).

## 9. Toolchain And Containers

The [decision entrypoint](../local-development-environment.md#9-toolchain-and-containers)
retains the historical runtime-version literals and routes to the current
runtime owner. Package locks own the installed dependency graph.


Container image tags are paired with immutable digests. The Dev Container
installs the exact tool versions and does not maintain a second application
topology. It has no host Docker socket by default because possession of that
socket grants authority over unrelated host containers and files. Connected
stack lifecycle commands therefore run on the trusted host; a future explicit
privileged profile may expose the socket only with separate admission evidence.
The project-built backend and frontend images perform dependency installation
as a build concern. Root authority exists only for the secret-copy transition;
application code executes as an exact unprivileged UID with no capabilities or
privilege escalation. Mutable source-watch targets are owned by those runtime
users. A provider witness performs add, update, and delete transitions for both
targets and verifies the backend restart rather than treating declared YAML as
runtime evidence.

The source tree remains bind-mounted, but mise tool data, `backend/.venv`, the
actual pnpm store, root `node_modules`, and frontend `node_modules` are mounted
from named volumes keyed by the Dev Container identity. The pnpm volume is
mounted at the store path observed for this multi-volume workspace, so cache
bytes never enter the host bind mount. Linux binaries and symlinks
therefore cannot replace the host's platform-specific locked installation,
repository inventory cannot absorb a package cache, and concurrent worktrees
do not share dependency state. These volumes are tool caches, not application
data; orphan discovery and pruning remain part of the separately specified
future cleanup surface.

After provisioning, the Dev Container witness admits exactly one workspace
bind from the admitted host path and the five expected writable named-volume
destinations with one shared identity derived from the exact ownership label by
the pinned CLI algorithm and lexically canonical Docker volume sources. Exact
volume inspection must additionally report the local driver and scope, no
driver options, and the same mountpoint observed on the container. It rejects
any additional mount or host Docker socket source, resolves the pnpm store
canonically inside its verified volume, disconnects every container network,
and only then executes the portable quality projection against the same full
container ID. Cleanup must rediscover exactly that ID before removal.
The interactive Dev Container retains normal network access; physical network
isolation is a property of the CI witness, not a false claim about every local
invocation.

The pinned Dev Container CLI may leave an attached host-side Docker client alive
when the `up` parent exits. Only this exact internal `up` route requests bounded
termination of its complete residual process group; all other commands retain
the generic reject policy. No mutable process metadata is used as authority.
After group quiescence and capture-pipe closure, the witness independently
requires exactly one owned container, verifies its mount and package-store
boundaries, disconnects every network, and executes the portable proof through
a new command. Cleanup therefore cannot create a false success when it damages
a required observable: the subsequent provider postcondition fails.

The supported host contract is x86-64 or ARM64 Linux, Apple Silicon macOS, plus
those Linux architectures under WSL2 with Docker integration. Native Windows
is outside the contract because the bounded process and private-state locks
require POSIX process groups and file locking. Intel macOS is outside the
locked contract because pnpm 11.25.0 publishes a checksum-bearing standalone
macOS artifact only for ARM64; silently changing the installer backend on one
platform would weaken reproducibility. Docker Compose 2.22.0 is the minimum
admitted plugin version because it is the first release supporting the
declared Compose Watch surface. The lifecycle owner checks this version before
starting a project.

## 10. Developer Commands

The task surface is intentionally small:

| Task                       | Postcondition                                                         |
|----------------------------|-----------------------------------------------------------------------|
| `mise run install`         | exact locked backend and frontend dependencies exist                  |
| `mise run browser:install` | admitted Chromium provider exists for browser witnesses               |
| `mise run dev:up`          | isolated connected stack is healthy or fails explicitly               |
| `mise run dev:status`      | owned services and actual endpoints are reported                      |
| `mise run dev:logs`        | bounded project logs are followed                                     |
| `mise run dev:watch`       | healthy endpoints are printed and admitted source changes are applied |
| `mise run dev:down`        | owned ephemeral resources stop; data survives                         |
| `mise run dev:reset`       | owned stack and persistent data are removed                           |
| `mise run check:portable`  | provisioned witnesses pass without Docker or network access           |
| `mise run check`           | deterministic repository quality gates pass                           |

Direct Python and Compose commands remain documented as a fallback when mise
is unavailable; they call the same lifecycle owner rather than duplicate its
logic.

Installation and proof are separate transitions:

```text
Bootstrap := mise run install
PortableProof := DependenciesAlreadyLockedAndInstalled and mise run check:portable

PortableProof does not perform Bootstrap
```

This makes an offline proof claim falsifiable instead of allowing an implicit
network operation to satisfy its own prerequisite.

Portable proof is a semantic projection, not a literal command-id subset of
the connected plan. It replaces provider-backed full coverage with the exact
`not persistence` Python test command while preserving static, contract,
type, package, import-boundary, frontend, and Proofkit witnesses:

```text
PortableProof = SharedStaticProof and NonPersistenceTests
ConnectedProof = SharedStaticProof and FullCoverage and PersistenceProof

PortableProof requires no Docker or PostgreSQL provider
ConnectedProof retains the repository-wide coverage threshold
```

Classifying full coverage as provider-free is invalid because its collected
test set includes PostgreSQL tests backed by Testcontainers even when a host
happens to have a reachable Docker daemon.


## Instance-Specific Proof

The branch-head connected-stack witness materializes the exact committed source
in an owned detached worktree and derives a separate instance from that root.
It therefore neither reuses nor deletes an interactive stack, even when proof
and development run concurrently from the same primary worktree. Witness
cleanup removes both its Compose resources and detached source root.
