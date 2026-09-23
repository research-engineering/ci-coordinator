# Developer State Schema Evolution Implementation Plan

Status: accepted implementation plan

Date: 2026-09-02

Owner: `ci-coordinator.developer-environment`

Design: [Developer state schema evolution](developer-state-schema-evolution.md)

## 1. Bound Epoch And Objective

Base: `master@66bea18cffad1919282063e81b85bdf7576680ba`.

Implement one restartable V1-to-V2 local-state transition without changing
provider behavior, Compose resource identity, credentials already present, or
persistent volumes. Preserve predecessor restart behavior for schema 1 metadata
published before its environment.

## 2. Impact Declaration

| Surface                                    | Intended delta                                                  |
|--------------------------------------------|-----------------------------------------------------------------|
| `scripts/dev_environment/private_files.py` | allow same-filesystem staging outside a closed target directory |
| `scripts/dev_environment/secrets.py`       | admit exact V1 and resumable prefixes; commit V2 last           |
| V1 conformance fixture                     | freeze the pre-change key and filename sets                     |
| secret-state tests                         | prove preservation, convergence, and idempotence                |
| developer requirement package              | add the schema-evolution invariant                              |
| Proofkit graph                             | bind design, implementation, fixture, code, and falsifiers      |

No target workflow, planner, API, database schema, UI, or production secret
contract changes in this objective.

## 3. Implementation Order

1. Freeze the exact V1 environment and secret sets in a checked-in fixture.
2. Raise the current metadata schema to 2.
3. Admit identity and both finite component states before the first write.
4. Complete an environment-absent predecessor creation prefix from only absent,
   exact V1, or exact resumable V2 secret state.
5. Add the metrics token only when absent, staging temporary bytes outside the
   closed secrets directory.
6. Atomically add the three exact environment defaults only to V1.
7. Publish schema 2 metadata last and run strict current admission.
8. Bind the transition to `REQ-CI-DEV-011` and its Proofkit witnesses.

## 4. Required Witnesses

```text
fresh creation -> V2
exact V1 -> V2 with all old bytes preserved
metadata V1 + no environment + absent/V1/V2 secrets -> V2
V2 components + V1 metadata -> V2 without regenerated material
token publication temporary path -> state directory, never secrets directory
one-field V2 default or token mutation -> rejection without writes
second ensure -> byte-identical state
partial or foreign state -> rejection
```

The targeted test, lint, and type witnesses must pass before repository-wide
quality gates. The connected-stack witness must then prove that the migrated
state remains consumable by Compose and PostgreSQL.

## 5. Rollback

Before merge, revert the branch. After merge, code rollback may continue to
read only metadata V1 and would reject an already migrated V2 state; therefore
an application rollback must retain the V2 reader or explicitly reset local
development state. Production data is unaffected because this schema belongs
only to local developer state.

## 6. Completion Criteria

- all listed witnesses pass;
- Proofkit reports complete requirement bindings;
- documentation graph is reachable;
- no old credential bytes or volume identifiers change;
- the PR describes the observable upgrade and the explicit rollback limit.
