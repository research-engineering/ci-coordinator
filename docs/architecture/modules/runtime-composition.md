# Runtime Composition Module Specification

Status: module specification

Last updated: 2026-08-21

## 1. Owned Invariant

The runtime composition module creates an executable process only for behavior
whose complete dependency closure is concrete, lifecycle-owned, and fail-closed.
It never substitutes an in-memory adapter for a durable or provider port merely
to make a route start.

```text
Runnable(mode) iff
  SettingsAdmitted(mode)
  and EveryMountedRouteHasConcreteUseCase(mode)
  and EveryUseCaseDependencyHasConcreteAdapter(mode)
  and EveryConstructedResourceHasExactlyOneLifecycleOwner(mode)
  and InitialReconciliationSucceededWithinBound(mode)
  and EveryUnavailableDependencyPreventsItsRouteFromClaimingSuccess(mode)
```

`SettingsAdmitted` is necessary but not sufficient. A process configuration can
be syntactically complete while the corresponding database, authenticated
provider, durable idempotency, or policy-projection adapter is absent.

## 2. Modes

| Mode                                            | Mounted behavior                                                                 | Readiness                                               | Forbidden behavior                                                         |
|-------------------------------------------------|----------------------------------------------------------------------------------|---------------------------------------------------------|----------------------------------------------------------------------------|
| `disabled`                                      | `/healthz`, `/readyz`, `/metrics`, `/api/v1/health`, `/api/v1/ready` only        | false with `runtime_mode_disabled`                      | webhook intake, plan issuance, operator mutation, selected dispatch        |
| `non_enforcing` before adapter closure          | no ASGI process                                                                  | typed startup rejection with sorted adapter identifiers | partial route mounting, in-memory durability substitute, selected dispatch |
| `non_enforcing` after adapter closure           | the complete candidate route set                                                 | derived from concrete dependency facts                  | selected dispatch or omitted-check success                                 |
| `enforcing` without production admission        | no ASGI process                                                                  | typed startup rejection                                 | selected dispatch, partial authority, fallback suppression                 |
| `enforcing` after admission and adapter closure | complete route set with receipt-bound selected issuance only for admitted scopes | derived from concrete dependencies and receipt validity | unscoped omission, receipt-free selected plans, override bypass            |

The second row is a deliberate safety boundary. Mounting only a subset of
stateful routes would make the deployment appear available while silently
dropping a promised protocol surface; using ephemeral state would make retry
and restart behavior contradict its idempotency contract.

## 3. Ownership

| File                                   | Responsibility                                                                                                            |
|----------------------------------------|---------------------------------------------------------------------------------------------------------------------------|
| `runtime/environment.py`               | the sole source-level read of process environment values and immutable mapping snapshot                                   |
| `runtime/application.py`               | entrypoint-evidence validation, mode selection, redacted composition-failure projection                                   |
| `runtime/composition.py`               | production-admission preflight, durable authority registration, and the shared connected dependency graph                 |
| `runtime/control_plane_composition.py` | optional Keycloak human and workload identity, opaque-session persistence, role admission, and mutation integrity closure |
| `runtime/readiness.py`                 | single-flight inductive database, background-service, and JWKS dependency probes                                          |
| `runtime/reconciliation_service.py`    | periodic single-flight reconciliation ownership                                                                           |
| `runtime/resources.py`                 | startup, shutdown, drain, and external-resource close order                                                               |
| `runtime/__main__.py`                  | executable server invocation and redacted startup-rejection projection                                                    |
| `runtime/__init__.py`                  | intentionally small public composition surface                                                                            |

The runtime module may import public composition APIs from the owning contexts,
HTTP DTOs, observability projections, admitted settings, and server
infrastructure. It may not import private predicates, execute SQL, or perform
provider HTTP directly; planning, verification, identity, persistence, GitHub,
and authorization decisions remain in their owners.

## 4. Constructive Non-Enforcing Closure

Both admitted modes are now constructible. Before either mode is constructed,
the composition root parses the bundled caller inventory and its finite
entrypoint disposition. Missing, malformed, unknown, or unmatched evidence
causes one redacted startup rejection. This remains local package evidence and
does not transfer an external caller.

`disabled` constructs only observability routes. `non_enforcing` constructs one
typed graph containing PostgreSQL units of work, GitHub App transport, bounded
OIDC JWKS retrieval, request-bound authentication, signed fallback issuance,
hot config registration and activation, monotonic operator controls, capacity
shaping, reconciliation, shadow evidence, provider inventory, exact-commit
workflow discovery, readiness, and lifecycle ownership.
The graph constructs exactly one shared workflow snapshot reader for direct
discovery and proposal review. Its capability-owned no-queue admission bounds
provider work across both entry points; route-local admission is only earlier
load shedding and cannot replace this composition invariant.
When the complete control-plane identity block is admitted, the same graph also
constructs Keycloak human and workload identity, opaque PostgreSQL sessions,
role admission, one-use GitHub reviewer attestation, and proposal activation.
No Keycloak or GitHub bearer token is persisted in a session. When the block is
absent, identity, repository-attestation, and configuration-mutation routes are
not mounted; break-glass remains limited to its emergency safety operations and
Actions OIDC remains independent.
Connected composition passes the one admitted optional outbound proxy URL to
the shared GitHub App transport, GitHub Actions JWKS provider, Keycloak clients,
and GitHub reviewer OAuth adapter. Every client retains `trust_env=False`.
Absence preserves direct routing; presence cannot create a mixed direct/proxied
graph for these owned clients. At client construction, an injected test
transport and an explicit proxy are mutually exclusive: `not (T and P)`.
This is at most one, not exclusive-or; `T = false, P = false` is the
ordinary direct-client case.
The graph sets `enforcement_enabled=False`; it has no selected-dispatch or
successful omitted-check publisher capability.

`enforcing` is a separate composition path. Before allocating provider clients
or the serving engine it reads one bounded final-component-no-symlink canonical receipt,
verifies its Ed25519 signature and temporal bounds, binds it to the packaged
production build identity and exact deployment and scope inputs, and derives a
production-admission authority. One bootstrap coroutine creates, uses, and
disposes a short-lived registration engine in one event loop while registering
the exact verified authority and scope bindings transactionally. Only after
that engine is disposed does composition allocate the fresh serving engine and
GitHub or JWKS clients. Any expiry, conflict, or registration failure rejects
startup before those runtime resources exist. The shared connected dependency
graph receives the opaque authority value; it never
receives an ambient boolean. A request outside its scope or under a subject
force-FullCI or repository omission-disable control has no selected-execution
authority.
Composition is synchronous and must run before an event loop starts. That
precondition is checked before the first external resource is allocated. If
construction fails after any subset of engine or HTTP owners exists, the
composition root closes that subset in reverse construction order before it
returns a redacted rejection; the original failure remains primary.
The ASGI lifespan yields only after one reconciliation round succeeds within
the admitted startup timeout. First-round failure, scheduler failure, stop, or
timeout raises a redacted startup error and drains before any route is served.
A later failed round makes readiness false until a successful recovery round.
Readiness samples background health after its asynchronous dependency probes,
which defines one final-observation linearization point. Periodic cadence begins
after terminal completion, not after a round is merely started.

The admitted shutdown timeout bounds the complete owner-controlled sequence:
background drain, GitHub transport close, JWKS close, and engine disposal. A
deadline breach is terminal and redacted; the deployment may then terminate the
process. The reconciliation drain receives exactly half of that total bound, so
its retryable timeout is strictly earlier than the outer owner deadline and the
remaining half is reserved for external-resource closure. A deadline with an
unsettled cleanup child enters `cleanup_pending`; the runtime remains not ready,
cannot restart or retry cleanup, and advances to `deadline_exceeded` only when
the child settles. A deadline fence prevents that child from initiating any
later close operation, although one already active third-party close can finish
after the deadline. Cleanup exception objects and causes never cross the
lifespan logging boundary. This is a cooperative asynchronous bound and does not
claim control over code that blocks the event loop or suppresses cancellation
indefinitely.

```text
ConstructedNonEnforcingRuntime
and EnforcementEnabled = false
and NoSelectedDispatchCapability
and NoOmittedSuccessPublisher
=> RunnableShadowCandidate
and not AuthoritativeDynamicCI
```

```text
ConstructedEnforcingRuntime
and ValidProductionAdmissionReceipt
and RepositoryScopeAdmitted
and not ForceFullCI(RepositoryScope)
=> SelectedExecutionMayBeIssued

otherwise => FullCI
```

Constructive evidence is the typed graph plus native unit, PostgreSQL restart,
HTTP/OIDC, readiness, lifecycle, and full-composition witnesses. The
full-composition witness starts the ASGI lifespan against real PostgreSQL,
proves readiness, authenticates a request, obtains an exact durable signed
FullCI replay, and registers and activates a config epoch without a process
restart. Its enforcing path additionally consumes a real signed receipt,
registers the exact authority and scope rows before serving, and proves that a
tampered receipt cannot allocate the database or provider clients.

## 5. Container Artifact Contract

The supported deployment artifact is the digest-addressed OCI image built by
the root `Dockerfile`. A source checkout, editable development environment, or
standalone wheel is not a deployment artifact.

```text
DeployableImage(i) :=
  ImmutableDigest(i)
  and ContainsLockedRuntime(i)
  and ContainsAlembicConfigAndRevisionChain(i)
  and RunsAsNonRoot(i)
  and StartsWithoutSourceMount(i)
  and ExposesBoundedLiveness(i)
```

The image contains `/app/backend/alembic.ini`, the Alembic environment, and the
complete revision chain. Migration is a separate deployment action using
`CI_COORDINATOR_MIGRATION_DATABASE_DSN`; the default placeholder fails closed.
The migration principal owns DDL and declaration changes. The service uses
`CI_COORDINATOR_DATABASE_DSN` for a separately attested runtime principal that
cannot perform DDL.

The container witness builds without a source mount, starts disabled mode,
proves CPython and non-root identity, executes liveness, admits the embedded
operator UI bundle without publishing it in disabled mode, admits the migration
CLI, and verifies the exact packaged revision inventory. It does not migrate a
production database or prove a registry digest, signature, platform rollout,
or backup policy.

## 6. Failure Algebra

```text
RuntimeSettingsRejection(field) -> process exits 2 with code and field identity
RuntimeDependencyConfigurationError -> redacted runtime_dependencies_unavailable
InvalidEntrypointEvidence -> process exits 2 with a resource category only
DisabledRuntime -> liveness 200; readiness 503; no mutable or planning route
InitialReconciliationFailureOrTimeout -> ASGI startup rejected; no route served
NonEnforcingDependencyFailure -> readiness false or typed unavailable/FullCI-safe response
InvalidProductionAdmission -> process exits 2 with production_admission_unavailable
```

No failure projection contains a settings value, DSN, private key, webhook
secret, bearer token, raw request body, or stack trace.

## 7. Required Falsifiers

- a module other than `runtime/environment.py` reads `os.environ`;
- a malformed bundled caller inventory reaches ASGI construction;
- a malformed, unknown, or caller-unmatched entrypoint disposition reaches ASGI construction;
- disabled mode mounts a plan, webhook, override, workbench, or asset route;
- non-enforcing mode starts with an invalid key or missing required process fact;
- a partial or disabled control-plane identity block mounts an identity or
  repository-mutation route;
- control-plane identity is enabled while a session repository, Keycloak
  adapter, reviewer-attestation dependency, or role admission is absent or
  ephemeral;
- non-enforcing mode serves before its first successful reconciliation round;
- shutdown wins before first-round publication but startup is still admitted;
- concurrent close changes startup state but lifespan later reactivates it;
- a failed periodic round leaves readiness true before a recovery succeeds;
- readiness returns success from a background-health sample taken before a
  blocking dependency probe that observes a later failed round;
- a composition rejection leaks a secret-bearing setting value;
- a failure after engine, GitHub, or JWKS construction leaves any previously
  constructed resource open;
- connected composition supplies different proxy identities to GitHub API,
  GitHub reviewer OAuth, Keycloak, and Actions JWKS clients, or ambient proxy
  variables affect any of those clients;
- composition starts inside an active event loop and allocates a resource;
- a constructed external resource has no shutdown owner;
- the complete shutdown sequence can exceed its configured owner deadline, or
  its rendered failure traceback contains a provider diagnostic;
- reconciliation drain receives the complete outer shutdown budget;
- unsettled cancellation is hidden or a new close operation starts after the
  cleanup deadline fence;
- a timed-out reconciliation drain makes a later safe close impossible;
- one failed external close causes already closed resources to be closed twice
  or makes the unfinished resource permanently unreachable;
- a composed route dispatches selected work or marks an omission successful;
- an enforcing runtime starts from a mode string or credential presence without
  a valid same-binding production-admission receipt;
- a valid receipt reaches provider-client allocation without exact durable
  authority and scope registration;
- a tampered receipt allocates a database engine, GitHub client, or JWKS client;
- a selected plan omits the exact production-admission receipt identity;
- an out-of-scope repository or durable force-FullCI override receives selected execution;
- shadow evidence is recorded under an epoch id instead of the precommitted rollout-profile id.
- the image starts only when repository source is mounted;
- the default Alembic placeholder can reach a database;
- the service runtime principal can perform migration DDL;
- the image omits any admitted Alembic revision.

## 8. Non-Claims

This module owns local composition and authenticated receipt consumption. It does not
authenticate live deployment credentials, prove provider availability or SLOs,
publish a successful coordinator check, migrate an external bootstrap caller,
satisfy the precommitted rollout profile, or make receipt evidence true. Those
predicates remain external deployment evidence asserted by the configured
admission authority.
