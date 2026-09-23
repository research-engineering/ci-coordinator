# Actions History Retention Implementation Plan

Status: proposed delivery plan; implementation pending

Design authority: [Actions history retention](actions-history-retention.md).
Program: [D4-H1](../../ROADMAP.md#d4-h1-historical-import-and-source-availability).

## 1. Ordered Delivery

Deliver with historical observation, not as a dormant policy field or a rewrite
of active evidence. The archive enumeration contract must first close attempt
population, provider caps, gaps, cancellation and resumability. Preserve the
current observation and source-time CI-evidence lifetime.

| Order | Deliverable                                      | Acceptance                                                                                                                                                               |
|-------|--------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1     | Policy and archive field/population contracts    | Each field has purpose, units/quality, privacy and lifetime; exact attempt/job identity, versioned cohort/category mapping and provider coverage semantics are explicit. |
| 2     | Pure archive/retention values and native oracles | Permanent summaries, total expiry and first-import identity have independent falsifiers.                                                                                 |
| 3     | Forward schema and transactional storage         | Unique contributions, scoped quota/CAS/cleanup, child-only expiry and migration attestation.                                                                             |
| 4     | Collector and maintenance integration            | Historical input populates statistics without claiming current evidence; recovery preserves counts and clocks.                                                           |
| 5     | API configuration and analytics                  | Authorization, requested/effective/applied policy, preview/apply, command replay, bounded workflow/job/category filters and hot activation.                              |
| 6     | UI and how-to                                    | Linter and other job trends survive detail expiry; population, classification, policy source, destructive effects and coverage are clear.                                |
| 7     | Independent review, CI and live qualification    | Exact-head native proof, migration/deploy and measured pilot remain separate evidence classes.                                                                           |

The dependency is substantive: no cleanup before retained-summary semantics;
no writer before durable identity/quota enforcement; no UI promise before its
API; no production claim before live resource and recovery evidence. Group
coherent rows in a feature batch, not one PR per field, test or file.

## 2. Ownership And File Routing

Reuse existing defining modules before adding these proposed files. Paths are
responsibility guidance, not a requirement for wrappers or one class per use case.

| Surface                                                         | Responsibility                                                                                                                   |
|-----------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------|
| `ci_economics/archive.py`                                       | Compact attempt/job contributions, cohort/category provenance and detail states, without SQL, acquired clocks or provider calls. |
| `ci_economics/archive_retention.py`                             | Strict policy algebra, resolution and expiry using explicit time inputs.                                                         |
| Capability-owned archive ports                                  | Bounded storage/read/cleanup outcomes using compatible existing transaction contracts.                                           |
| `persistence/_schema_ci_economics_archive.py`                   | Tables, constraints and indexes independent of evidence expiry.                                                                  |
| `persistence/ci_economics_archive_repository.py`                | SQL, quotas, lock order and CAS; no metric meaning or HTTP policy.                                                               |
| New forward Alembic revision                                    | Archive schema and runtime capability transition; published revisions stay unchanged.                                            |
| Existing observation application and GitHub adapters            | Authorized sequencing and bounded provider reads, with policy remaining in its capability.                                       |
| HTTP and frontend capability API modules                        | Shared boundary contract/OpenAPI projection, not duplicate business validation.                                                  |
| Repository Settings and economics views                         | Policy controls, truthful coverage/detail states and usable analytics.                                                           |
| Specifications, Proofkit routes and current architecture/how-to | Owned requirements, sensitive native witnesses and navigation.                                                                   |

Before editing each owner, bind concrete paths, delta, protected observations,
derived surfaces and its narrowest sufficient gate. Pydantic admits shape; it
does not prove database time, isolation, quota or effect authority. Add a file
only for a distinct contract or evidenced maintenance need.

## 3. Native Witness Obligations

Parameterize related boundary cases while retaining independent assertions.
Behavioral execution belongs in repository-owned GitHub Actions.

| Predicate                | Controls and counterexamples                                                                                                                                                                                            |
|--------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Policy algebra           | Valid variants and defaults; missing/extra/null/duplicate keys, bool-as-int, invalid duration and cross-field combinations.                                                                                             |
| Contribution identity    | Same attempt replay and conflict, next attempt, other scope; exactly-once aggregate contribution.                                                                                                                       |
| First detail import      | Failed fetch, rollback, summary-only record, first commit, replay and ambiguous-commit recovery.                                                                                                                        |
| Expiry                   | `K = E - epsilon`, `E`, `E + epsilon`; forever, never-imported, expired and invalid persisted variants.                                                                                                                 |
| Conservation             | Import attempt/job statistics and richer detail, expire/delete only detail, query unchanged job/category trends, rebuild aggregates, rescan without resurrection.                                                       |
| Classification and grain | Renamed/redefined jobs, matrix variants, unclassified/mixed jobs, explicit historical mapping revisions and overlapping filters; preserve observations and count each source contribution once per declared population. |
| Evidence independence    | Old archive remains queryable while current evidence is ineligible; cleanup cannot cascade across owners.                                                                                                               |
| Policy races             | Default/override and future/existing scope; applied row revision/deadline versus future effective policy; both lock orders for import/change/cleanup; stale preview and claim reject.                                   |
| Destructive batches      | Crash before/after commit, resume, cutoff exclusion, cancellation, policy revision changes and exact scope.                                                                                                             |
| Quota                    | Concurrent boundary writes, matrix-job and metadata growth, zero-delta replay, rollback, capacity pause/resume without eviction.                                                                                        |
| Privacy/authority        | Unauthenticated, stale, other-scope and removed-access requests; token-free audit/export and forbidden field rejection.                                                                                                 |
| Provider gaps            | 404/403, missing listing, removed YAML and unavailable attempts cannot erase statistics or prove completeness.                                                                                                          |
| Restore                  | Older backup cannot revive old write claims or bypass admitted deletion fences.                                                                                                                                         |
| UI                       | Inheritance, preview, conflict/retry, expired detail with preserved charts, unknown data, keyboard access and reduced motion.                                                                                           |

The PostgreSQL witness must exercise the full lifecycle through public adapters,
not only prebuilt terminal rows. Schema, route and fixture existence do not
replace behavioral falsifiers. Bind each atomic predicate to a native assertion
before claiming the feature complete.

## 4. Admission And Rollout

1. Freeze numeric page/byte/quota bounds, field classification and provider
   population semantics before implementation. Preserve unresolved evidence.
2. Add requirements and positive/falsification routes; use Proofkit for its
   admitted mechanics, keeping route admission separate from native execution.
3. Use one independent reviewer under `AGENTS.md`; repeat only for a material
   finding or an uncovered independent scope. Preserve prior failed evidence.
4. Run admitted static checks locally; behavior, migration, PostgreSQL and
   browser suites run in GitHub. Retain the independent PR158 HTTP-admission
   timing problem rather than claiming this archive change fixes it.
5. Publish additively and squash only fresh qualified work; inspect post-merge
   separately. Source/CI success does not establish release or deployment.
6. Apply a forward migration and image through admitted Swarm deployment.
   Keep archive collection disabled until its budgets and recovery behavior are
   qualified; existing observation remains available.
7. Use a separately authorized non-enforcing pilot without creating a PR, changing
   workflows or dispatching them. Verify retained history, replay, retention
   and UI through the deployed service.

Source gaps remain explicit even after traversal completes. Infinite capacity,
perfect durability, causal cost savings, result-reuse authority, a generic DSL
and optional chat are not implied by completing this feature.
