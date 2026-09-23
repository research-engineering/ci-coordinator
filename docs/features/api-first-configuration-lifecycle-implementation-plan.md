# API-First Configuration Lifecycle Implementation Plan

Status: implemented; exact-head verification pending
Last updated: 2026-09-04
Design: `docs/features/api-first-configuration-lifecycle.md`
Owner requirements: `REQ-CI-CORE-016`, `REQ-CI-CONTROL-009`

## 1. Completion Predicate

The batch is complete exactly when:

```text
Complete :=
  DesignAccepted
  and ExactTransportProfileAdmitted
  and PureValidationImplemented
  and RegistrationPairAtomic
  and RegistrationReplayExact
  and StatusBoundedAndScoped
  and ExportExactAndFailClosed
  and SuccessSchemasEqualRuntimeDomains
  and OpenApiAndTypeScriptCurrent
  and ProofBindingsComplete
  and ExactHeadFullCheckGreen
  and IndependentCloseoutHasNoUnresolvedP0P2
```

`Complete` does not promote aggregate `REQ-CI-CONTROL-008`; UI parity and the
remaining product operations stay in the next batch.

## 2. Ordered Implementation

### Step 1: Freeze Contracts

1. Add blocking `REQ-CI-CONTROL-009` for this bounded lifecycle subset.
2. Extend `REQ-CI-CORE-016` with operation-aware atomic registration.
3. Add `config-lifecycle-http-profile.v1.json` with exact operation, method,
   path, role, effect, request, response, and bound fields.
4. Add a static profile validator and deletion/mutation falsifiers.

Exit condition: every subsequent production and witness file has a normative
owner and no claim is stronger than its assigned evidence.

### Step 2: Separate Pure Admission

1. Add `app/config_admission.py` with a closed accepted, invalid, forbidden,
   unavailable result algebra.
2. Give the service only `ConfigScopeAuthorizer` and `PolicyAdmission`.
3. Route both validation and registration through this service.

Exit condition: the validation use case's declared runtime dependency topology
grants no config store, clock, audit, transaction, or provider access after
transport request admission.

### Step 3: Add Registration Authority

1. Add `config_epochs/registration.py` containing command, replay identity,
   single-use prepared audit pair, immutable receipt, and result algebra.
2. Add `config_epoch_registrations` metadata with scoped operation primary key,
   scoped epoch foreign key, unique audit foreign key, hash constraints, and an
   immutable trigger.
3. Add one forward Alembic expand migration and append a generation-two
   compatibility declaration.
4. Add the separate registration capability definition, exact schema
   attestation, operation admission, and exact runtime column grants.
5. Implement scope-lock, replay classification, content insert/reuse, paired
   audit append, and receipt insert inside one unit of work.

Exit condition: transaction failure, cancellation, audit failure, or receipt
failure leaves no partial registration state.

### Step 4: Add Bounded Queries

1. Add config query value objects for immutable epoch summaries and one page.
2. Add scope-first SQL for status and exact epoch load.
3. Add `app/config_queries.py` for authorization and typed unavailable outcomes.
4. Keep active pointer revision separate from immutable epoch identity.

Exit condition: every query has a finite row bound and cross-scope data is
rejected before row projection.

### Step 5: Add HTTP Projection

1. Move configuration DTOs to `api/http/config_lifecycle_contracts.py`.
2. Keep command routes in `routers/config_management.py`.
3. Add validation, status, and export routes in
   `routers/config_lifecycle_queries.py`.
4. Reuse the existing authentication, role, CSRF, body-limit, and typed error
   policies.
5. Emit exact source bytes with strong ETag and RFC 9530 `Content-Digest`.
6. Project hash, identifier, byte-count, and JSON-safe integer bounds without
   widening their domain contracts.

Exit condition: routers only map transport values and outcomes; no business
policy is duplicated in HTTP code.

### Step 6: Prove And Publish

1. Add unit falsifiers for every closed result and field mutation.
2. Add PostgreSQL integration witnesses for atomicity, exact replay, concurrent
   registration, scope-first reads, migration replay, privileges, and schema
   attestation.
3. Regenerate OpenAPI and TypeScript contracts; do not add UI business logic.
4. Update documentation indexes, Diataxis API how-to, Proofkit routes, source
   digests, and witness plan.
5. Run allowed local static gates only.
6. Push the additive branch and run exact-head Full Check and CodeQL in GitHub.
7. Run exactly one independent `gpt-5.6-sol/max` closeout review on the frozen
   green commit, resolve valid findings, rerun required checks, and squash merge.

## 3. File Topology

New production ownership units:

| Path                                                          | Single responsibility                    |
|---------------------------------------------------------------|------------------------------------------|
| `app/config_admission.py`                                     | Effect-free source and scope admission   |
| `app/config_queries.py`                                       | Authorized bounded lifecycle reads       |
| `config_epochs/registration.py`                               | Registration operation and audit algebra |
| `config_epochs/queries.py`                                    | Immutable query value objects            |
| `api/http/config_lifecycle_contracts.py`                      | Versioned wire DTOs                      |
| `api/http/routers/config_lifecycle_queries.py`                | Validation/status/export mapping         |
| `persistence/config_epoch_registration_schema_attestation.py` | Exact facts for the new capability       |

Existing files may grow only where the responsibility already exists:

- `_schema_config_epochs.py`: SQLAlchemy metadata;
- `config_epoch_repository.py`: config-owner PostgreSQL operations;
- `runtime_adapters.py`: one transaction per application operation;
- `schema_capabilities.py`: capability registry;
- `runtime_principal_access.py`: runtime grant projection;
- runtime composition and HTTP app: dependency assembly only.

## 4. Witness Matrix

| Property               | Required counterexample killed                                                          |
|------------------------|-----------------------------------------------------------------------------------------|
| Pure validation        | A spy store, clock, UoW, audit sink, or provider is touched                             |
| Shared admission       | One byte or format mutation produces divergent validation and registration drafts       |
| Exact replay           | Any client-owned registration field changes under the same operation ID                 |
| Server-time neutrality | Same exact retry receives a later clock value and conflicts                             |
| Pair atomicity         | Epoch, audit, or receipt survives a failure of either sibling write                     |
| Concurrency            | Two same-operation workers both commit                                                  |
| Content reuse          | Two operation IDs with equal source create two epochs                                   |
| Scope safety           | A foreign scope causes row materialization or source emission                           |
| Status bound           | Result cardinality exceeds requested or maximum limit                                   |
| Export integrity       | Mutated bytes, format, hash, profile, or epoch identity are emitted                     |
| Wire byte bounds       | A multi-byte string exceeds its UTF-8 budget while satisfying its character bound       |
| Success projection     | OpenAPI admits an impossible hash, identifier, byte count, or safe-integer result       |
| Authority consistency  | Lifecycle roles or provider effects diverge from the organization control-plane profile |
| Transaction adapter    | Registration bypasses commit, rollback, or store-unavailable translation                |
| Transport parity       | Profile, FastAPI route graph, OpenAPI, or generated TypeScript differs                  |
| Compatibility          | Old capability cannot attest after expand migration or new code admits old head         |

## 5. Change And Rollback Policy

The migration is additive. Code rollback is admissible while the current
database declaration still provides every predecessor capability and the old
binary's exact attestation passes. The predecessor binary has no privileges on
the new relation and cannot mutate it. Database downgrade is allowed only by
the existing pre-retention protocol; after retained production state, recovery
is forward-only.

No migration file is amended after publication. No compatibility descriptor is
silently widened. No receipt is synthesized for historical raw registrations
because the missing operation identity and actor cannot be reconstructed.

## 6. Review Checkpoints

Review after each semantic unit, but publish one coherent PR:

1. contract and migration proof;
2. registration algebra and persistence atomicity;
3. query and HTTP boundary;
4. generated artifacts and Proofkit closure;
5. exact-head independent closeout.

A checkpoint is accepted only when its assumptions, alternative, falsifier,
and non-claims are explicit. Size alone is not a god-file proof; any file that
acquires a second independent policy owner must be decomposed before closeout.
