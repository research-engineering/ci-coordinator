# Runtime Settings Module Specification

Status: module specification

Date: 2026-07-17

## 1. Owned Invariant

`runtime_settings` converts one explicit process-input mapping into one immutable,
redacted settings value or one typed admission failure. It owns neither domain
configuration nor dependency construction.

```text
AdmittedRuntimeSettings(input) iff
  EveryRequiredProcessFactIsPresent(input)
  and EveryScalarIsBoundedAndWellTyped(input)
  and SigningKeyPairIsCompleteOrAbsent(input)
  and BrowserIdentityBlockIsCompleteOrAbsent(input)
  and RuntimeModeIsAdmitted(input)
  and NoSecretValueIsProjectedOutsideComposition(input)
```

The module reads no environment itself. Only the composition root supplies a
mapping, so pure domains remain independent of ambient process state.

## 2. Configuration Classes

| Class             | Owner               | Mutability                          | Application rule                                                                   |
|-------------------|---------------------|-------------------------------------|------------------------------------------------------------------------------------|
| Process wiring    | `runtime_settings`  | immutable for one process           | change requires a new process                                                      |
| Secret references | deployment owner    | immutable for one process           | resolve only at composition boundary; never log or return                          |
| Repository policy | `config_epochs`     | revisioned at runtime               | create and activate a new immutable epoch; each use case reads the active revision |
| Operator override | `operator_controls` | command-specific durable transition | read its current subject expiry or repository latch for each affected decision     |

Therefore hot configuration is not an in-memory mutable settings reload:

```text
HotPolicyChange => NewEpoch + AuditedCASActivation
HotSafetyControl => GovernedDurableOverride
ProcessWiringChange => Restart
```

This is necessary because permitting a mutable in-memory policy would make a
request's proof, audit record, and replay identity depend on an unversioned
ambient value. A config epoch instead supplies one stable identity to each
request while allowing a later request to observe a newly activated revision.

## 3. Public API

```text
admit_runtime_settings(mapping)
  -> DisabledRuntimeSettings
   | NonEnforcingRuntimeSettings
   | EnforcingRuntimeSettings
   | RuntimeSettingsRejection
redacted_settings_projection(settings) -> RuntimeSettingsProjection
ControlPlaneIdentitySettings -> exact Keycloak, callback, cookie, workload, and reviewer bounds
parse_build_identity(bytes) -> BuildIdentity
load_bundled_build_identity() -> BuildIdentity
admit_caller_inventory(document) -> CallerInventory | CallerInventoryRejection
parse_caller_inventory(bytes) -> CallerInventory
load_bundled_caller_inventory() -> CallerInventory
admit_entrypoint_disposition(document, callers)
  -> EntrypointDispositionInventory | EntrypointDispositionRejection
parse_entrypoint_disposition(bytes, callers) -> EntrypointDispositionInventory
load_bundled_entrypoint_disposition(callers) -> EntrypointDispositionInventory
admit_python_runtime(version_info?, implementation?)
  -> None | UnsupportedPythonRuntime
parse_python_runtime_profile(bytes) -> PythonRuntimeProfile
load_bundled_python_runtime_profile() -> PythonRuntimeProfile
```

`RuntimeSettings` is the closed union `DisabledRuntimeSettings |
NonEnforcingRuntimeSettings | EnforcingRuntimeSettings`. The mode is therefore
a type discriminator rather than a nullable field bundle. All variants contain:

```text
bind_host: canonical lowercase DNS name or canonical IPv4/IPv6 address
bind_port: integer in [1, 65535]
shutdown_timeout_seconds: positive bounded duration
```

`DisabledRuntimeSettings` has no dependency, identity, policy, or secret-bearing
field. `NonEnforcingRuntimeSettings` additionally requires every field below at
construction time:

```text
database_dsn: secret-bearing value, never serializable as a public projection
webhook_secret: secret-bearing value, never serializable as a public projection
github_app_id: non-empty text
github_private_key: secret-bearing value, never serializable as a public projection
plan_signing_key_id: non-empty text
plan_signing_private_key: secret-bearing value paired with key id
oidc_audience: non-empty bounded text
oidc_allowed_workflow_refs: unique bounded set for workflow_ref
oidc_allowed_job_workflow_refs: unique bounded set for job_workflow_ref
request_timeout_seconds: positive bounded duration
plan_ttl_seconds: integer in [1, 300], matching the target signed-plan lifetime bound
reconciliation_interval_seconds: positive bounded duration
reconciliation_startup_timeout_seconds: positive bounded duration
reconciliation_scan_limit: integer in [1, 1000]
shadow_rollout_profile_id: lowercase SHA-256 identity of the precommitted rollout profile
break_glass_actor_id: bounded emergency principal identity
break_glass_bearer_token: secret-bearing emergency transport credential
metrics_bearer_token: secret-bearing metrics-only transport credential
control_plane_scope_allowlist: non-empty bounded repository-scope set
control_plane_inventory_installation_allowlist: bounded installation-id set; empty is valid
control_plane_identity: absent or one complete ControlPlaneIdentitySettings value
outbound_proxy_url: absent or one canonical credential-free HTTP proxy URL
```

The installation-inventory allowlist is independent process wiring. It admits
at most 64 unique canonical-decimal positive safe integer ids and controls only provider catalog
browsing. It does not widen `control_plane_scope_allowlist`; an empty value performs
no provider inventory I/O while the exact repository workbench remains
available through its existing scope authority.

Control-plane identity is optional process wiring for connected modes and is
admitted only as one complete block:

```text
mode = keycloak
issuer: exact deployment-admitted canonical HTTPS OIDC issuer
browser_client_id = ci-coordinator-admin-ui
browser_client_secret: secret-bearing value limited to 4,096 UTF-8 bytes
api_client_id = ci-coordinator-admin-api
public_origin: canonical HTTPS origin or explicit loopback HTTP development origin
session_key: canonical unpadded base64url encoding of exactly 32 bytes
maximum_session_seconds: integer in [60, 900]
workload_client_ids: at most 128 canonical client identities
github_reviewer_client_id: exact admitted GitHub App client identity
github_reviewer_client_secret: secret-bearing value limited to 4,096 UTF-8 bytes
callback_uri = public_origin + /api/v1/auth/keycloak/callback
back_channel_logout_uri = public_origin + /api/v1/auth/keycloak/backchannel-logout
reviewer_callback_uri = public_origin + /api/v1/repository-attestations/github/callback
```

Absent mode plus any identity field is a missing-mode rejection. Explicit
`disabled` plus any other identity field is an invalid-value rejection. A
non-loopback HTTP origin, origin path/query/fragment/user-info, default-port
spelling, non-canonical key, non-exact issuer/client identity, unsupported
algorithm, or partial credential block fails admission. The public projection
exposes only mode, origin, duration, profile digest, counts, and secret-presence
booleans; it never exposes a secret.

Outbound proxy routing is optional connected-mode process wiring. Its wire form
is lowercase ASCII `http://host[:non-default-port]` with either no path or `/`.
Admission normalizes the optional slash away before construction. User
information, any other path, query, fragment, HTTPS proxy scheme,
non-canonical host or IP spelling, unspecified address, port zero, and explicit
default port are rejected. HTTPX2 2.12.0 does not correctly support HTTPS proxy
connections; HTTPS GitHub destinations retain end-to-end TLS over an HTTP
CONNECT tunnel. The setting is immutable for the process and applies to every
owned GitHub API, GitHub reviewer OAuth, Keycloak, and GitHub Actions JWKS client. Disabled mode
rejects the field instead of silently ignoring it. The public projection
reveals only whether the proxy is configured; it never exposes the network
location.

Ambient proxy variables are not settings authority. Every outbound client
keeps HTTPX environment trust disabled and receives the admitted value directly
from composition. A caller-injected test transport and a live proxy URL are
mutually exclusive so route precedence is total rather than library-defined.

`EnforcingRuntimeSettings` extends the complete connected settings value with:

```text
production_admission_receipt_path: bounded absolute path
production_admission_key_id: bounded signing authority identity
production_admission_public_key_pem: bounded redacted trust input
deployed_artifact_digest: lowercase SHA-256 artifact identity
environment_id: canonical bounded environment identity
enforcement_scope_allowlist: non-empty subset of control_plane_scope_allowlist
```

These fields are necessary inputs to production-admission verification; they
are not authority. Only `production_admission` can combine them with the
packaged build identity and a valid receipt to mint an opaque grant.

At least one OIDC workflow-identity set is non-empty. Exact ref identities use
`CI_COORDINATOR_OIDC_ALLOWED_WORKFLOW_REFS` and
`CI_COORDINATOR_OIDC_ALLOWED_JOB_WORKFLOW_REFS`. Repository workflow-path
identities use `CI_COORDINATOR_OIDC_ALLOWED_WORKFLOW_PATHS` and
`CI_COORDINATOR_OIDC_ALLOWED_JOB_WORKFLOW_PATHS`. Every variable is optional
when another identity set is present, parsed and bounded independently, and
projected only to its matching OIDC claim policy field. A path identity has the
exact `owner/repository/.github/workflows/file.yml` shape and requires an
immutable workflow SHA during claim admission. A member of one set never
becomes a member of another by union, fallback, or omission.

In connected runtime, `request_timeout_seconds` is the absolute elapsed-time
bound for every non-liveness HTTP request, including admission, optional body collection,
authentication, provider access, persistence, and response construction.
Body-bearing requests additionally acquire a path-local no-queue lane and a
process-wide weighted lease from `maximum_retained_body_bytes`; an absent
`Content-Length` reserves the route maximum. The ASGI server separately caps
concurrent request tasks. These bounds do not claim allocator peak memory,
replica-wide fairness, or ingress capacity.

Every secret-bearing scalar is limited to 65,536 UTF-8 bytes before wrapping.
The break-glass and metrics bearer tokens are each further limited to 4,096
ASCII bytes, which is the upper bound shared with their HTTP authentication.
The webhook secret, break-glass bearer, and metrics bearer must each contain at
least 32 bytes before connected runtime construction. Break-glass uses the
dot-free base64url alphabet, so no compact JWT can inhabit both the machine and
emergency credential languages. The authenticator compares only fixed-length
SHA-256 digests after admission. The metrics bearer uses its separate
header-safe URL-token alphabet and cannot reuse any configured credential
secret or authenticate a non-metrics operation.
These deterministic floors are work-factor proxies only: byte length does not
prove randomness, entropy, custody, rotation, or ingress rate limiting. Every
process string must encode as valid UTF-8. Workflow-reference
and control-plane-scope CSV inputs are bounded by raw member count and total UTF-8
bytes before splitting; duplicate members do not bypass those input bounds.
Every environment integer is ASCII decimal and is rejected outside its owning
bound. A rejection identifies the exact input field; it never mislabels a scalar
failure as a runtime-mode failure.

The process boundary snapshots the supplied mapping once. Every key in the
owned `CI_COORDINATOR_` namespace must belong to the selected mode; unknown and
mode-irrelevant owned keys are rejected instead of ignored. Foreign process
keys remain outside this contract. A bind host is admitted only as a canonical
lowercase DNS name or canonical IPv4/IPv6 address.

Each secret-bearing input accepts exactly one direct `NAME` or file-backed
`NAME_FILE` form. File resolution exists only in `runtime/environment.py`,
before pure settings admission. A mode-irrelevant file form is rejected before
the file is accessed. Paths are absolute and bounded; the reader
opens a non-blocking descriptor, admits only regular files that are not group-
or world-writable, removes at most one conventional terminal LF, bounds the
result before strict UTF-8 decoding, rejects empty or NUL-bearing text, and
rejects a file whose descriptor identity, size, mode, or modification time
changes during the read. Rejections expose the setting name, never the path or
content.

The signing key id and private key have all-or-none admission semantics.
`disabled` constructs only operability projections and can never contain a
selected-plan issuer's settings or secrets. `non_enforcing` may issue only
FullCI-safe responses. `enforcing` is merely a syntactically complete candidate
until the separate production-admission and durable-registration predicates
succeed. Admitted settings alone do not establish that either connected mode
can compose every required durable and provider adapter. The composition owner
rejects startup until the relevant closure predicate is satisfied.

The rollout profile id is process wiring rather than repository policy. Its
threshold document must be fixed before the observation window; changing the id
starts a distinct evidence population and therefore requires a new process.
Config epochs remain hot because a policy activation changes the repository
decision, whereas silently relabeling retained evidence would invalidate the
precommitment proof.

## 4. Build Identity

`build-identity.v1.json` is an immutable package resource with:

```text
schemaVersion = ci-coordinator-build-identity/v1
releaseIdentity: lowercase SHA-256
sourceCommit: canonical 40-64 hexadecimal Git object id
productionEligible: exact boolean
```

Image construction renders this resource from release-owned inputs. A
production-eligible identity cannot use development sentinels. Parsing requires
duplicate-free canonical JSON and one trailing LF; the installed resource and
documentation mirror are package-witnessed byte-for-byte.

The resource prevents ambient configuration from relabelling one executable
artifact as another. It does not prove that a registry digest, source commit,
or release identity is true; the release owner must establish those external
facts.

## 5. Caller Inventory

`runtime-caller-inventory.v1.json` lists positive runtime-authority callers. A
runtime-authority entry point starts the coordinator service, runs its health
process, or invokes operational replay. Build, test, lint, conformance, and
provider-CI commands are not runtime authority merely because they execute.

The companion `runtime-entrypoint-disposition.v1.json` closes the finite source
universe selected by its versioned discovery profile: Python project and module
entry points, declared Dockerfiles, workflow directories, deployment
directories, generated entrypoint directories, and root deployment files. Each
direct caller or source group has a disposition of `runtime_authority`,
`non_runtime`, or `unknown`.
The complete current caller set is owned by the
[runtime caller inventory](../../specs/ci-coordinator-runtime/runtime-caller-inventory.v1.json),
including the runtime, replay, database-access and target-artifact entry
points and container callers. Its non-runtime
workflow group contains sorted member identities and a SHA-256 fingerprint of
its complete canonical content. Thus a new member, changed command, changed
workflow, or new file in a declared root cannot inherit a disposition: the
native witness fails until a reviewed resource update classifies it.

The generated target consumer under `.ci-coordinator` is a `non_runtime`
source group: its Node control bundle and data artifacts request, validate and
consume CI plans without starting the coordinator service. The complete sorted
member set and canonical content fingerprint are disposition-bound, including
the previously empty `generated` and `scripts/generated` discovery roots.
This classification neither applies the backend CPython singleton to Node
tooling nor grants production or omission authority to generated artifacts.

The repository-root `compose.yaml` is classified as `non_runtime`: it is an
owned local-development topology, not a production entry point or deployment
authority. Its exact member identity and content hash remain disposition-bound,
so changing or adding a root deployment file still requires explicit review.
The `ci-coordinator-consumer-lab` console script is also `non_runtime`: its
trusted bootstrap materializes an exact-commit private source image and then
produces provider-independent conformance evidence. It cannot start the service
or receive production authority.

Admission rejects `unknown` records and requires a bijection between positive
disposition records and caller-inventory rows over `(callerId, path, kind,
currentTarget)`. The installed resource is package-bound; the native witness
proves the repository profile's source facts. Neither fact discovers external
deployment configuration, so neither transfers authority.

The root has exact identity fields:

```text
schemaVersion = ci-coordinator-runtime-caller-inventory/v1
inventoryId = ci-coordinator/runtime-callers/v1

schemaVersion = ci-coordinator-runtime-entrypoint-disposition/v1
dispositionId = ci-coordinator/runtime-entrypoint-disposition/v1
```

Every row has these fields:

```text
callerId, path, kind, currentTarget

sourceId, path, kind, currentTarget, memberIds, disposition, callerId
```

`kind` is one of `container`, `python_console_script`,
`python_module`, `workflow`, `deployment`, or `generated_entrypoint`.
All identities, paths, targets, and member lists are bounded. A path is intentionally not
unique: Docker has both a command and health check. The package witness proves
both installed resources byte-identical to canonical sources; source
enumeration is a separate native witness.

`currentTarget` names the executable declaration found at the record's own
source path. Completeness is checked against the canonical caller inventory and
entrypoint dispositions described above.

The interpreter contract is the singleton
`{GIL-enabled CPython 3.13.15}`. The duplicate-free machine profile, runtime
admission, provider workflow, package marker, development toolchain, and
container are exact projections of that set. The packaging field
`requires-python = "==3.13.15"` is therefore an exact admission constraint,
not a wider compatibility envelope. Consequently:

```text
RuntimeAdmitted iff ExactRuntimeIdentity = GIL-enabled CPython 3.13.15
```

Every other patch is rejected both by package installation and again before
application startup. A free-threaded build with the admitted patch is rejected
through CPython's
`Py_GIL_DISABLED` build variable because its ABI and concurrency semantics are
not covered by the project's compatibility matrix.

The admission predicates are deliberately distinct:

```text
RuntimeEntrypointEvidenceAdmissible(I, D, P) iff
  I is schema-admitted and packaged
  and D is schema-admitted and packaged
  and DiscoveryProfileIsEnumerated(P, D)
  and RuntimeAuthorityRowsExactlyMatch(I, D)
  and UnknownSourceCount(D) = 0

RuntimeCompositionAdmissible(I, D, P, A) iff
  RuntimeEntrypointEvidenceAdmissible(I, D, P)
  and AdapterClosureAdmissible(A)
```

The inventory is necessary because a wheel or ASGI app does not prove what a
Docker entrypoint, package script, workflow, or deployment manifest executes.

## 6. Failure Algebra

```text
missing_required_setting
invalid_setting_value
incomplete_signing_configuration
duplicate_caller_id
duplicate_caller_target
unclassified_caller
invalid_caller_target
invalid_entrypoint_disposition
duplicate_entrypoint_source
unclassified_entrypoint_source
invalid_entrypoint_target
unknown_entrypoint_source
unmatched_runtime_authority
unsupported_python_runtime
```

Failures contain a stable code and field identity only. They never include a
DSN, private key, webhook secret, bearer token, or copied environment value.

## 7. File Ownership

| File                                                                | Single responsibility                                                   |
|---------------------------------------------------------------------|-------------------------------------------------------------------------|
| `runtime_settings/contracts.py`                                     | immutable mode-specific settings, secret wrapper, and rejection algebra |
| `runtime_settings/authority_contracts.py`                           | immutable caller and entrypoint-disposition inventory values            |
| `runtime_settings/admission.py`                                     | mapping-to-settings admission only                                      |
| `runtime_settings/redaction.py`                                     | public redacted projection only                                         |
| `runtime_settings/caller_inventory.py`                              | strict inventory parsing, duplicate rejection, and bundled loading      |
| `runtime_settings/entrypoint_disposition.py`                        | profile and source-disposition admission plus caller binding            |
| `runtime_settings/python_runtime.py`                                | exact CPython profile parsing and entrypoint admission                  |
| `runtime_settings/build_identity.py`                                | canonical build-identity parsing, loading, and image-build rendering    |
| `runtime/environment.py`                                            | one process snapshot and bounded file-backed secret projection          |
| `runtime/healthcheck.py`                                            | listener-derived container liveness probe with explicit exit status     |
| `runtime_settings/resources/runtime-caller-inventory.v1.json`       | tracked caller authority input                                          |
| `runtime_settings/resources/runtime-entrypoint-disposition.v1.json` | tracked finite discovery profile and classifications                    |
| `runtime_settings/resources/python-runtime-profile.v1.json`         | exact supported interpreter set                                         |
| `runtime_settings/resources/build-identity.v1.json`                 | packaged executable artifact identity                                   |

No runtime-settings file imports FastAPI, SQLAlchemy, a GitHub client, a
planner, or an environment module.

## 8. Required Falsifiers

- a process setting read inside a domain module fails import-boundary analysis;
- a partial signing pair is rejected before composition;
- a partial control-plane identity block, non-exact issuer/client identity,
  non-exact origin, non-loopback HTTP origin, non-canonical 256-bit session key,
  unsupported signing algorithm, or duration outside `[60, 900]` passes;
- a disabled settings value contains a dependency or secret-bearing field;
- an unknown or mode-irrelevant `CI_COORDINATOR_` key is silently ignored;
- a non-canonical bind host reaches Uvicorn, or a wildcard listener is probed
  through the wrong loopback address;
- direct and file-backed forms of one secret coexist, or a relative,
  non-regular, mutable, group- or world-writable, oversized, or non-UTF-8
  secret file is read;
- enforcing settings omit a receipt, trust key, artifact, environment, or
  scope input, or admit an enforcement scope outside operator authority;
- an invalid scalar is reported against a different input field;
- a non-ASCII integer, out-of-range integer, malformed UTF-8 scalar, secret over
  65,536 UTF-8 bytes, or break-glass bearer outside 32 to 4,096 base64url bytes
  passes admission;
- a compact JWT is admitted as break-glass, making machine and emergency
  credential languages overlap;
- an empty required workflow-reference or control-plane-scope set is constructed;
- duplicate CSV members bypass the raw count or byte bound;
- a non-enforcing profile omits the startup reconciliation bound but composes;
- a secret appears in the public projection or rejection text;
- a non-canonical or credential-bearing proxy URL passes, disabled mode accepts
  proxy wiring, the proxy URL appears in the public projection, ambient proxy
  variables alter routing, or one owned GitHub/OIDC client bypasses the single
  admitted proxy value;
- an unclassified executable caller passes inventory admission;
- duplicate JSON keys in the inventory reach admission;
- an installed inventory resource differs by one byte from its canonical source;
- duplicate caller identity or `(path, currentTarget)` passes inventory admission;
- an unknown source, unbound positive source, changed grouped member, or a new
  file in a declared discovery root passes disposition admission or enumeration;
- a policy update mutates an already admitted settings value rather than using
  an epoch activation;
- `disabled` construction exposes selected-plan issuance;
- a mutable process input can relabel the packaged release identity or source
  commit, or a development sentinel becomes production eligible;
- an implementation other than CPython, a patch other than `3.13.15`,
  or a free-threaded build reaches a runtime or replay entrypoint;
- packaging, provider matrix, container, static target, canonical profile, or
  packaged profile diverges from the admitted interpreter set.
- optimized Python removes a health failure or converts an unhealthy response
  into a successful container probe.

## 9. Non-Claims

This module does not read the real process environment, resolve or rotate
secrets, reload process wiring, construct dependencies, start an ASGI server,
  authenticate a control-plane principal, activate an epoch, discover external deployment
inventory, authenticate deployment facts, or approve production omission.
