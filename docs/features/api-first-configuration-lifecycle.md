# API-First Configuration Lifecycle

Status: accepted design; CI-019 read-model and preview delta provisional
Last updated: 2026-10-11
Owner requirements: `REQ-CI-CORE-016`, `REQ-CI-CONTROL-009`

## 1. Decision

CI Coordinator exposes repository configuration validation, registration,
status, epoch-to-epoch preview, and exact retained-source export through
versioned HTTP contracts before adding their UI projections.

The capability has four owners:

| Concern                             | Owner            | Authority                                                               |
|-------------------------------------|------------------|-------------------------------------------------------------------------|
| Source admission                    | `config_control` | Decode, normalize, compile, and derive one immutable epoch              |
| Registration and activation algebra | `config_epochs`  | Operation identity, replay, conflict, revision, and audit pairing       |
| Use-case orchestration              | `app`            | Scope authorization and composition of admitted capabilities            |
| HTTP representation                 | `api.http`       | Authentication, role admission, bounded wire models, and status mapping |

PostgreSQL retains config content and registration operation receipts in
different relations. The immutable audit ledger records evidence but is not a
query or replay authority.

This design governs standalone registration through the configuration API.
Workflow proposal acceptance may insert or reuse the same immutable content
inside its own operation, receipt, and paired audit transaction. It does not
create a standalone registration receipt because the proposal-review receipt
already owns that command's replay identity.

## 2. Public Operations

The Batch D transport profile is finite and exact:

| Operation       | Method and path                                                                               | Role        | Effect                                    |
|-----------------|-----------------------------------------------------------------------------------------------|-------------|-------------------------------------------|
| Validate source | `POST /api/v1/config/validations`                                                             | `configure` | Pure configuration use case               |
| Register epoch  | `POST /api/v1/config/epochs`                                                                  | `configure` | PostgreSQL and audit                      |
| Activate epoch  | `POST /api/v1/config/activations`                                                             | `activate`  | PostgreSQL, audit, and attested authority |
| Roll back epoch | `POST /api/v1/config/rollbacks`                                                               | `activate`  | PostgreSQL and audit                      |
| Read status     | `GET /api/v1/config/repositories/{installation_id}/{repository_id}/status`                    | `read`      | Bounded read                              |
| Export source   | `GET /api/v1/config/repositories/{installation_id}/{repository_id}/epochs/{epoch_id}/source`  | `configure` | Exact read                                |
| Preview epoch   | `GET /api/v1/config/repositories/{installation_id}/{repository_id}/epochs/{epoch_id}/preview` | `read`      | Two exact reads, pure comparison          |

Activation admits three request generations: v1 is exact retained replay only,
v2 selects a proposal review by `reviewOperationId`, and v3 selects a reviewed
registered epoch by `registeredEpochReviewOperationId` and
`expectedSourceHash` (ROADMAP CI-018). A body that mixes subject selectors is
rejected at the wire. The registered epoch's review itself starts at
`POST /api/v1/repository-attestations/github/start` with
`ci-registered-epoch-attestation-start/v1`; its algebra is owned by
[proposal review registration](../architecture/modules/proposal-review-registration.md#12-registered-epoch-review-subject).

This map closes only configuration lifecycle API coverage. The aggregate
`REQ-CI-CONTROL-008` remains deferred until every product operation and its UI
projection are covered.

### 2.1 CI-019 Provisional Owner Decisions (2026-10-11)

Each item below is a provisional owner decision (2026-10-11) for ROADMAP
CI-019; revisit it when its falsifier is observed.

- **D1, files.** "Files" means the existing single-document JSON or YAML 1.2
  source import (UI file input) and exact retained-source export. There is no
  multi-file bundle or repository-resident policy source: the policy schema is
  one closed document and content addressing binds one source byte sequence.
  Falsifier: the schema admits includes, or the owner requires a
  repository-resident policy file.
- **D2, dry-run.** Validation remains the pure dry-run of unregistered source.
  A separate read-only preview compares two retained same-scope epochs:
  `ci-config-epoch-preview/v1` returns the `policy-semantic-diff/v1` changed
  pointers and hash from the proposal-review owner and the rollback coverage
  relation of the target against the base. Falsifier: the coverage relation
  cannot be computed from two re-admitted drafts alone.
- **D3, visibility.** Status and preview are metadata reads with the `read`
  role; exact source export keeps `configure`; validation and registration keep
  `configure`; activation and rollback keep `activate`. Admission stays all-of:
  an `activate`-only actor sees no status or preview and must also hold `read`.
  Pointer disclosure analysis: every object in
  `repository-policy.schema.v1.json` is closed (`additionalProperties: false`)
  and the diff collapses any changed array to its own pointer, so every pointer
  segment is a schema field name. User-authored text such as rule names,
  branches, signal names or paths never appears in a pointer. Falsifier: the
  policy schema admits an open map or the diff descends into array members;
  then the pointer list must move behind `configure` and `read` keeps only the
  coverage relation, pointer count, limit state and diff hash.

Status changed shape for D8 rollback-target visibility, so its success schema
is `ci-config-epoch-status/v2`. A strict v1 client rejects the added field and
fails closed; there is no dual-version negotiation on the same route.

## 3. Formal Model

Let:

- `S = (installationId, repositoryId)` be one repository scope;
- `B` be exact source bytes and `F` their declared source format;
- `A(B, F)` be the total bounded admission result;
- `E` be the content-addressed epoch identity returned by successful admission;
- `O` be a client-generated operation identity;
- `P` be the authenticated actor; and
- `I` be a retained contract-resource identity bounded to 4096 UTF-8 bytes; and
- `R = (S, O, E, P, H)` be an immutable registration receipt whose `H` binds
  the paired audit input.

Admission obeys:

```text
A(B, F) = Accepted(D) xor Invalid(diagnostics) xor Unavailable
Accepted(D) => D.sourceBytes = B and D.scope = S and D.epochId = E
Validate(B, F, P) and Register(B, F, O, P) invoke the same A
ValidateUseCase after transport admission
  => no clock, config transaction, config store, audit, or provider capability
```

Registration obeys:

```text
NoReceipt(S, O)
  => atomically InsertOrReuse(E) + AppendAudit(H) + InsertReceipt(R)

Receipt(S, O) = R and ExactClientFacts(R, B, F, P)
  => Duplicate(E) and Writes = 0

Receipt(S, O) = R and not ExactClientFacts(R, B, F, P)
  => OperationConflict and Writes = 0
```

`ExactClientFacts` compares the admitted draft, source format, exact bytes,
scope, actor, and operation identity. It deliberately excludes the new
server-owned occurrence time used by an unknown-commit retry.

For concurrent attempts with the same `(S, O)`, a repository-scope advisory
lock and a primary key on `(S, O)` serialize the decision. Therefore:

```text
same prior receipt absence and one transaction boundary
  => at most one committed receipt and one paired audit event
```

## 4. State And Evidence Separation

The registration receipt and audit event are both required:

```text
Receipt => replay and conflict authority
AuditEvent => tamper-evident historical evidence
Receipt != AuditEvent
```

The distinction is necessary because audit retention and replay traversal are
independent from command idempotency. If the ledger alone were the receipt,
registration correctness would depend on an evidence-query lifecycle and the
repository rule that query tables do not replace the ledger would be violated.

One forward expand migration adds `config_epoch_registrations` and a distinct
`config-epoch-registration-operations/v1` capability. The existing
`config-epoch-lifecycle/v1` descriptor remains immutable, so an older binary
can still attest and use its original closure after the additive migration.
Registered-epoch review likewise uses its own expand revision `20261011_0019`
and capability `registered-epoch-review/v1` instead of widening an existing
descriptor.

## 5. Read Semantics

Status is a live, bounded keyset traversal over immutable epochs plus the
current mutable active pointer. Each projected epoch carries
`previouslyActivated`, the exact rollback-target predicate of the lifecycle
owner: a same-scope activation-journal transition whose pair-owned audit event
is `config-epoch-activation/v1`. A rollback transition is not activation
evidence. One extra query evaluates this predicate for the at most 100 page
identities; no index, table or migration is added. The flag is a read-time
projection; the rollback command re-evaluates it under the scope lock.

```text
Status(S, after, limit)
  => Authorized(P, S)
  and 1 <= limit <= MAX
  and rows are filtered by S before projection
  and every projected row reports S
  and count(rows) <= limit
  and rows are ordered by epochId
```

The active pointer owns `revision`. An immutable epoch does not own an
activation revision because the history `A@1 -> B@2 -> A@3` makes
`revision(A)` non-functional. Clients that need a stable snapshot restart
traversal when the active pointer or collection changes; Batch D does not
claim snapshot pagination over concurrent appends.

Exact-source export filters by `(S, E)` in SQL before decoding and re-admits the
stored draft before returning bytes. Success guarantees:

```text
response.body = retained source_bytes
ETag = quoted domain-separated sourceHash
Content-Digest = RFC 9530 raw sha-256 digest of response.body
```

Missing, cross-scope, malformed, or integrity-invalid rows cannot publish any
stored bytes.

Preview loads the base and target epochs through the same scope-filtered,
re-admitting read, then reuses `compare_policy_drafts` and the conservative
rollback coverage comparator:

```text
Preview(S, base, target)
  => Authorized(P, S) and both epochs retained in S
  and changedPointers = PolicySemanticDiff(base, target).changedPointers
  and coverageRelation = Coverage(active := base, target)
  and no clock, transaction write, audit or provider effect
```

An over-limit diff returns `diffState = limit_exceeded`, no pointers and no
hash, but keeps the coverage relation. A missing epoch is `404`. The preview is
advisory: activation and rollback re-check coverage, review and revision at
command time. Each preview re-admits both drafts inside the request; this is
the same CPU cost as rollback admission and is not process-isolated.

## 6. HTTP And Error Algebra

Authentication and role admission precede use-case execution. The transport
profile classifies effects after this prerequisite request-admission stage;
browser identity admission may read its own identity state. Mutation CSRF
admission applies to validation and commands because browser credentials are
accepted and every POST must be origin-bound even when its configuration
application effect is pure. Query routes require scope authorization before
repository access.

The closed outcomes are:

| Outcome                              | HTTP status |
|--------------------------------------|-------------|
| Created                              | `201`       |
| Exact replay                         | `200`       |
| Valid pure result or status/export   | `200`       |
| Unauthenticated                      | `401`       |
| Forbidden or cross-scope             | `403`       |
| Missing epoch                        | `404`       |
| Operation or revision conflict       | `409`       |
| Invalid source/request               | `422`       |
| Overloaded or dependency unavailable | `503`       |

No route owns policy semantics. Pydantic owns untrusted wire shape only; domain
constructors and application outcomes own behavioral invariants.

OpenAPI carries both character bounds and the repository extension
`x-max-utf8-bytes` for every UTF-8 byte-bounded string. The character bound is
a portable early rejection; the byte extension is the exact wire contract.
Domain construction remains authoritative, so multi-byte input cannot bypass
the declared byte budget.

Success models are exact projections of their runtime value domains: repository
identities and revisions are positive JSON-safe integers, epoch and content
identities are lowercase SHA-256 values, source size is bounded by the admitted
source budget, and contract-resource identities satisfy the same UTF-8 bound as
durable storage. Therefore generated clients cannot treat a value that the
runtime can never produce as part of the supported API.

## 7. Minimal Abstraction Proof

The design adds only boundaries with distinct change predicates:

1. `ConfigAdmissionService` changes when source admission or scope admission
   changes. Its declared runtime dependencies grant no clock, config store,
   audit, transaction, or provider authority.
2. Registration contracts change when replay, audit, or operation identity
   changes.
3. Configuration queries change when bounded read or export semantics change.
4. HTTP DTOs change when the external representation changes.

A class per endpoint is rejected because endpoint count does not prove a new
semantic owner. Reusing the workbench snapshot is rejected because it has a
different consumer, projection, and pagination contract. Extending the old
database capability descriptor is rejected because capability identity is
immutable. The selected boundaries are therefore the minimum set that
separates different authorities and lifecycle predicates.

## 8. Falsifiers

The design is false if any assigned witness permits:

- validation to touch clock, transaction, store, audit, or provider code;
- validation and registration to derive different admitted drafts;
- the same `(scope, operationId)` with changed source, format, actor, or scope
  to replay successfully;
- an epoch, receipt, or registration audit event to commit without the other
  required records;
- two concurrent attempts to commit two receipts for one operation;
- a different operation with the same source to create a second content epoch;
- status to exceed its bound, return source bytes, or traverse nondeterministically;
- a cross-scope query to materialize or emit a row;
- export to return bytes after any stored-draft integrity mutation;
- status to report `previouslyActivated` for a rollback-only, foreign-scope or
  registration-only epoch;
- preview to emit a policy value, write state, or differ from the review diff
  owner or rollback coverage owner for the same two drafts; or
- a success response schema to admit values outside its runtime result domain; or
- runtime routes, generated OpenAPI, and the exact transport profile to differ.

## 9. Non-Claims And Revision Conditions

This design does not claim complete product API/UI parity, provider-side
configuration, deployment, production identity installation, snapshot
pagination, retention policy, backup, failover, or dynamic CI omission.

Revisit the design if config sources exceed the admitted body budget, immutable
epoch identities cease to be content-addressed, operation receipts receive a
retention lifecycle, or measured status traversal requires a server-side
snapshot cursor. Each condition requires a new owner-approved contract rather
than an implicit behavior change.
