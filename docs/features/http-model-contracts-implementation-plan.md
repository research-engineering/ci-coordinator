# HTTP Model Contracts Implementation Plan

Status: active implementation plan

Last updated: 2026-08-30

Design: [HTTP Model Contracts](http-model-contracts.md)

Owner requirement: `REQ-CI-RUNTIME-029`

## 1. Objective

Replace divergent per-module Pydantic configuration with one enforced request
policy, one enforced response policy, and an explicit trusted-attribute
projection role while preserving every admitted wire key and route outcome.

## 2. Preconditions

- The exact base tree is recorded before the migration.
- Current OpenAPI and frontend projections provide the compatibility baseline.
- The HTTP model inventory is complete over tracked production Python files.
- Historical design and implementation plans remain unchanged.

## 3. Implementation Sequence

1. Add `api/http/model_contracts.py` with the two directional policies, the
   bounded trusted-attribute role, and one final JSON-mode serializer.
2. Migrate every HTTP request DTO to `RequestModel`; replace `alias=` with
   explicit `validation_alias`, retaining request `serialization_alias` only
   for the plan DTO that is serialized into the canonical parser.
3. Migrate every HTTP response DTO to `ResponseModel`; remove repeated aliases
   proved equivalent to the serialization generator and preserve the readiness
   exception explicitly. Admit `ProjectedResponseModel` only for the exhaustive
   inventory that consumes trusted record attributes.
4. Remove redundant `Strict*` annotations because strictness is now enforced
   by the owning base policy.
5. Replace direct `model_dump` calls with the owned wire-mapping operation and
   change response constructors to internal field names.
6. Enable the Pydantic mypy plugin with typed, extra-forbidding constructor
   checks and required-dynamic-alias rejection.
7. Add exhaustive policy and falsification witnesses for aliases, coercion,
   shallow freezing, bounded attribute access, nested revalidation, direct and
   nested finite numbers, defaults, JSON mode, schema mode, and typed
   constructors. Extend the existing bounded HTTP mutation inventory with
   named policy relaxations instead of introducing a second runner.
8. Regenerate OpenAPI and TypeScript projections, conservatively normalizing
   recursive values that the TypeScript generator cannot represent directly.
9. Bind source, design, plan, generated consumer, and tests to
   `REQ-CI-RUNTIME-029`.

## 4. File Ownership

| Surface                                                            | Change                                                    |
|--------------------------------------------------------------------|-----------------------------------------------------------|
| `backend/src/ci_coordinator/api/http/model_contracts.py`           | Own shared representation policy only                     |
| Existing HTTP contract/router modules                              | Retain concrete fields, validators, and transport mapping |
| `backend/tests/unit/api/http/test_model_contracts.py`              | Own runtime model-algebra falsifiers                      |
| `backend/tests/unit/api/http/test_model_contract_source_policy.py` | Own static source-policy and constructor falsifiers       |
| `backend/pyproject.toml`                                           | Admit static Pydantic constructor enforcement             |
| `frontend/src/api/generated.ts`                                    | Reproducible generated consumer projection                |
| Runtime requirement and Proofkit files                             | Bind the claim to native witnesses                        |

No domain package, database schema, provider adapter, route path, or
authorization policy changes in this batch.

## 5. Required Evidence

Local admission is limited to static and generated-contract checks permitted by
the repository verification policy:

- Ruff formatting and lint;
- strict mypy with the Pydantic plugin;
- Python import-boundary policy;
- requirement-source and Proofkit admission;
- documentation graph and text policy; and
- deterministic OpenAPI/frontend contract generation and comparison.

GitHub CI owns behavioral pytest, branch coverage, browser, PostgreSQL,
container, mutation, and connected-stack evidence. The PR is mergeable only
after every required check succeeds on the exact reviewed head.

## 6. Rollback And Falsification

Rollback is one squash revert because the migration changes no durable data.
Do not merge if any existing request/response golden changes unexpectedly, the
alias inventory is non-injective, a request field loses its canonical wire
name, the readiness exception changes, or generated consumer checks are stale.

## 7. Completion

The plan is complete only when the exact PR head is independently reviewed,
all authoritative GitHub checks pass, the squash commit has one parent and the
reviewed tree, and the post-merge Full Check succeeds.
