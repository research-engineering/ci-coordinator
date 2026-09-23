# Persistent CI Report Budgets: Implementation Plan

Status: implementation and native witnesses authored; static validation and
independent review in progress. Native, release and live qualification pending.

The [design](economics-budget-policies.md) owns rationale and semantics.
Deliver one coherent budget-administration and retained-signal PR, not a
separate PR per technical layer. Native execution remains GitHub-only under
the current repository and organization policies.

## 1. Freeze The Complete Contract

- [x] Rebind exact base/head, current report acceptance, cleanup, scope roles,
  operation replay, audit-pair and database capability/ACL owners.
- [x] Resolve the exact lock order and demonstrate no reverse edge in every
  affected report, policy, audit and cleanup path.
- [x] Define policy and signal wire records, canonical hashes, quotas, response
  algebra and query plans. Keep per-report semantics and explicit wildcards.
- [x] Register a new runtime requirement and UI requirement in the actual
  source inventories; do not infer their IDs from the previous count.
- [x] Build the operand-to-oracle matrix before adding implementation. Each
  conjunct needs a feasible isolated mutant and representative valid control.

## 2. Implement By Semantic Owner

All paths below are relative to the repository root. Proposed new filenames
must be reconciled with existing owners before creation; no size-only split.

| Owner and surface                                     | Change and preserved contract                                                                           |
|-------------------------------------------------------|---------------------------------------------------------------------------------------------------------|
| `backend/src/ci_coordinator/ci_economics/budget.py`   | Reuse threshold/value/outcome algebra; do not change ad-hoc behavior                                    |
| `ci_economics/budget_policy.py`                       | Exact selector, bounded policy identity/revision, configure command and invalid/conflict outcomes       |
| `ci_economics/budget_signal.py`                       | Immutable policy/report relation, derived evaluation and retained read models                           |
| `ci_economics/budget_ports.py`                        | Capability-owned configure/read contracts only; no copied app aliases                                   |
| `persistence/_schema_ci_economics_budgets.py`         | Policy and signal tables, exact identity/retention constraints and scoped indexes                       |
| `persistence/ci_economics_budget_repository.py`       | Bounded policy CAS, operation/audit pairing and scoped policy reads                                     |
| `persistence/ci_economics_budget_signals.py`          | Bounded signal insertion/read/retention projection with no provider I/O                                 |
| Existing report repository and economics UoW/adapters | Bind first report and complete signal set to one transaction; preserve replay and uncertainty           |
| Existing cleanup owner                                | Delete report and dependent signals atomically; preserve source tombstone lifecycle                     |
| `app/ci_economics_budgets.py`                         | Current configure/audit authorization before storage and exact returned identity admission              |
| HTTP budget contracts and scoped router               | Pydantic boundary, raw JSON admission, existing bulkhead/deadline/CSRF mapping                          |
| Runtime composition/dependencies                      | Wire existing transaction factories; no new maintenance queue or background service                     |
| Frontend economics API and `features/ciEconomics`     | Generated types, runtime admission, policy editor and related signal tab; no duplicate threshold policy |

Only the first path in each backend package is fully prefixed for readability;
all subsequent package paths remain under `backend/src/ci_coordinator`.
Keep policy editing and signal inspection separate UI responsibilities while
sharing existing scoped request/abort/error primitives.

These source changes are implemented. The native witness matrix below remains
an execution obligation, not a consequence of file existence or static checks.

## 3. Evolve Durable State Safely

- [x] Add forward revisions `0008` and `0009` after the current head; never edit
  an applied migration. The existing transition algebra requires separate
  retire and expand declarations, executed in one migration transaction as
  specified by the design. This is not an online intermediate deployment.
- [x] Extend capability attestation and exact runtime ACL contracts together.
- [x] Existing retained reports remain valid without signals. Only first
  reports accepted under the successor contract require signal completeness.
- [ ] Prove empty install, upgrade with retained reports, repeated migration,
  incompatible old-writer rejection and least-privilege access.
- [x] Define release/rollback ordering. No live image runs against a partially
  migrated or incompatible schema; preserve the dedicated database boundary.

## 4. Prove Behavior And Cost

| Scope          | Sensitive witnesses                                                                                                                                      |
|----------------|----------------------------------------------------------------------------------------------------------------------------------------------------------|
| Domain         | Every selector operand, missing/exact/wildcard runner, counter bounds, threshold equality, unavailable counter and failed command                        |
| Policy command | Create/update/disable, stale revision, duplicate/conflicting operation, revision overflow, quota including disabled entries                              |
| PostgreSQL     | Shared/exclusive lock barriers, concurrent policy edit/report insertion, duplicate report replay, crash rollback and uncertain commit                    |
| Retention      | Accepted report and signals, exact expiry boundary, report removal, no orphan, no extension and no re-evaluation under a newer policy                    |
| API            | Role/scope before access, raw duplicate/missing/extra/null fields, deadlines, returned identity, conflict and unavailable mapping                        |
| UI             | Real create/edit/disable journey, historical-version labels, no-report unknown, foreign/stale response rejection, retry and desktop/mobile accessibility |
| Cost           | Maximum policy set, bounded rows/body, retained query plans and report-ingress latency; no claim of production capacity from fixtures                    |

Use the existing isolated PostgreSQL fixture for zero and sixteen policies.
Record committed report-ingress durations with a monotonic clock, actual
signal-row storage and `EXPLAIN ANALYZE` of the adapter's captured read queries.
Keep setup outside the timed interval and publish the bounded observations in
native job logs. Do not force index use on a tiny fixture, impose an invented
hardware-independent latency threshold, or call fixture timings CPU savings.
This avoids a new benchmark runner; E1 still owns representative load and SLOs.
Two policy writers must compete under a real observed database lock, and a
lost post-commit acknowledgement must resolve through the original operation
or report identity without duplicate audit events or signals.

Use parameterized meaningful cases, shared immutable factories and actual
completion/lock barriers. Mutation witnesses must fail because the intended
predicate changed, not because test collection or fixture setup broke.

The first frozen review identified four bounded repairs before native CI:
editor Close/New/Refresh bypasses, an overstatement of SQL arithmetic authority,
missing mixed-selector persistence controls, and missing connected route
admission witnesses. Close them with held-response UI tests, an otherwise valid
corrupt-outcome row rejected by the decoder, exact mixed/zero-match signal sets,
and real middleware saturation/deadline/cancellation tests. The latter three
are documentation or proof gaps, not evidence of an existing runtime failure.
The primary review was partial; it does not establish exhaustive conformance.

## 5. Close Derived Surfaces Before Native Publication

- [x] Update requirements, Proofkit bindings/routes/index, architecture module
  and import ownership, docs graph, OpenAPI and generated browser contracts.
- [x] Update independently owned native inventories: model count/validator
  policy, requirement cardinality, route and strict-schema fixtures.
- [x] Keep current coverage floors and all existing test/mutation cohorts.
- [x] Synchronize canonical and packaged database profiles, the raw digest
  admission pin, exact inventory cardinality, schema exports/topology and
  OpenAPI generator provenance. The first native run exposed these omitted
  projections before migration execution; preserve the rejecting oracles.
- [x] Preserve schema-facade initialization, serialize database audit time
  through the current UTC-millisecond contract, and bind current-state drift
  probes and forward-only downgrade expectations to v3. Keep historical v2
  migration controls pinned to their original epoch.
- [ ] Run permitted static checks, freeze source, obtain the normal independent
  review under `AGENTS.md`, admit its exact result before further writes, then
  run the complete required native GitHub route.
- [ ] Repair confirmed findings additively; distinguish old review evidence
  from new source and new native results. No global-optimum claim.

The required connected-stack gate also exposed an existing witness phase-cut
gap: restored Vite HTTP behavior can precede the corresponding container
restart. Bind each configuration restoration to its changed runtime identity
using the existing bounded effect helper before starting the HMR-only phase.
The falsifier delays identity transition while exposing the restored response;
the next phase must not begin early. Keep the no-restart HMR assertion, native
Compose matrix and deadlines unchanged. This repairs evidence sequencing, not
the watch implementation or budget semantics; no new polling mechanism is needed.

## 6. Deliver And Preserve Remaining Work

- [ ] Squash only after exact native gates pass; verify postmerge and release.
- [ ] Admit forward migration, ACLs and immutable image on owner-approved development environment;
  verify private reads, policy changes and retention without neighbor changes.
- [ ] Update the current roadmap and measurement how-to; leave historical
  designs untouched. Report source, native, live and production boundaries.
- [ ] Keep cohorts, paired savings, full queue/cache/shard/retry statistics,
  external notification effects, pilot target authorization/pilots and E1-E3 open.
  Optional UI chat remains last.
