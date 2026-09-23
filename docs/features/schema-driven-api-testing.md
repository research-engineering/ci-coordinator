# Schema-Driven API Testing

Status: proposed bounded testing capability
Owner: repository test portfolio
Base: `7911c7dcc6ddd1ed1c5c0bc18a8f7fa6817a32b2`

## Decision

Add pinned Schemathesis development tooling and a small native pytest contract
cohort. Test actual FastAPI routers, admission and wire responses through an
isolated ASGI composition. Extend generated input exploration without replacing
the existing independent domain, security, database, mutation or browser oracles.
The [implementation plan](schema-driven-api-testing-plan.md) owns delivery order;
the [testing contract](../architecture/cross-cutting/testing-and-proofkit.md)
continues to own requirement and witness admission.

Production dependencies, routes, schema exposure, databases and credentials
are unchanged. One discovered transport defect is corrected below; accepted
commands and business policy are unchanged. No external test service or new scheduler is
introduced. Local developer and native CI commands use the same locked tests;
agent execution placement still follows the organization verification policy.

## Scope And Capability Dispositions

| Capability                                 | Admission                                                                                                              |
|--------------------------------------------|------------------------------------------------------------------------------------------------------------------------|
| OpenAPI positive/negative generation       | Enabled for the exact operation manifest.                                                                              |
| Examples, boundary coverage, fuzzing       | Enabled with independent positive controls and finite profiles.                                                        |
| Status, media, headers and response schema | Checked against raw operation contracts and scenario-specific outcomes.                                                |
| Authentication probes                      | Enabled with synthetic credentials; all follow-up requests stay in-process.                                            |
| Custom domain-aware checks                 | Preserve contextual rejection, scope, no-store and no-effect expectations.                                             |
| Shrinking and reproduction                 | Shrink supported fuzzing failures; retain exact explicit/coverage cases and their phase, versions and schema identity. |
| Deeper exploration                         | Explicit bounded profile, not automatic post-merge duplication.                                                        |
| Stateful generation                        | Not claimed for read/validation operations; real durable sequences retain PostgreSQL owners.                           |
| Load and network/server testing            | Existing separate owners; in-process latency is not production capacity.                                               |
| GraphQL, SSE, arbitrary live URLs          | Not applicable to this declared HTTP cohort; no generic target runner.                                                 |

Initial exact operations:

1. `GET /api/v1/workbench/repositories/{installation_id}/{repository_id}`.
2. `GET /api/v1/config/repositories/{installation_id}/{repository_id}/status`.
3. `POST /api/v1/config/validations`.

These exercise independent path/query/body and response boundaries. Every other
operation is outside the generated cohort, not silently covered or safe. Expand
only with a meaningful positive path, safe effect model, owner-specific oracle
and measured marginal benefit. This is not whole-API or exhaustive-input proof.

## Formal Claims And Falsifiers

Let `S(x)` be structural schema validity, `A(x,c)` current authorization and
`D(x,c)` domain/state validity under context `c`.

```text
S(x) does not imply A(x,c) or D(x,c).
S(x) does not imply status(I(x,c)) = 2xx.
Finite generated observations do not prove all possible requests correct.
```

A config envelope's `source` string can satisfy OpenAPI yet contain invalid
policy bytes. Its rejection must remain visible and typed. Standard character
length also does not replace UTF-8 byte limits. Do not globally accept all 422,
all errors, or disable a check just to obtain a green campaign.

| Guarantee                           | Protected observable                               | Counterexample                                                   |
|-------------------------------------|----------------------------------------------------|------------------------------------------------------------------|
| Exact nonempty operation population | Current native collection identities               | Filter drops an operation or unexpectedly adds a mutation.       |
| In-process request closure          | No real network, provider or persistent writes     | Main call uses ASGI but an auth retry selects network transport. |
| Meaningful positive path            | Real router and response serialization             | Every generated request stops at401/403/404 before the use case. |
| Independent negative oracle         | Existing requirement-owned bounds                  | Malformed input is accepted, or rejected only by unrelated auth. |
| Example isolation                   | Deterministic fixture and cookie/call state        | One example's state changes another example's outcome.           |
| Native evidence integrity           | Exact setup/call/teardown and shard inventory      | Lazy subtest node ID is absent from collection.                  |
| Bounded cost                        | Finite examples, request and process limits        | Coverage/auth retries escape the fuzz example budget.            |
| Oracle sensitivity                  | Original schema versus deliberately wrong response | A modified response passes, or fails only during setup.          |

Schema and application may share Pydantic declarations. Their agreement cannot
prove that a shared incorrect declaration implements the requirement. Retain
literal semantic assertions and independent invalid values. Qualification must
detect controlled response and boundary violations through the same path used
by ordinary generated tests, and restore a passing positive control.
The contextual policy oracle reads the actual serialized JSON request, not the
generator's Python representation: an object and equivalent raw JSON bytes
must receive the same interpretation.

## Composition And Transport

Construct a test-only application with `create_app`, real routers and applicable
middleware. Use bounded synthetic capability ports and actual role admission;
unexpected mutation ports fail rather than silently succeed. Do not import the
connected runtime composition or launch background workers. Validation has no
registration write, but must retain its mutation-admission requirements.

Use the app's raw `openapi()` dictionary, not `rendered_contract()` or the
frontend file. The latter intentionally widens recursive JsonValue/FactValue
types and uses metadata-only dependencies. No production schema URL is enabled.

Bind the test application at schema level before creating operations. In
Schemathesis 4.27.4, `ignored_auth` drops the session for follow-up requests;
without `schema.app`, transport selection can fall back to real HTTP. The
egress guard must cover both direct requests and those follow-up checks.
Use the library's native synchronous ASGI path rather than a new adapter.
The scoped fixture disables Requests environment trust for every new session,
including implicit auth probes, and rejects netrc lookup as well as network
transport. Local credentials and proxy settings are not test inputs.
The built-in auth probe checks refusal status, not the full response contract
or absence of effects. Independent missing and syntactically valid invalid
credential cases must also check response schema, cache policy and protected
port calls. An auth-only response defect must be detected by those oracles.

Do not reuse async database pools across the library's AnyIO portal and another
event loop. Keep collection free of startup/network effects. Reset mutable test
state between independent examples; qualify lifespan shutdown explicitly.
Closing an ASGI session alone does not end the shared lifespan registry.

### Qualified Compatibility Repair

Native qualification exposed unclosed AnyIO memory streams in the pinned
Schemathesis `_Lifespan.stop()`: it settles the application task without closing
its two stapled streams. Preserve warnings-as-errors. A test-only, version-fenced
subclass closes those resources after native lifecycle handling, including a
failed start. It does not replace request serialization, transport or checks.
This narrow private-API dependency is preferable to another HTTP adapter or
globally ignoring resource warnings. Requalify and remove it when an upstream
release owns complete cleanup; the version guard deliberately blocks upgrades
until that decision. This is not a general patch for arbitrary ASGI applications.

### Discovered HTTP Contract Defect

FastAPI wraps a request JSON Unicode decoding failure in HTTP 400, bypassing the
existing request-validation handler and returning an undeclared `detail` body.
Normalize only HTTP 400 whose direct cause is `UnicodeDecodeError` to the existing
redacted, non-cacheable HTTP 422 `invalid_request` response. This is an explicit
wire correction for invalid input, not a change to valid configuration or
authorization. Preserve every other HTTP exception's original handling; do not
match framework message strings or turn arbitrary internal failures into 422.
Independent malformed-byte cases and unrelated-status controls qualify it.

## Test Population And Profiles

Use eager, stable operation items. Current native reporting admits one
setup/call/teardown triple per collected node. Lazy fixture subtests change node
IDs and are deliberately not used; adapting the scheduler would add unnecessary
scope. Generated request counts remain distinct from pytest item counts.
Disable automatic Schemathesis plugin loading globally and activate its public
entry point from this cohort's conftest only. Unrelated short mutation witnesses
must not pay its import and hook cost. Keep Hypothesis's lightweight plugin and
all existing timeout budgets; qualify both absent-plugin unrelated runs and
the nonempty generated cohort through native CI.

The ordinary profile is deterministic, has no shared example cache and preserves
shrinking. The explicit exploration profile increases the finite budget and
supports a caller-supplied reproduction seed. Profile selection cannot alter
the operation manifest, production guards or CI acceptance thresholds.

Bound the complete command with the existing process runner. Per-operation
example counts do not bound coverage/examples, shrinking or auth probes. A
timeout, missing operation, skipped generated test or missing positive control
is a failure/incomplete result, never success. Record wall and process cost
separately; do not equate either with saved production CPU.
Compute reaped-child CPU as the sum of separate user and system counter deltas.
An unchanged counter pair must yield exactly zero, including after unrelated
earlier subprocesses. Subtracting mixed cumulative floating-point totals can
create a negative rounding residue; independent zero/user/system controls
qualify the actual serialized summary rather than hiding it with a tolerance.
The initial admission budget is at most 10 additional seconds of native
collection and 60 seconds of cohort execution per normal invocation, including
the repeated portable invocation. Compare native timings with the baseline;
runner noise is not attributed to the library without supporting evidence.
Exceeding these budgets reopens the normal/deep split, not the existing gates.
Standalone process deadlines are 180 seconds for fast and 300 seconds for deep;
these safety ceilings do not replace the incremental performance budgets.

## Evidence And Workflow

Pin the dev dependency and update both lock projections. Existing production
installations exclude dev groups. Use the existing backend/native cohort and
Proofkit routes, preserving all present coverage and mutation gates.
The [CI trigger policy](ci-trigger-policy.md) remains authoritative: PR and
merge-group qualification, explicit dispatch, no automatic post-merge rerun.

Failure artifacts contain only synthetic inputs, version/profile/seed and
bounded diagnostics. Keep them outside the exact native shard receipt directory
and publish them on failure without converting failure into success. Confirmed
counterexamples become checked-in ordinary regression cases; transient cache
contents are not the regression authority. Reproduction binds source, lock,
schema, configuration and state, not seed alone.
Coverage and explicit examples are not promised Hypothesis minimization. A
controlled defective response must fail the same oracle after exact-case replay
and pass after restoring the response; setup failure is not reproduction.

## Alternatives And Review Conditions

Existing tests alone are cheaper but explore fewer generated combinations.
Hand-written Hypothesis strategies remain appropriate for non-OpenAPI laws;
rewriting generic HTTP generation and response validation is not justified.
A separate service/environment is unnecessary. A dedicated manual campaign
workflow is justified by qualification: the API cohort takes tens of seconds,
whereas the full portfolio takes many minutes. Full Check calls that same
workflow when deep qualification is requested and requires its success; ordinary
PRs retain the fast native cohort. Standalone campaign success is not full-PR
qualification and is never a release witness. Fuzzing all live endpoints is
rejected because side effects, state and authorization would be uncontrolled.

Reassess after dependency upgrades, operation expansion, auth/lifespan changes,
native reporter changes, measured critical-path growth or a new counterexample.
Installation and green finite tests do not prove global optimality, absolute
regression freedom, all Academic Engineering invariants or production readiness.

## Upstream Basis

- [Pinned release](https://pypi.org/project/schemathesis/4.27.4/).
- [ASGI adapter](https://github.com/schemathesis/schemathesis/blob/v4.27.4/src/schemathesis/python/asgi.py).
- [Authentication retries](https://github.com/schemathesis/schemathesis/blob/v4.27.4/src/schemathesis/specs/openapi/_auth_retry.py).
- [Pytest integration](https://schemathesis.readthedocs.io/en/stable/explanations/pytest/).
- [Configuration](https://schemathesis.readthedocs.io/en/stable/reference/configuration/).
