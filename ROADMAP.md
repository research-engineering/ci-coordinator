# CI Coordinator Roadmap

Status: sole task, priority and readiness register

Consolidated: 2026-09-23 against source `beb1abf8faa36c6629d82b7dbb8cee6b2a2b6015`.
This is a documentation/conservation checkpoint, not fresh runtime qualification.

## Authority And Use

This file is the complete current work queue. A task has one stable `CI-` ID,
one state, a deliverable, a closure condition and its actual prerequisites.
Feature designs, implementation recipes and audit dispositions explain its
contract and evidence; they do not own separate priorities or completion states.
Change the task here when a detailed document exposes a new remaining delta.
Do not copy its status into another plan or create one PR per task by default.

The product remains a GitHub App CI control plane, not a general infrastructure
manager or a central repository-command executor. Its canonical laws are in the
[meta-specification](docs/architecture/01-meta-specification.md): deterministic
proof may reduce work; advice may only increase validation; uncertainty runs
independent FullCI or produces an explicit failure. A local implementation,
passing CI, provider behavior and production admission are distinct facts.

Use [the documentation index](docs/INDEX.md) for contracts and execution recipes.
The [documentation authority contract](docs/architecture/cross-cutting/documentation-authority.md)
and native graph gate enforce the single `State:` owner and valid navigation.
The gate does not prove semantic completeness of arbitrary prose.

### Reading Task States

- **open**: an accepted outcome still needs a scoped implementation and/or
  qualification delta. Reuse implemented parts; do not rebuild a feature simply
  because its end-to-end acceptance is open.
- **qualify**: source mechanisms are recorded; obtain current native, provider
  or operational evidence as named. A discovered source defect becomes an
  explicit repair within that task, not a false passing qualification.
- **validate**: adjudicate hypotheses before accepting a code change. Historical
  counts, severity, LOC, library names and generic clauses are not defect proof.
- **conditional**: adopt only after the named value/authority decision; explicit
  rejection with a falsifier can close the decision without implementing it.
- **optional-last**: never blocks the required product, pilot or release work.

Closure requires the exact changed source, preserved hard constraints, the
task's native falsifiers and any separately owned external evidence. Record a
closed state and its evidence here; an old receipt cannot qualify a new epoch.
No meaningful completion percentage follows from counting these unequal tasks.

## Cohesive Delivery Batches

The eight batches group the existing eighteen directions, not eight mandatory
PRs or new services. Only `CI-` IDs below identify current tasks. Historical
repair B1-B5, delivery D1-D9 and external E1-E3 labels in retained references are
context, not additional queues or competing batch definitions.

| Batch | Outcome | Conserved original directions |
| --- | --- | --- |
| B1 | Own-CI observation, independent fallback and measured improvement | 13; relevant 7, 8, 15, 16 |
| B2 | Durable history, retention, recovery and administration audit | 1, 2, 3, 5; relevant 15 |
| B3 | API-first workflow adoption and usable administration | 6, 9, 11; relevant 4, 14 |
| B4 | Useful analytics, notifications and optional external bot integration | 7, 10; relevant 11 |
| B5 | Sound selective execution, reuse, consumers and controlled enforcement | 8, 16, 17; relevant 6, 13 |
| B6 | Draft/related PR, governance, credentials and environment lifecycle | 12 |
| B7 | Audit, architecture, security and operational/public-release qualification | 4, 14, 15; residual acceptance of 1-17 |
| B8 | Optional extensions; embedded chat last | 18 |

### Immediate Execution Order

Start with CI-001 and the minimum own-CI path. Pull forward the confirmed
workflow grammar CI-017, custom-epoch review CI-018 and plan TTL CI-055 only
where the exercised path requires them. Read-only observation does not wait
for every configuration, analytics or optional UI feature.

Prepare the authorized environment through CI-002/CI-062; then CI-003 observes
this repository without omission. CI-004 qualifies independent fallback.
CI-005 measures comparable execution using CI-027/CI-028; CI-006 optimizes the
measured bottleneck without weakening the independent baseline. Complete the
own-CI coverage comparison CI-057 before claiming complete self-optimization.

While external access or native CI is unavailable, finish independent B2-B6
source work and the applicable B7 repairs, never relabel the unavailable proof
as passed. Full production admission additionally needs CI-058/CI-059 and
CI-067-CI-069. CI-047 alone owns controlled omission after its complete external
conjunction. UI polish, optional advice and chat neither waive nor substitute
for those gates. Include related source, API/UI, documentation and falsifiers
in one outcome-oriented batch instead of serial layer-only PRs.

## 8. Next Work

The following register is the only list of remaining tasks. The acceptance
details after it refine these IDs; their numbered clauses are not extra tasks.
Prerequisites name hard closure dependencies, not a ban on independent design
or source work. A condition within a dependency is explicitly identified.

### B1: Own-CI Pilot And Measured Efficiency

State: open; previous private run and deployment receipts do not qualify this source.

| ID | Work And Closure | Depends On |
| --- | --- | --- |
| CI-001 | **qualify** the current repository execution environment, trusted base/head, native test inventory, required gates and baseline costs. Reobserve hosted/self-hosted runner availability; preserve every required outcome, coverage/mutation floor and secret boundary. No assumed former-account restriction or unapproved runner provisioning. | None |
| CI-002 | **qualify** an immutable release from exact master and its explicitly dispatched Full Check. Bind OCI digest, provenance, SBOM, registry ownership, signing/attestation and packaged build identity; source or local image is not a published artifact. Image admission does not require unrelated follow-ups or all of CI-054. [Release contract](docs/architecture/cross-cutting/release-artifact-publication.md). | CI-001; applicable required image-admission predicates from CI-060/CI-061 |
| CI-003 | **qualify** this repository as the first consenting non-enforcing consumer: inventory, discovery, real runs, webhook/durable reread, duplicates, errors and browser login/renewal/logout. Preserve independently runnable FullCI. Use the bounded environment's actual admission; do not wait for unrelated product features or claim production capacity. | CI-001, CI-002, CI-062; CI-017 for affected discovery |
| CI-004 | **qualify** target-local timeout fallback and stable gate under coordinator outage, unavailable reusable definition, invalid/stale plan, fork context, push/PR/merge-group and cancellation. Explicit failure is distinct from FullCI actually executed; required checks cannot turn missing work green. [Consumer laboratory](docs/features/consumer-contract-laboratory.md). | CI-001; live cases CI-003 |
| CI-005 | **qualify** paired full/selected or shadow experiments on exact source, runner/cache/profile/run/attempt cohorts. Record elapsed time, coordination overhead, real CPU or explicit estimate, negative savings and uncertainty. Do not inherit former five-minute or CPU-saving claims. | CI-003, CI-004, CI-027, CI-028; custom activation CI-018/CI-055 |
| CI-006 | **open** measured own-CI cost reduction: PostgreSQL preparation/query/cleanup, duplicated coverage runs, fixture lifetime, shards, mutation, Compose, Dev Container, browsers and tool startup. Preserve collected tests and independent oracles; five minutes per job is a design objective, not permission to omit work. [Efficiency contract](docs/features/proof-preserving-ci-efficiency.md). | CI-001; compare before/after, with CI-057 for complete coverage claim |

CI-001 qualification checkpoint (2026-09-23): hosted jobs execute in the new
private repository; no self-hosted runners are registered. The recovered PR's
[native run](https://github.com/research-engineering/ci-coordinator/actions/runs/35851411775)
exposed a stale exact job-inventory assertion and an unresolved nested-process
cancellation timeout. The assertion repair preserves the independent expected
set; bounded fixture diagnostics preserve the original timeout and cleanup
oracles. Both require fresh native execution after repair. Static checks are
not a substitute for this evidence.

On 2026-09-23 the owner authorized public source visibility to remove the
private-plan restrictions. GitHub now reports PUBLIC; anonymous repository
access, Dependency Review comparison and branch-rules API access are verified.
The history secret scan covered all 11 observed commits, including PR refs,
without findings. Public feature availability is not active branch protection,
a passed Dependency Review job, an attested release or production admission.
Do not skip required checks or replace provider enforcement with a local scan.

The successor native run at `82c2e68` also found the restored Qodana preflight
missing from the standalone shell-input owner and a PostgreSQL activity
diagnostic timeout. The shell repair extends the existing ShellCheck inventory
and its independent oracles, without a new scanner or exclusion. The database
timeout remains an investigation, not permission to increase its budget.

The first same-head serial qualification on public `9939a25`
([run 35932427988](https://github.com/research-engineering/ci-coordinator/actions/runs/35932427988))
completed the test loop but failed to write its bounded native report. One
log-overflow test's payload had become a 4,194,405-byte pytest ID, repeated in
the report. The targeted repair gives this parameter set short collection IDs
without changing the payload or oracle; the 32 MiB artifact bound remains.
At this snapshot CI-001 and CI-006 still required a successful fresh same-head
serial comparison and measured costs after that source change.

The subsequent exact-master [run 35940367926](https://github.com/research-engineering/ci-coordinator/actions/runs/35940367926)
showed that short IDs fixed collection size but did not qualify the optional
serial route: the process timed out after 2400 seconds at 83% of the same
12,603-node universe. All eleven ordinary shards and aggregate coverage passed;
their shard-session time totalled 2819.67 seconds and test phases totalled
2733.90 seconds. A separately bounded
serial-budget revision and fresh exact-head comparison are required. This is
not evidence that any missing serial tests passed or that PR-gate coverage was
weakened.

The bounded serial-budget repair was squash-merged at `640a463`. Its explicitly
dispatched [same-head serial run](https://github.com/research-engineering/ci-coordinator/actions/runs/35952421534)
passed 12,618 native nodes; the native comparison reported equal node and
terminal-outcome populations. Serial elapsed time was 3,014.97 seconds, while
the ordinary shards remained independent. On `0ed8dec`, an explicitly
dispatched [exact-master Full Check](https://github.com/research-engineering/ci-coordinator/actions/runs/35957325576)
passed. These receipts qualify that bounded source cohort, not every future
source epoch or a causal CPU-saving claim. CI-006 remains open for measured
proof-preserving cost reduction. CI-002 still requires the immutable package,
its attestation and its visibility/ownership admission; passing source CI is
not a published image.

CI-006 lifecycle follow-up keeps the full test population and existing command
ceilings while repairing cooperative Dev Container cleanup, bounded failure
diagnostics, shared monotonic phase budgets and the COPY fault-injection oracle.
The [efficiency plan](docs/features/proof-preserving-ci-efficiency-implementation-plan.md#9-cooperative-lifecycle-and-aggregate-budget-repair)
owns its acceptance. This scoped repair does not close the remaining cost,
cohort-rebalancing or deployment qualification work.

### B2: Durable Observation And Recovery

State: open; existing storage and worker slices require current qualification, not blind reimplementation.

| ID | Work And Closure | Depends On |
| --- | --- | --- |
| CI-007 | **open** all-available Actions-history discovery, not a recent-window substitute. Qualify capped/dense pagination, changing pages, bounded/resumable scope, checkpoint coverage, partial populations and honest gaps through API/UI. [Population contract](docs/features/actions-history-population.md). | Current authorized inventory; CI-014 budgets |
| CI-008 | **qualify** webhook-to-archive ingestion, bounded missed-event reconciliation and new attempts of old runs. Prove duplicate/replay/crash and independent recent/history lanes, without rereading unchanged history unnecessarily or assuming automatic GitHub redelivery. [Recent recovery](docs/features/actions-history-recent-recovery.md). | CI-007 for historical coverage; CI-014 |
| CI-009 | **qualify** stable partial attempts, jobs/steps, recorded-gap retry, concurrent reads and original-generation recovery. Preserve permanent statistics, immutable import clocks, quotas and source/detail independence. [Convergence](docs/features/history-review-convergence.md), [detail boundaries](docs/features/history-detail-boundary-closure.md). | CI-007, CI-008 |
| CI-010 | **open** complete retention administration: permanent compact job/attempt statistics; optional detail default 365 days from first successful import; global defaults, repository overrides and retroactive-shortening preview. Expiry cannot reset on rescan, double-count or extend 90-day CI evidence. [Retention](docs/features/actions-history-retention.md). | CI-009 |
| CI-011 | **open** explicitly authorized destructive erasure with scope, generation/fencing, deletion intent, backup/WAL/quarantine/key policy and restore reconciliation. A rescan is non-destructive. Preserve immutable deletion evidence and current authorization; data access loss is not an erasure instruction. | CI-010, CI-067 |
| CI-012 | **conditional** lazy workflow/run source-availability presentation and budgeted reconciliation where useful. Distinguish definition/run/attempt/job/artifact deletion, unavailable/denied/unknown and positive deletion proof; retained statistics stay readable. A 404 or missing page entry proves no deletion. | CI-009; demonstrated operator need |
| CI-013 | **open** repository lineage across reinstall/transfer, preserving exact historical workload identity separately from current installation authority. Adopt archival continuity only with explicit authorized mapping; never inherit grants or erase history automatically. | CI-009, CI-062 |
| CI-014 | **open** bounded, fair asynchronous ingress/worker processing across installations, repositories, recent/history work and foreground requests. Native controls cover bytes, CPU/RSS, connections, queue age, provider limits, cancellation, retry/poison and drain; durable acceptance precedes acknowledgement. Prefer the existing store/inbox unless measured requirements justify another broker. Live capacity qualification is separately owned by CI-069. | Current owner design and declared budgets |
| CI-015 | **open** the explicit [T9 policy decision](docs/adoption/temporal-and-oracle-audit-2026-09-06.md): may evidence acquired before logout create a later session? If forbidden, require a durable sid/subject revocation fence checked atomically on insertion; otherwise document the allowed residual window and falsifiers. Do not tighten policy silently. Qualify login, expiry/refresh, logout/revocation, bounded failure and [view continuity](docs/features/workspace-session-continuity.md) without replaying commands/drafts or widening authority. | CI-062 for deployed qualification; policy design can proceed independently |
| CI-016 | **open** complete security/administrator Activity coverage beyond the existing product slice: sensitive machine/CLI/read/export events, IdP correlation and actual log retention. Preserve atomic mutation evidence, unknown actors, attempted/committed/rejected/uncertain outcomes, bounded reads and privacy. [Activity](docs/features/administrator-activity-product.md). | CI-015; live storage/log custody CI-067/CI-068 |

### B3: API-First Adoption And Operator Experience

State: open; bring forward only the prerequisites of the chosen pilot path.

| ID | Work And Closure | Depends On |
| --- | --- | --- |
| CI-017 | **open** whole-workflow discovery including the admitted GitHub.com `$/` and `./` same-repository call forms, caller-commit resolution, missing/cyclic/deep/unknown edges and provider-profile boundaries. Present entrypoints and components, not underscore-based classification. [Adoption acceptance](docs/features/recovered-work-admission.md). | Current provider semantics; no inferred GHES support |
| CI-018 | **open** review of an already registered custom policy epoch, distinct from rediscovered proposals. Bind exact epoch, actor/reviewer, source/inventory, active baseline and one-use handoff through callback, registration, activation, replay and races. Never activate by direct database writes or drop rules/default-branch semantics. [Adoption acceptance](docs/features/recovered-work-admission.md). | CI-017 when its graph is needed; CI-055 before incompatible signing |
| CI-019 | **open** complete API-first configuration and equivalent UI journeys: import/export, files, validation, dry-run, semantic diff, registration, review, hot activation, rollback and bounded status. Separate visibility, observation and command authority; no hidden UI-only policy. [Configuration lifecycle](docs/features/api-first-configuration-lifecycle.md). | CI-018 for custom epochs |
| CI-020 | **open** deterministic workflow-adaptation recommendations under explicit support profiles, including already unmodified workflows. Explain unsupported/unknown semantics and minimal consumer changes; optional agent advice cannot invent safe coverage or modify a consumer without approval. [Universal adoption](docs/features/universal-workflow-adoption.md). | CI-017, CI-019 |
| CI-021 | **open** current installation/organization discovery qualification and O1 capability diagnostics: exact grants, webhook observation, reconciliation freshness/gaps, provider limits, trust keys and next action through API/UI. No mutable duplicate readiness flag; denial is not empty inventory or broken webhook proof. [O1](docs/features/ci-operator-capability-completion-plan.md#o1-capability-diagnostics). | CI-062 for live provider facts |
| CI-022 | **open** O2 reusable/action dependency passport with exact caller/callee/action/tool revisions, authorized reverse consumers and distinct observed usage. Moving tags cannot rewrite past resolution; incomplete graphs cannot report no consumers. [O2](docs/features/ci-operator-capability-completion-plan.md#o2-reusable-dependency-passport). | CI-017; no second parser |
| CI-023 | **qualify** task navigation, catalog/repository views, collapsible sidebar and user control, icon/text spacing, symmetric viewport padding and long/similar identities visible beside actions. Preserve drafts appropriately, stale-response barriers, keyboard focus and mobile return paths. [Operator navigation](docs/features/operator-navigation.md), [layout](docs/features/operator-console-layout.md). | CI-015 for renewed-session journeys |
| CI-024 | **open** polished repository/history scanning progress from actual lifecycle/checkpoints, with active scope, counters, gap/failure states, pause/resume/cancel and reduced motion. No percentage or ETA without a valid denominator; no presentation-owned success. | CI-007/CI-009 for historical population; CI-023 |
| CI-025 | **open** consistent visual system, accessible loading/empty/partial/stale/denied/error recovery and useful task-level UX. Verify real browser, screenshots and supported mobile/desktop scope; synthetic README imagery stays synthetic, including Bart Simpson. Charts belong to CI-032/CI-033, not an independent metrics system. | Current backend capabilities; CI-023 |
| CI-026 | **open** complete Diataxis tutorial/how-to/API/CLI/reference and architecture overview for real administrator journeys. Qualify Mermaid semantics, native rendering and actual GitHub display separately; no stale manual setup steps or additional task queue in documentation. | CI-019; follow implemented scenarios incrementally |

### B4: Analytics And External Collaboration

State: open; telemetry and advice never authorize omission or credential broadening.

| ID | Work And Closure | Depends On |
| --- | --- | --- |
| CI-027 | **open** complete logically useful measurement and provenance: actual CPU, runner occupancy, wall/queue/setup time, retry/shard/cache and collection coverage. Bind units, source, population, denominator, missing/reset counters and finite query/retention costs; workflow completion is observed via durable events plus provider reconciliation, not trusted arbitrary reports. | Existing collection; optional instrumentation admitted separately |
| CI-028 | **qualify** requested/admitted/completed route separation and exact completion-bound comparison contracts through native positive/negative cases. Preserve compatible source/runner/cache/window cohorts, negative/unknown results and distinct CPU/billing/estimates. CI-005 separately owns real paired experiments and measured saving claims. [Measured comparisons](docs/features/ci-economics-measured-comparisons.md). | CI-027 |
| CI-029 | **open** permanent job statistics and usable period/workflow/logical-job/purpose filters, including linter trends. Version bounded category mappings, preserve unknown/mixed jobs and definition/matrix/runner changes; overlaps cannot double-count duration. [Purpose configuration](docs/features/analytics-purpose-configuration.md). | CI-010, CI-027 |
| CI-030 | **open** useful cost/usage forecasts: qualify and reuse the existing [occupancy forecast](docs/features/archive-analytics-product.md), then separately admit and implement only the missing monetary/tariff/currency/quota delta. Preserve configurable periods, uncertainty and time-ordered backtests; suppress unsupported estimates and distinguish spend from causal savings or fixed-runner cost reduction. A delivered occupancy model does not prove tariff support. | CI-027, CI-029 |
| CI-031 | **open** calibrated gradual/abrupt duration-regression and budget signals: stable cohorts, effect/sample/persistence thresholds, hysteresis, deduplication and recovery. Distinguish code and runner hypotheses using matched inputs and independent controls; correlation alone is not diagnosis. | CI-027, CI-029 |
| CI-032 | **open** O3 organization/repository optimization overview, useful graphs and drill-down to contributing runs, filters, attention list and table equivalents. Expose source, uncertainty, coverage and denominators; bound queries/rendering and prevent cross-repository cohort mixing. [O3](docs/features/ci-operator-capability-completion-plan.md#o3-optimization-portfolio). | CI-027; CI-029/CI-030/CI-031 only for their respective views |
| CI-033 | **open** read-only pipeline visualization separating declared graph, selected plan and observed timeline. Expand reusable/matrix/retry nodes without duplicated totals, show unknown edges and provide keyboard/table equivalents. Choose HTML/SVG/library/canvas from measured need; do not render provider-supplied SVG/HTML or invent an editor. | CI-017, CI-022, CI-027 |
| CI-034 | **open** O4 bounded test-result acquisition and reliable test-history semantics, initially one needed native/JUnit format. Bind test/parameter/run/attempt/tool/environment identities, reject unsafe parsing, keep retries and missing coverage distinct, and prove any flake attribution before presenting it as a fact. [O4](docs/features/ci-operator-capability-completion-plan.md#o4-test-reliability). | CI-027, CI-010 |
| CI-035 | **open** O5 actionable durable subscriptions/notifications with scope, idempotent delivery, acknowledgement, retry/recovery, bounded cost and source freshness. Qualify actual delivery and history-specific alert fixtures; preserve service-level paging versus replica drilldown semantics. [O5](docs/features/ci-operator-capability-completion-plan.md#o5-actionable-notifications). | Existing signals; CI-031 for new regression signals |
| CI-036 | **open** optional external review-bot command and read API: any admitted block/depth, exact revision/registry, scoped identity, idempotency, rate limits and repository/economics evidence. Bot absence leaves normal CI unchanged; commands only increase checks and never widen credentials or bypass target ownership. | CI-019, CI-027; admitted command registry |
| CI-037 | **open** O6 source-bound analysis adapters and optional outgoing requests for informational assistance. Study the actual agent endpoint, opt in per scope, allowlist destinations, bound egress/context/time/results, redact secrets and prevent recursion. Advice is inert and stale-aware; execution uses CI-036. [O6](docs/features/ci-operator-capability-completion-plan.md#o6-external-analysis-adapters). | CI-036 for suggested execution, not read-only display |
| CI-038 | **open** truthful PR-visible check/evidence summaries and agent-readable reports. Match exact source/run/attempt, distinguish skipped/missing/cancelled/N-A, and admit provider-write authority separately. Before mutation, implement reserved/uncertain/reconciled-or-reissued outcomes with generation fencing and audit; read-only integration needs no dormant effect framework. | CI-027, CI-036; explicit write profile |

### B5: Sound Dynamic Execution And Enforcement

State: open; selected execution and production omission have separate admission.

| ID | Work And Closure | Depends On |
| --- | --- | --- |
| CI-039 | **open** pre-CI unsigned candidate preparation where measured benefit exceeds context-only preparation. Key every policy/input/graph operand, share bounded storage, re-admit current authority/overrides/capacity at consumption and fall back immediately on miss/expiry/failure. No fabricated run identity or duplicate cache. | CI-044; [preparation contract](docs/features/pre-ci-context-preparation.md) |
| CI-040 | **open** independently justified input/dependency closure for deploy builds, Compose rendering, scripts/generated inputs, tools/actions/data and transitive external revisions. A digest is not completeness proof; remove a real known-path edge as a negative control. Unknown or incomplete closure keeps FullCI. | CI-017, CI-022; current semantic owners |
| CI-041 | **open** sound result reuse bound to exact subject, epoch, task, tool/environment/input and admitted compatibility. Separate omission/reuse/execution proof and preserve independent fallback; no global receipt from another subject or lifetime extension by archive retention. | CI-040, CI-044, CI-045 |
| CI-042 | **open** eligible-runner adaptive shards under exact labels/groups/shared-pool capacity, setup cost and bounded scheduler overhead. Compare small cases with an exact oracle; LPT is a heuristic, not globally optimal. Preserve complete disjoint work and no inferred CPU from predicted duration. | CI-027, CI-040; current capacity contracts |
| CI-043 | **open** qualify thin clients and an optional reusable-block library with caller/callee trust, packaging, secrets/environment restrictions, final-gate identity and definition-resolution fallback. Before extraction, prove one portable block on two distinct synthetic profiles; keep consumer policy local and do not create external repositories without authorization. | CI-004, CI-017, CI-022, CI-040 |
| CI-044 | **open** independent planner/omission verification and declared adapter applicability. A replay sharing the same defect is not an independent oracle; unsupported topology remains full-only or rejected. Preserve fixed-coordinate protocol boundaries until a supported-profile successor is admitted. [Selective safety](docs/features/selective-planning-safety.md). | CI-001; current requirements and causal controls |
| CI-045 | **open** bounded structured decision provenance through planner, verifier, stored evidence and replay: repository/request/diff/config/compiled-effective policy/graph/decision/reasons. Mutating any independent coordinate must alter identity or reject; admit codec migration and fallback consistency explicitly. | CI-044; [adoption acceptance](docs/features/recovered-work-admission.md) |
| CI-046 | **qualify** additional explicitly consenting consumer archetypes after the own-CI pilot: application, extension and reporting workloads as needed. Obtain fresh App/source/workflow/profile/runner evidence and paired observations; no former private installation or consumer edit is authorized by this list. | CI-003, CI-004, CI-005; CI-043 for reusable profiles |
| CI-047 | **qualify** limited production omission through exact external receipts, non-vacuous shadow/paired evidence, independent target-authority relation, generation cutover and old authority/execution/replica drain. Verify explicit activation, rollback and kill switch. Unknown evidence never becomes readiness through a score. | CI-002, CI-004, CI-040-CI-046 for admitted scope; CI-059, CI-062, CI-067-CI-069 |

### B6: Repository And Environment Lifecycle

State: open; lifecycle policy does not override provider facts or required gates.

| ID | Work And Closure | Depends On |
| --- | --- | --- |
| CI-048 | **open** revision-bound draft stages beyond the provider draft flag, with explicit stage transitions, permissions and checks. Preserve provider draft state, branch protection and exact merge readiness; do not introduce bypasses disguised as workflow flexibility. | Existing governance/identity; [lifecycle design](docs/features/governance-drift-release-evidence.md) |
| CI-049 | **open** related/stacked PR dependency ordering, changed-base invalidation and independently admitted transitions. Make cycles, missing dependencies, stale evidence and downstream changes explicit without merging on another PR's receipt. | CI-048 |
| CI-050 | **open** continuous governance drift against approved durable baselines, current provider rules and exact comparison. Reuse observation/baseline/comparison source, qualify scheduling and operator recovery, and route meaningful notifications through CI-035. | CI-021; current governance owners |
| CI-051 | **open** least-privilege credential and incident-risk profiles for forks, privileged blocks and external effects. Deterministic AllPermissions is not least privilege; bind necessity, custody, revocation, uncertainty and explicit incident actions. | Current credential planes; CI-036/CI-038 for their extensions |
| CI-052 | **open** release/environment bindings and multi-environment ownership. One shared control plane may coordinate many environments; independent test coordinators need separate data, credentials and authority, not a label alone. Apply per-copy/full-row transfer safeguards only to actual authority transfers. | CI-002, CI-051; CI-065 for changed boundary contracts |
| CI-053 | **open** remaining local developer lifecycle and safe multi-worktree `dev:list`/`dev:prune`: dynamic/conflict-free resources, explicit ownership, dry-run, approved destruction and usable mise/dev-container commands. Preserve credentials/volumes across normal upgrades; add Make only for demonstrated value. | Current developer environment contracts |

### B7: Engineering And Operational Qualification

State: open; hypothesis validation, source repair and external qualification remain separate.

| ID | Work And Closure | Depends On |
| --- | --- | --- |
| CI-054 | **validate** all residual admitted audit findings, including the 180 unresolved predicate mappings from two retained legacy inputs. Match current owner and falsifier, merge duplicates and preserve scoped rejection triggers; do not recreate retired TypeScript. Regenerate current SARIF when needed; deleted report `3ed45d...` is retired-unadjudicated, not a passing review. [Audit acceptance](docs/features/assurance-audit-hardening-plan.md). | Exact current snapshot; no required reconstruction of deleted reports |
| CI-055 | **source repaired; qualify deployment compatibility**. Producer settings, signer and local verifier now admit only 1-300 seconds and exercise the actual target at 1/300/301. Before live selected execution, establish that the new environment contains no previously issued incompatible envelopes or admit an explicit migration/compatibility receipt. | Current signing/target owners; before affected selected execution |
| CI-056 | **validate** unjustified abstractions, ownership/co-location, test duplication and contract size. Check the unused `ReconciliationPublisher` against real consumers before removal; prefer existing Pydantic/FastAPI/standard-library capabilities where behavior is preserved. Metrics select review, not god-file verdicts; no indiscriminate DTO/repository classes or whole rewrite. | CI-054; exact invariants and protected observations |
| CI-057 | **validate** the current shared dynamic-CI blueprint against own-CI requirements and native consumed-input/evidence inventories. Freeze the candidate, disposition every applicable validation class, implement only real gaps, preserve independent baseline and profile coverage before self-optimization. Existing CF01/CF03 source repairs are not the entire comparison. | CI-001; [input coverage](docs/features/own-ci-input-coverage.md) |
| CI-058 | **qualify** PostgreSQL contracts and the 225-scenario intake on current source: coherent observations, total collection-state checks/forward migrations and native query-plan/cardinality/concurrency controls. Source repairs do not prove deployment workloads, maintenance or memory; those measurements belong to CI-067/CI-069. Tune only measured counterexamples. | CI-014 for changed concurrent paths |
| CI-059 | **qualify** project-specific FastAPI Production context/conformance against the exact selected sealed profile. Keep applicable, N/A and unknown distinct, include edge/proxy/abuse control, cancellation, serialization and capacity evidence; not implementing financial FAPI does not reject this profile. | [Profile owner](docs/architecture/cross-cutting/fastapi-production-assurance.md); CI-062, CI-068, CI-069 |
| CI-060 | **qualify** delivered userspace and minimal runtime image: bounded in-image pytest subset, exact Python/libc/native libraries, functional/performance/memory comparison and fast reproducible rebuild. Renew narrow digest/file/patch/test/expiry-bound security repairs; new unsupported High/Critical/Unknown stays blocking. Size is an outcome, not a speed/correctness waiver. | [Runtime qualification](docs/features/runtime-base-qualification.md); current source/security evidence |
| CI-061 | **open** residual proof-integrity qualification: actual analyzer/database snapshot, cost-hint provenance, complete library-copy inventory, admitted import root under optimization/path variations, consumed input versus assigned command, critical negative controls and final-artifact scanning. Preserve independent expected manifests, exact error causes and malformed/missing/truncated rejection. Only the applicable required image/admission subset blocks CI-002; closure of the whole legacy-audit task CI-054 is not a prerequisite. [Audit criteria](docs/features/assurance-audit-hardening-plan.md). | Current accepted source-bound predicates and native routes |
| CI-062 | **qualify** a freshly authorized non-enforcing environment for this repository: ingress/webhook, App/installations, least-privilege grants, OIDC/JWKS, Keycloak administrator login, database access, package/attestation trust and immutable task digest. Reconcile live versus saved stack before update/rollback. Bounded pilot admission is not the full operational qualification separately owned by CI-067-CI-069. | CI-002; administrator access |
| CI-063 | **qualify** current stable dependencies and pinned runtime/toolchain, including seven consumed Proofkit commands, malformed/unsupported inputs, platform locks and actual Pydantic/FastAPI use. Evidence of an update must preserve consumer behavior; no global SOTA from recency or library presence. | Current dependency profiles and policy; scoped native compatibility |
| CI-064 | **qualify** open-source privacy/licensing/security: neutral source/docs/fixtures/screenshots, Apache-2.0 and third-party obligations, secret scans, appropriate telemetry/data retention and absence of private artifacts in export. Historical identifiers in private recovery are not distributable assets. | [Portability contract](docs/features/open-source-portability.md); CI-010/CI-011 for data guarantees |
| CI-065 | **validate** boundary-specific contract/lineage/authority-epoch and browser-proof architecture candidates against local ownership and existing enforcement. Compare cheaper alternatives, per-copy durable conservation, full-row independent inventories and fenced uncertain effects only where applicable. Adopt real deltas with user-visible business choices, migrations and falsifiers. | [Contract comparison](#d9-c1-contract-and-testing-architecture-comparison); no foreign-project change |
| CI-066 | **open** useful bounded private diagnostics with source coordinates/build identity/correlation while excluding messages, arguments, locals, secrets and raw provider data. Distinguish same-type failures without log storms or unbounded labels; no new generic error framework merely for aesthetics. | Current diagnostic owner; redaction and distinguishability controls |
| CI-067 | **qualify** database platform, TLS/primary/ACL/schema and existing-data migration, explicit text semantics, safe provisioner credentials, backup/restore and independent integrity/access/RPO/RTO evidence. A CLI, fresh database or artificial downgrade is not restore proof. | Current DB platform baseline; admitted operational environment |
| CI-068 | **qualify** all-replica secret/signing/emergency-bearer rotation, custody, expiry/revocation, shutdown, bounded cleanup, rollout and compatible rollback. Live state and saved declarations must agree; no neighboring application data may be changed by qualification. | CI-062, CI-067 |
| CI-069 | **qualify** composed failure and capacity envelope: independent offered arrivals, COMMIT/ack response loss, crashes/cancellation/reclaim, saturated pools/queues, dependent-service outage, rolling deploy and recovery. Measure all outcomes, queue age, CPU/RSS and drain; eight developers/two repositories is a scenario, not a proven ceiling. | CI-014, CI-058, CI-066; authorized workload and independent state reads |
| CI-070 | **open** final release qualification of the owner-authorized public source, with current exact-source CI, package/release/deployment authority and explicit residual limits. Source visibility was authorized and enabled on 2026-09-23; it does not close this release task. Verify no private history, caches, external reports or former receipts escape. Do not waive unresolved release blockers with a score. | Required scoped product/qualification tasks, CI-064; excludes conditional/optional extensions |

### B8: Conditional Extensions And Chat Last

State: conditional; none of these items blocks the required product's independent operation.

| ID | Work And Closure | Depends On |
| --- | --- | --- |
| CI-071 | **conditional** typed DSL, central dispatch or an embedded model executor/evaluator only after demonstrated unmet demand and cheaper-alternative comparison. Reusable libraries do not require central dispatch. Qualify new failure/authority/credential boundaries or explicitly reject the extension with revision triggers. | Required deterministic product; no assumed new repository or service |
| CI-072 | **optional-last** UI configuration chat after the other agreed product, repair, pilot and qualification work. Prefer existing external assistance; allow explanations and schema-constrained drafts with explicit confirmation and deterministic admission, never model-owned mutation. | All required tasks; CI-037 where sufficient |

## Detailed Acceptance References

The sections below preserve source requirements that are not replaceable by a
short task title. They are contracts and falsifiers for the register, not a
second schedule. Historical PRs, dates and legacy B/D/E labels are context only;
their successful observations do not qualify the new source. Current task state
and order always come from the `CI-` rows above. A new obligation discovered here
must be assigned to an existing task or admitted explicitly into that register.

Core controls apply throughout: exact identity/freshness and replay; bounded
resources and deadlines; fail-closed unknowns; least privilege/fork isolation;
separate provider facts and policy stages; rollback before enforcement;
independent expectation and negative controls; no claimed speedup, optimality,
CPU savings or readiness from LOC, green counts or architectural names alone.

The [audit acceptance](docs/features/assurance-audit-hardening-plan.md) and
[adoption acceptance](docs/features/recovered-work-admission.md) retain exact
source findings, already-delivered repairs and counterexamples. Preserve their
clauses; do not replay completed private repair batches as new work.

### Webhook Concurrency And Asynchronous Processing

Task owners: CI-014 (source/native behavior) and CI-069 (operational qualification).

Required design and qualification, researched against authoritative Python,
FastAPI, AnyIO, PostgreSQL and GitHub contracts current on2026-09-12. Complete
the active archive batch first; settle this architecture before claiming that
the multi-repository pilot has reliable burst capacity. This extends B2/E1,
not the number of unrelated product phases.

The [runtime admission cost design](docs/features/runtime-admission-cost.md)
and [plan](docs/features/runtime-admission-cost-plan.md) address a measured
expected-row cross-products in the database authority query. Grant-map lookups
stay inside one statement; fresh ACL/schema admission and transaction fencing are
unchanged. Native plan evidence and post-deployment throughput measurements are
separate obligations, not a claim that burst capacity is already qualified.

1. Measure the complete arrival population: webhook deliveries per run/job,
   bursts across installations/repositories, payload distributions, retries,
   backfill, foreground API requests and provider limits. Derive explicit
   throughput, admission latency, queue-age, drain/recovery and memory budgets
   plus a justified safety margin; eight developers across two repositories
   is a minimum scenario, not a capacity ceiling or load-proof substitute.
2. Separate authenticated ingress, durable acceptance and processing as
   responsibilities. Compare an existing PostgreSQL-backed bounded inbox and
   worker pool against a separately operated queue/worker deployment; adopt
   an external broker only when its durability, ordering, throughput and
   operational costs are superior under measured requirements. Splitting
   responsibilities does not itself require more microservices.
3. Preserve signature/body/identity binding and exact duplicate semantics.
   A successful acceptance response requires durable admitted work, not a
   process-local background task. Model crash cuts before/after commit and
   acknowledgement, ambiguous commit, retry/poison recovery, stale workers,
   generation fencing, payload retention and guarded replay. Failed GitHub
   deliveries require an explicit recovery owner, not assumed redelivery.
4. Use structured concurrency, bounded task populations and appropriate
   process/thread isolation for measured CPU work. Bound queue bytes, decoded
   payload memory, connections, deadlines, cancellation and shutdown. Keep
   provider I/O outside database transactions; model all-replica limits and
   credential refresh as shared resources rather than independent infinities.
5. Design fair repository/installation scheduling, independent recent versus
   historical work, bounded retries with jitter, provider rate-limit handling
   and adaptive concurrency only where feedback is stable and measurable.
   A slow or abusive repository must not starve other repositories or UI/API.
6. Qualify normal and oversized bursts, duplicate/late/out-of-order events,
   unavailable PostgreSQL/GitHub, worker death, full queue, rolling deployment
   and recovery. Record accepted/rejected/recovered deliveries, durable gaps,
   per-scope queue age, latency distributions, CPU/RSS, database contention,
   provider cost and sustained drain capacity without weakening correctness.
7. Before implementation, freeze a design, alternatives, falsifiers and
   implementation plan with the current independent-review policy. Reuse
   admitted libraries and simple existing mechanisms where sufficient;
   reject both unearned infrastructure and unjustified simplicity. Separate
   native CI proof from authorized Swarm load/operation evidence.

### D4-H1: Historical Import And Source Availability

Task owners: CI-007-CI-013, with CI-029 for statistical categories.

The [recorded gap recovery design](docs/features/history-gap-recovery.md) and
[plan](docs/features/history-gap-recovery-plan.md) add explicit bounded retries
for recoverable historical gaps, preserving original observations and current
generation. UI/API admission, immutable-source replay and current retained-header
projection belong to the same delivery batch. Native transactional/browser
qualification and targeted live pilot target recovery remain acceptance work; this
does not close complete upstream history or the wider capacity/pilot program.

The [partial history design](docs/features/partial-history-capture.md) and
[plan](docs/features/partial-history-capture-plan.md) preserve stable completed
attempts with nonterminal children as honest partial statistics. Seen identity
and retained terminal facts are separate, so skipped jobs cannot hide duplicate
IDs or break pagination. Existing refinement preserves import clocks and quota;
active CI evidence and omission authority remain unchanged. Native qualification
and original-generation live recovery remain required.

The [history worker design](docs/features/history-worker-progress.md) and
[plan](docs/features/history-worker-progress-plan.md) replace cumulative archive
drains with independent work-conserving lanes, preserving initial reconciliation
and all-owner shutdown. This advances D1/D4/E1 throughput qualification without
changing durable queues, leases, access or retention. Native lifecycle proof and
same-generation deployed measurements remain required; the wider burst/fault,
complete-history and production-capacity obligations above remain open.

The [concurrent archive read design](docs/features/concurrent-archive-reads.md)
and [plan](docs/features/concurrent-archive-reads-plan.md) address the live
import/analytics availability counterexample and the report Refresh action.
REQ-CI-RUNTIME-052 now binds a coherent statement snapshot rather than requiring
no concurrent dataset writes. Historical designs retain their original bytes.
This does not change metric meaning, access, retention or omission authority;
native and deployed qualification remain separate acceptance steps.

A synthetic pagination counterexample uses a numeric repository alias in the
next-page Link for a named repository request. The bounded
[pagination identity correction](docs/features/github-pagination-identity.md)
and [plan](docs/features/github-pagination-identity-plan.md) preserve exact
resource/query binding while admitting only the independently verified alias.
A future admitted pilot must preserve its cursor and quotas during recovery.
No former live pilot or executed synthetic run is claimed.

The [analytics purpose settings increment](docs/features/analytics-purpose-configuration.md)
adds durable, generation-fenced category configuration through API and UI, so
explicit lint/test/build categories can participate in descriptive analytics
without a process restart. Its [qualification plan](docs/features/analytics-purpose-configuration-plan.md)
retains exact-name admission and unknown/mixed populations. Source integration
is not deployment or native acceptance; automatic classification, complete
workflow provenance and measured savings remain separate open work.

Planned design and adjudication, followed by justified implementation, after the
current bounded observation journey and before claiming a complete historical
analytics product. D4 owns retained facts and reconciliation; D5/D7 own API/UI
operation parity. This does not expand the current seven-day source-admission
window, 90-day evidence lifetime or collection quota without a new contract.

Retention successor: [design](docs/features/actions-history-retention.md) and
[implementation plan](docs/features/actions-history-retention-implementation-plan.md).
These define the requested policy, not a delivered archive or runtime setting.

Current implementation follows [population](docs/features/actions-history-population.md),
[storage](docs/features/actions-history-storage.md) and the
[delivery sequence](docs/features/actions-history-population-implementation-plan.md).
Traversal, permanent statistics, guarded storage, independent collection lanes,
bounded detail expiry and atomic incremental delivery are implemented for
qualification. The [administration boundary](docs/features/actions-history-administration.md)
adds authenticated configuration/status APIs and the History view with explicit
range, workflow selection, pause/rescan, retention inheritance/override, quotas
and recorded progress. Fresh native and live qualification remain separate.
D4-H1 stays open for automatic all-available population discovery, recent
missed-event recovery, archive record/analytics queries, global defaults,
retroactive-policy preview and guarded erasure controls. Source implementation
does not authorize live activation or claim complete upstream history.

1. Distinguish workflow definition, workflow run, run attempt, job and logs or
   artifacts. Their deletion and availability are independent; removing a YAML
   file does not establish deletion of historical runs. Preserve exact numeric
   identities and separate the historical display name/path from current state.
2. Support an explicit **all available Actions history** initial import, as
   well as narrower repository/workflow/date selections, then incremental
   ingestion with bounded reconciliation. This is an accepted product goal,
   not an optional seven-day-only interpretation. Define whether older
   attempts and job details are obtainable, which facts merit storage, and
   class-specific retention, cost, privacy and schema policy. Compact attempt
   and job statistics, including supported cohort/category provenance, must
   remain permanent by default. Richer detail defaults to 365 days from its first
   successful import, with explicit service defaults and repository overrides.
   Design policy changes through API/UI, shortening previews, immutable import
   anchors, expiry without lost statistics, capacity pauses and deletion guards.
   Repeated scans must neither renew detail lifetime nor double-count attempts
   or jobs. Job-statistic collection remains independent of optional detail.
   Historical inventory must
   not silently become currently admissible CI evidence or extend its lifetime.
3. Do not assume one traversal is an atomic or complete historical snapshot.
   Check pagination/search caps, overlap, checkpoints, missing ranges, changing
   pages, nonterminal runs, webhook loss and new attempts of old runs. A
   creation-time watermark alone cannot discover every later rerun. Avoid
   repeatedly scanning unchanged history without an identified recovery need.
4. Evaluate lazy source-availability checks when an operator opens or refreshes
   retained evidence, plus a separately budgeted reconciliation policy. Record
   observed time and reason; distinguish unavailable, access-denied/unknown and
   positively confirmed deletion. `404` or absence from one page is not a
   deletion proof. Require exact authorized scope and sufficient provider
   evidence before showing a definitive deleted label. Recheck whether the
   label provides operator value before implementing it.
5. Provide a full-rescan proposal with scope/date selection, cost bounds,
   durable progress, cancellation, resumability and idempotent updates. It
   reconciles accessible data rather than clearing the local archive. Cache or
   conditional requests are used only where provider semantics preserve these
   properties; a rescan cannot retrieve data no longer available upstream.
6. Keep saved statistics and provenance readable under current authorization
   and local retention even when the source link is unavailable. Do not replace
   missing measurements with zero, invent deletion times or silently purge
   history because access changed. Show collection coverage and unresolved gaps.
7. Design workflow-version provenance for historical comparisons and diagnosis.
   Preserve provider workflow identity/path, exact run/attempt and executed
   source bindings where they can be established; never substitute today's
   default-branch YAML or assume the workload SHA proves every called workflow's
   version. Evaluate a content-addressed definition plus exact reusable-call
   bindings rather than duplicating full YAML for every run. Distinguish
   observed, reconstructed and unknown provenance. Compare storage/privacy cost
   with the benefit of version cohorts, before/after regressions and explanation
   of changed job topology. This metadata alone cannot authorize result reuse.

Acceptance includes replay without duplicate counts, late completion and old-run
reruns, disappearance versus lost permission, interrupted/resumed import, capped
searches, rescan without destructive reset and local-retention expiry. Choose the
smallest storage extension supported by these cases, not a speculative mirror
of all GitHub objects. Missing deletion evidence remains unknown, not an error.
GitHub documents [search limits and separate run/attempt operations](https://docs.github.com/en/rest/actions/workflow-runs)
and [authentication-related 404 responses](https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api#404-not-found-for-an-existing-resource).

### D4/D7-S1: Scanning Progress

Task owner: CI-024; history denominators are supplied by CI-007/CI-009.

Design and implement a polished scan animation for repository discovery and
Actions-history import. Its state must follow retained or current operation
evidence: queued, scanning, paused, retrying, interrupted, completed with gaps
or completed within the admitted scope. Show useful counters, active scope and
freshness; show a percentage only when a stable denominator is established.
Unknown totals require indeterminate motion, not an invented ETA or completion.
The view must support cancellation/resume where the owning operation does,
reduced motion, screen-reader status and bounded rendering. Add richer progress
after the current observation journey; D4-H1 must expose import checkpoints
before the UI can claim all-history progress. Animation is presentation, never
an independent lifecycle authority.

### D4/D7-V1: Dashboard And Pipeline Views

Task owners: CI-025, CI-032 and CI-033; no separate visualization backlog.

The [analytics exploration increment](docs/features/analytics-exploration.md)
and [delivery plan](docs/features/analytics-exploration-plan.md) group visible
sparse measurements, selectable usage/queue/outcome graphs and source-run
inspection into one UI batch. Existing forecasts and metric meanings are
reused, not reimplemented. Refresh the README screenshot with the synthetic
name Bart Simpson through its native browser fixture, without changing real
administrator identities. Native and deployed qualification remain separate.

Planned design and adjudication, followed by justified implementation. Extend
the existing analytics and workflow-navigation work rather than adding a second
metrics subsystem. This is not a prerequisite for the current observation batch.

1. Bind each view to an administrator decision: which repositories need
   attention, where execution or queue time is spent, what regressed, and why
   a particular pipeline was selected or executed. Evaluate an overview page
   with repository drill-down, filters and linked tabs instead of another long
   page. Choose charts only where they improve comparison over a table.
2. Define each metric's owner, source, unit, cohort, denominator, aggregation,
   retention and freshness before drawing it. Candidates include run/attempt
   counts, failures and cancellations, queue and elapsed distributions, runner
   occupancy, retries, collection gaps, budgets and regression signals. Actual
   CPU and paired savings require their own measurements; neither is inferred
   from elapsed time. Expose sample size and incomplete coverage; missing data
   is not zero. Comparison windows must use compatible populations.
3. Separate three possible pipeline views: declared dependencies at an exact
   source revision, the coordinator's selected plan, and an observed run/attempt
   timeline. A YAML call graph is not a runtime trace or proof of an execution
   critical path. Show unknown/dynamic edges explicitly. Expand reusable
   workflows and matrix jobs on demand while preserving their identities;
   shared components and retries must not duplicate totals. Link visible nodes
   to the relevant source, planning reason or retained run evidence.
4. Compare semantic HTML with focused inline SVG, an admitted graph/layout
   library and canvas using representative small and large pipelines. Add a
   separate renderer only if measured interaction/layout needs justify it;
   an editable workflow designer is not implied by a read-only visualization.
   Bound node/edge counts and rendering cost, disclose collapsed/truncated
   content, support keyboard navigation and a textual/table equivalent, and
   respect reduced motion. Do not render provider-controlled raw SVG or HTML.
5. Deliver in dependency order: metric/query contracts and bounded API reads,
   useful summary/drill-down views on available evidence, then a read-only graph
   or timeline where it answers a demonstrated question. D4-H1 can later widen
   supported historical coverage; it must not delay a truthful bounded overview
   or be silently implied by one. Keep charts and graph presentation outside
   planning and execution authority.

Before implementation, record the chosen operator questions, visual hierarchy,
data and interaction contracts, alternatives, cost and falsifiers in a focused
design and plan. Acceptance covers empty/partial/stale/denied states, exact
scope and revision changes, reruns without double counting, responsive layout,
accessible keyboard/table journeys and bounded large-graph behavior. Reconsider
the renderer or chart when a simpler representation answers the same question
with lower operational and accessibility cost. No global UI optimum is claimed.

### D4/D7-F1: Forecasts And Degradation Attribution

Task owners: CI-027-CI-031; presentation consumes those contracts through CI-032.

Planned design and validation before justified implementation. Extend D4's
measurement and budget owners and D7's analytics views; do not create a second
forecasting or alert subsystem. The decision is whether an administrator can
use the result to budget capacity, prioritize an optimization or investigate a
regression, not whether another attractive chart can be drawn.

Support period, workflow, logical-job and purpose-category selections, including
the requested linter trend. Retain compact job statistics before adding these
views: attempt totals cannot reconstruct them after detail expiry. Categories
such as lint, typecheck, test, build and deploy use bounded, versioned
repository mappings with provenance; unknown and mixed jobs remain visible.
Preserve exact execution identities separately from cross-run cohorts and
display names. Category overlap cannot double-count a job or turn a mixed
job's duration into lint-only time. Show definition/matrix/runner changes and
the applied classification rather than silently mixing incomparable series.

1. Evaluate per-repository forecasts with configurable lookback and future
   horizon, such as the next month. Separate observed runner-minutes, forecast
   runner-minutes, monetary estimates under an explicit versioned tariff, and
   counterfactual savings under an admitted comparable baseline. Runner
   occupancy is not CPU utilization or necessarily billable usage; saved
   minutes do not necessarily reduce fixed self-hosted costs. Display currency,
   pricing assumptions, known discounts/quotas and excluded costs where relevant.
2. Compare the simplest historical-rate or seasonal baseline before admitting
   a more complex model. Use rolling time-ordered backtests, minimum usable
   sample/coverage criteria, prediction intervals and calibration/error measures.
   Handle release bursts, workload mix, censored or partial runs, new workflows,
   incomplete imports and structural changes. Suppress an unsupported estimate
   rather than present a precise number or extrapolate missing values as zero.
3. Assess gradual and abrupt job-duration degradation separately from queue,
   setup and service-overhead time. Compare stable job/workflow and measurement
   cohorts; include source/input changes, test count, shards, cache state,
   runner class/image, dependencies, concurrency and external services where
   observable. Keep a minimum effect size, sample support, persistence window,
   configurable thresholds, hysteresis, deduplication and recovery semantics.
4. Treat code regression and runner degradation as competing hypotheses.
   Unchanged application code alone does not fix all other inputs. Use matched
   workload/source cohorts and independent runner observations or control jobs
   when available. Label the result observed slowdown, suspected contributor or
   unclassified unless evidence supports stronger attribution. Never identify a
   specific root cause solely from correlated charts.
5. Design actual-versus-forecast views with a clear cutoff, uncertainty band,
   baseline/scenario comparison and a link to contributing runs. Separate
   calendar-period billing, cumulative consumption and duration distributions.
   Support a compact table, accessible colors, responsive layouts and drill-down.
   Highlight actionable budget risk or sustained regression rather than every
   fluctuation; estimates remain informational and cannot authorize CI omission.
6. Order delivery after trustworthy metric definitions, archive coverage and
   query budgets: baseline evaluation, bounded API/model output, useful charts,
   then calibrated alerts. Record model/definition/tariff versions and exact
   input window in every result. Revisit or retire a model/view if it performs
   no better than the simple baseline, miscalibrates uncertainty, produces
   excessive false alerts or does not change an administrator decision.

Acceptance requires time-ordered out-of-sample evaluation, missing/sparse data,
changing workflow/runner cohorts, known regressions and non-regression controls,
threshold-boundary/recovery cases, bounded query/compute cost and a browser
journey from signal to evidence. Do not claim reliable forecasts, measured
savings, or causal runner diagnosis before these obligations are discharged.

### Administrator Activity And Authentication Audit

Task owners: CI-015 and CI-016, with CI-067/CI-068 for external retention/custody.

The 2026-09-09 source check found durable actor/time evidence for selected
business mutations in the existing PostgreSQL audit ledger, but no complete
administrator activity history. Browser login writes token-free session state;
logout/expiry delete that state. It is not a retained login/logout journal.
Structured application logs go to the process stream; their deployment-level
collection/retention and Keycloak realm-event persistence are not yet qualified.

Add this work to B2/D1 security lifecycle and B4/D7 operator journeys, before
E1 qualification and wider pilot use. Reuse the existing audit mechanism where
its semantics fit; do not equate a request log with a committed business action.

- Inventory every security-relevant login, logout, expiry/revocation, role
  denial and administrator mutation across UI/API/CLI. Define which reads and
  exports need audit by sensitivity; do not record every page render blindly.
- Bind verified actor/issuer/subject, action, exact repository/organization,
  operation/correlation identity, outcome, trusted time and relevant revisions.
  Failed unauthenticated attempts have unknown actor, never a caller-asserted
  identity. Distinguish attempted, committed, rejected and uncertain operations.
- Keep mutation evidence atomic with its state change, and make retry/replay
  behavior explicit. Decide fail-closed requirements by event class; bounded
  failed-login telemetry must not become a storage-amplification attack.
- Provide an administrator Activity view and equivalent bounded, filtered,
  paginated read/export APIs. Define retention, access, integrity verification,
  deletion/privacy, cardinality and cost. Preserve existing replay contracts.
- Exclude passwords, OAuth codes/state, bearer tokens, cookies and raw sensitive
  request bodies. Treat IP/device fields as a separately justified privacy
  decision, not a mandatory collection default.
- Qualify actual Swarm log export/rotation and Keycloak login/admin-event
  configuration; bind IdP events to application sessions without assuming that
  IdP login implies successful Coordinator session admission. Prove success,
  failure, expiry, replay, cancellation and persistence-unavailable paths.

This is planned coverage expansion, not a claim that all existing operations
are unaudited or that deployment log retention has been inspected.

The [Activity product](docs/features/administrator-activity-product.md) and
[operator workspace](docs/features/administrator-activity-workspace.md) implement
the bounded source slice: atomic session/security records, selected business
references, schema/ACL admission, scheduled cleanup, issuer/repository APIs,
source tabs, filters, pagination and explicit page export. Native integrated
qualification and immutable deployment remain acceptance gates. The finite
hook inventory explicitly retains external machine/CLI/IdP diagnostics and
Swarm log retention; this slice does not close the whole security workstream.

### External Advisory Assistance

Task owners: CI-036-CI-038; embedded chat remains CI-072.

After the D6 scoped command/read contracts, design the reverse direction:
Coordinator can request help from an operator-configured external review
bot/agent through a protected internal API and receive an informational
recommendation. Prefer reusing that agent over embedding a second agent
runtime. Internal network placement is not authentication or authorization.

Require explicit per-repository opt-in, independently scoped service identity,
an operator-owned destination allowlist, TLS, restricted egress, no arbitrary
URL or redirect following, and minimum authorized context with secret
redaction. Bind request and response to repository, exact subject/revision,
question and correlation identity; bound request size, concurrency, time,
response size, retention and audit cost. A result is untrusted advice, displayed
as inert content with provenance and staleness, never executable instructions
or evidence that CI passed. Failed, unavailable or absent assistance leaves
deterministic planning and FullCI fallback unchanged. Any suggested execution
still enters the separately authorized command path. Prevent recursive
Coordinator/bot invocation and duplicate retry effects.

The integration design must decide user-triggered versus explicitly governed
automatic requests, approved context fields, synchronous versus asynchronous
delivery and retention based on actual agent API contracts. Do not invent
compatibility before studying that endpoint. This optional integration can
supply a future UI assistance surface; embedded UI chat stays last and may be
unnecessary if the external service satisfies its requirements.

### D9-C1: Contract And Testing Architecture Comparison

Task owners: CI-056 and CI-065; adopted deltas enter the existing capability task.

Study candidate contract and testing improvements against Coordinator-owned
invariants. This extends D9/B5 and informs D2 contract/effect work, D5 API
boundaries and D7 browser proof. Preserve the current observation work and do
not block a bounded non-enforcing pilot on unrelated unproven enhancements.

The implementation target is CI Coordinator. Each adopted improvement needs
local design, code, contracts and native verification. A foreign proposal is
candidate methodology, never approval or a prerequisite to independently
justified local work. No consumer-repository modification is implied.

Use the current local architecture, contract and browser-proof owners as
comparison entrypoints. Former private ADR references are not exported.


Former private source-routing and PR-state observations are revoked for this
export. Before a new comparison, freeze current local base/head, the admitted
external candidate (if authorized), owner evidence and acceptance predicates.

1. Build a bounded comparison ledger: candidate invariant, protected behavior,
   current Coordinator semantic owner, existing enforcement/native witness,
   concrete counterexample or unresolved question, cheaper alternative,
   implementation/proof cost and disposition. Use `already covered`, `adopt`,
   `bounded pilot`, `defer`, `not applicable` or `unresolved`, with evidence and
   revision triggers. A foreign ADR or framework's availability is not proof
   that a new registry, layer or dependency is needed.
2. Compare boundary-specific ownership across Pydantic/FastAPI, generated
   OpenAPI, frontend validation, durable payloads, compatibility capabilities,
   audit records and provider effects. Distinguish authored semantic authority,
   generated projections and observed behavior. Check stable identity versus
   semantic revision, runtime use and authority epoch against current exact
   source/configuration/lease bindings; reuse sufficient existing machinery.
3. Evaluate transfer safeguards only where the operation exists: per-copy
   preservation for live data, WAL, backups, quarantine and key bindings during
   durable-authority transfer; reserved/uncertain/reconciled-or-reissued effects
   with generation fencing for remote mutations; independent registration and
   observation inventories comparing full relation rows during owner transfer.
   Do not add dormant protocols to read-only ingestion, or treat these three
   candidates as a complete proof of transfer safety.
4. Compare candidate testing guidance with current native/browser/visual/accessibility/security
   witnesses, fixture ownership, exact-artifact binding, test isolation, flake
   diagnosis and CI cost. Separate deterministic acceptance from agents that
   propose tests or evaluate advisory behavior. Reuse admitted libraries and
   Proofkit routes where they preserve the actual oracle; generated tests or
   agent approval alone cannot prove correctness or replace a native gate.
5. Record each adopted behavioral delta explicitly for the user, with a scoped
   design/implementation plan, independent causal counterexamples, migration
   and rollback constraints, and exact-head qualification. Route business
   choices through the existing review-decision register, with falsifiers and
   revalidation triggers, never a permanent reviewer exemption.

Acceptance is a source-bound comparison and disposition of every admitted
candidate, followed by proof of adopted changes. Share the own-CI evidence
inventory below instead of duplicating its audit. Neither retaining the
current architecture nor adopting external proposals proves global SOTA;
currentness claims require relevant stable-component/source evidence and an
explicit comparison scope. Preserve unresolved and deployment-only obligations.

### Own-CI Coverage And Measured Self-Optimization

Task owners: CI-001-CI-006 and CI-057; enforcement remains CI-047.

The current external blueprint is frozen at
`a5c4d775207a6de30ab392bbd7ed9c70c427f596a5d5c849889dd6e83d05556f`.
Its CF01/CF03 gaps are implemented by the bounded
[input-coverage design](docs/features/own-ci-input-coverage.md) and
[plan](docs/features/own-ci-input-coverage-plan.md): standalone ShellCheck and
explicit consumer workflow syntax coverage reuse the existing pinned image
and command. Further security scanners, connected-boundary properties,
profile mapping, shadow measurement and operational omission remain open;
this slice is not completion of the blueprint or self-optimization.

Before D9/B5 closes own-CI validation, re-read and freeze the current external
`dynamic-ci-review-architecture-blueprint.md`. Its historical location is not a
checkout requirement; the private companion catalog records the input path and
digest. Keep accepted predicates in repository-owned plans without publishing
private historical payloads. Treat it as candidate methodology, not canonical
project authority.
Compare its applicable validation classes with current requirements, source
risks and native CI witnesses. Record each class as covered, a confirmed gap,
not applicable with rationale, or unresolved; include proof limits and cost.
Implement justified gaps using existing admitted tools where sufficient.
Neither a large check inventory nor green CI proves universal correctness.

The [synthetic page closure correction](docs/features/synthetic-page-closure.md)
and [plan](docs/features/synthetic-page-closure-plan.md) close an observed
browser-harness acknowledgment-without-effect gap using bounded confirmation
and owned-context containment. Native browser qualification remains required;
this development-only repair does not establish production confinement.

After that independent baseline and the D2/D4/E1 prerequisites, include this
repository in E2 as a consumer of its own deployed coordinator. First use
shadow planning and paired full/selected runs, measure CPU or explicitly
labelled estimates, elapsed time and coordination overhead, then consider
bounded omission only through E3 admission. Preserve an independently runnable
Full Check/fallback and trusted base/head policy: a proposed coordinator or
workflow change cannot approve its own weakened validation. Unknown coverage,
authority, unavailable planning or contradictory evidence must not produce a
green result by omission. This work precedes the final optional UI chat.

### Administrator and deployment gates

Task owners: CI-062/CI-067-CI-069 for environment qualification, CI-003/CI-046
for pilots and CI-047 for enforcement. E1-E3 are evidence classes, not extra tasks.

Former private ingress, signed deliveries, login sessions, App installations,
allowlists, database observations and deployment receipts are not exported.
None qualifies the new public source or authorizes a synthetic pilot.

E1 must obtain fresh administrator-owned ingress, exact App/installation and
repository bindings, signing/secret custody, recoverable login and an admitted
immutable release. E2 must then bind independent native FullCI and measured
non-enforcing observation to an explicitly consenting target.

Retain the [runner-reference boundary correction](docs/features/github-runner-observation.md)
and its [plan](docs/features/github-runner-observation-implementation-plan.md)
as source obligations. Requalify capture, duplicate registration, source
retention and recovery without fabricating live success. Job elapsed time is
not CPU usage or saved compute. Pilot UX follow-ups remain: workflow names and
status, and validated view recovery after session renewal.

The next E2 step is a bounded non-enforcing observation pilot, not omission
activation and not a prerequisite to finishing every optional roadmap item.
Require exact App/repository admission, live webhook and workflow reads,
recoverable operator authentication, repository-isolated persisted observations
and unchanged independently runnable FullCI. Exercise inventory, discovery,
actual run observation, durable reread, duplicate deliveries and failure states.
Use observed integration and usability gaps to prioritize further work.
Selective execution and savings claims additionally require D2 coverage and
fallback closure, paired measurements and the relevant E3 authority gates.
Optional assistance and unrelated UI polish cannot substitute for these
prerequisites and need not delay the observation pilot.

PR194 repairs the reported Mermaid source failures and adds semantic/rendering
qualification for the complete documentation inventory. Preserve graph semantics;
the pinned renderer's native success is separate from actual GitHub rendering
and architectural truth. Retain the latter observations in D7 acceptance.

| Stage                                  | Required external result                                                                                                                                                                                                                                                                                            |
|----------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| E1: Qualified non-enforcing deployment | Administrator-provided webhook/ingress and App installations/permissions; exact immutable Swarm artifact; PostgreSQL 18.6 with migrations, ACLs, TLS/primary policy and restore; Keycloak admin access; signing/secret custody and rotation; aggregate admission, alerts, capacity, shutdown and rollback receipts. |
| E2: Measured repository pilots         | pilot target signed FullCI, representative shadow and paired selected/full runs with source/runner/cache/attempt identities; native gate retained; failure and rollback cases. Extend to extension-pilot and reporting-pilot with authorization.                                                                          |
| E3: Controlled enforcement             | Non-vacuous matched evidence, successor production admission and generation cutover, drained old authority/executions/replicas, explicit activation, limited omission scope and kill switch.                                                                                                                        |

One deployed control plane may share a database across replicas and client
environments. An independent test coordinator needs separate data, credentials
and authority: an environment label alone is not isolation. Durable-copy
conservation and independently enumerated full-row inventories apply on actual
authority transfer, not speculatively to every ordinary configuration edit.

### Active Delivery: Automatic Inventory And CI Efficiency

Task owners: CI-021 and CI-006. The following source references do not identify
a second current batch or carry forward previous qualification.

The [inventory design](docs/features/automatic-app-inventory.md) and its
[plan](docs/features/automatic-app-inventory-implementation-plan.md) define
App-wide read discovery, bounded organization pages and deny-all command
bootstrap. They do not register repositories or widen command grants.
Public-repository rollout and second-installation qualification remain open;
former private deployment receipts are not carried forward.

The [CI efficiency design](docs/features/proof-preserving-ci-efficiency.md) and
its [plan](docs/features/proof-preserving-ci-efficiency-implementation-plan.md)
cover native collection, file-cohesive scheduling, preserved coverage/outcome
admission, isolated preparation and independent mutation cohorts. Requalify
these owners at the exact public source. Do not inherit private-run timing or
CPU-saving claims. Preserve database cleanup and temporal oracles; cheaper
execution requires its own equivalence proof. Remaining product, pilot,
release, deployment and optional-chat work stays open.

### Observation And Access Qualification

Task owners: CI-003, CI-007-CI-010, CI-015, CI-021, CI-027-CI-035.

Current D4/D7 source work is [persistent per-report budgets](docs/features/economics-budget-policies.md)
and its [plan](docs/features/economics-budget-policies-implementation-plan.md):
repository policies, atomic report signals, bounded APIs and separate UI tabs.
Public-source CI, immutable release, deployment and login qualification must
be established afresh. Do not create live policies merely to manufacture a
receipt. Continuous observation/backfill, cohort analytics, paired savings,
notifications, remaining queue/cache/shard/retry metrics and capacity remain open.

The prerequisite [administrator repository access](docs/features/administrator-repository-access.md)
and its [plan](docs/features/administrator-repository-access-implementation-plan.md)
require exact-source checks followed by separately authorized release and
App-scope deployment qualification. Preserve restricted defaults, emergency
scope, workload roles and omission admission. Recheck catalog-to-repository
access without changing neighboring databases. Access qualification does not
activate observation or carry forward a former private deployment.

Next is [continuous repository observation](docs/features/repository-observation.md)
and its [implementation plan](docs/features/repository-observation-implementation-plan.md):
durable enable/pause/workflow selection, bounded scanning/backfill, existing
collection reuse, finite capacity, persisted progress/gaps and API/UI parity.
Backend storage, scanning, API and the focused UI journey were delivered through
PR #156, with subsequent session recovery in PR #157. Their source, native and
bounded development observations do not close the remaining live lifecycle or
capacity qualification. All-history population, workflow-version provenance
and richer scan presentation remain D4-H1/D4-D7-S1 work rather than implied
completeness of recent observation. PR #160 adds archive administration as
described above. D7 still requires fresh deployed login, expiry/recovery,
return-view and logout verification; earlier successful login is not that proof.

D7 workflow navigation must present a pipeline as an entrypoint and its reusable
call graph, rather than a flat list of YAML files. Reuse discovery's exact-revision
`callEdges` and trigger evidence: a leading underscore is not semantic identity.
Show shared and dual-role workflows, keep unresolved calls and unreferenced
components discoverable, and preserve a complete source inventory separately.
Observation selectors address run-owning provider workflow IDs; cost aggregation
must not count the same attempt/job again through its parent and reusable call.
The graph presentation follows D4/D7-V1 above and remains planned, not an
implemented part of D4 storage.

### Failure Recovery Qualification

Task owners: CI-066 and CI-069; isolated repairs do not close composed recovery.

The additional2026-09-13 reliability review targets fefdeb38. Its unbounded
overload/timeout response finding duplicates D04, repaired in PR160 with an
absolute application deadline and permit release before failure sending.
Native predecessor evidence does not prove transport socket closure, scheduler
availability, deployment load or the correctness of later source changes.

Retain these distinct D1/D7/D8/E1 obligations:

- Evaluate bounded private diagnostic fingerprints containing admitted source
  coordinates and build identity, with correlation IDs. The current observer
  intentionally emits only exception type and stage. Preserve absence of
  messages, arguments, locals, credentials and raw provider payloads; prevent
  log storms and metric-label cardinality growth. A useful improvement needs
  both distinguishability and redaction witnesses, not unrestricted traceback
  logging or a new generic error framework.
- Close a composed failure matrix: before/after durable writes and COMMIT,
  response loss after successful COMMIT, worker cancellation/crash, duplicate
  retry, lease expiry/reclaim, dependent-service failure and saturated pools.
  Assert state from an independent connection and retry the same operation;
  no lost acknowledged work, double contribution or recovered stale authority.
- Measure critical-request progress and recovery under concurrent background
  load and explicit dependency outages, with owner-defined time/resource
  budgets. Isolated rollback, exception handling and finite-pool tests are
  necessary scoped evidence, not proof of combined liveness or production
  reliability. Record external assumptions and untested cuts explicitly.

The acceptance criterion is preserved state plus bounded, observable recovery
under an admitted fault model, not the number of catch blocks or architectural
layers. Security redaction and reliable settlement remain higher priority than
diagnostic convenience.

### Cross-Repository Reusable Workflows And Model Limits

Task owners: CI-022, CI-040-CI-044, CI-046 and conditional CI-071.

The [O2 dependency passport](docs/features/ci-operator-capability-completion-plan.md#o2-reusable-dependency-passport)
projects exact caller/callee/action identities and authorized reverse-consumer
references. It does not create a second parser or omission authority.

The proposed organization-wide CI library belongs to B3/B5, after the current
portability repairs. Extract portable check implementations, typed inputs and
versioned result contracts, not this repository's complete matrix as a mandatory
organizational policy. Consumer-owned manifests retain applicable checks, native
test roots, required outcomes, budgets and coverage/security thresholds. Keep
the catalog flat for selection and the execution DAG dependency-aware.

Before repository extraction, qualify one shared block against two different
synthetic consumer profiles with immutable caller/callee commits, minimal
permissions, no inherited deployment secrets, native evidence artifacts and
explicit missing/failed/cancelled/not-applicable outcomes. Test inaccessible
library, changed library with unchanged application, fork PRs and final-gate
identity. Bootstrap the coordinator first; create no external repository or
consumer workflow changes merely from this planning entry. Reconsider extraction
if the second consumer needs broad special cases or duplicates local policy.

The2026-09-13 review intake is validated against the unchanged planning owners
at fefdeb38 and the current archive repair. Its six observations are not six
established production failures. Preserve this work in D2/D4/D5/D7 and E1-E3,
without treating the archive PR as delivery of the following capabilities.

| Concern                 | Validated disposition                                                                                                                         | Scheduled acceptance                                                                                                                                                                                                               |
|-------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Runner time versus CPU  | The optimizer's `cpu` term sums predicted shard durations, not measured CPU consumption.                                                      | D2/D4: distinguish runner-seconds, CPU-seconds, wall time and billing; migrate any public cost vocabulary explicitly and retain measurement provenance.                                                                            |
| Scheduling optimality   | LPT is a bounded heuristic: with two workers and durations3,3,2,2,2 it gives7+5 instead of feasible6+6.                                       | D2: compare small instances against an exact oracle, include cross-profile slot allocation, setup and planner cost; retain the simpler heuristic unless measured benefit justifies a bounded solver. Never claim a global optimum. |
| Adapter applicability   | Fixed adapter coordinates are a protocol, not proof of arbitrary unmodified workflow support; current selected profiles require one workflow. | D5: declare support profiles and extend exact execution topology for caller/callee graphs. Unsupported semantics remain explicit full-only or rejected.                                                                            |
| Installation lifecycle  | Installation plus repository is an authorization scope; reinstall/transfer may change that scope without changing the historical workload.    | D4/E1: design repository lineage and explicit authorized archival continuity separately from current access. Never inherit old authority automatically or erase history because access changed.                                    |
| Shared resources        | Isolated exceptions do not isolate database occupancy or latency.                                                                             | D1/E1: measure combined ingress/planning/archive pool waits, throughput and fairness; introduce priorities or resource separation only against demonstrated budgets.                                                               |
| Dependency completeness | Hashes bind the graph but do not prove every relevant edge exists.                                                                            | D2/E3: independent graph-admission evidence, including a removed real edge between two known paths; unknown edges, scripts, generated inputs and external implementation revisions cannot authorize omission.                      |

For reusable adoption, the project retains its triggers, code/configuration,
run/attempt identity and required-check policy. The service repository owns
versioned execution blocks; Coordinator owns policy, planning and explanation.
Centralizing those definitions does not require central `workflow_dispatch`.

Implement in this order before claiming selective reusable-workflow support:

1. Freeze a supported provider/profile contract and resolve a bounded, exact
   cross-repository call graph. Bind caller commit, configuration, each callee
   commit/blob, non-secret inputs and relevant action/tool/data/environment
   identities. Unknowns remain visible and cannot prove omission.
2. Verify caller repository/run/attempt and trusted requester/callee identities,
   then refine execution and final-gate topology across that graph. OIDC leaf
   claims alone do not prove the whole transitive graph or script behavior.
   Distinguish App read access from Actions reusable-library access; preserve
   token privilege reduction and explicitly bound secrets/environments.
3. Package shared scripts/actions intentionally: checking out a project is not
   fetching implementation files from the service repository. Keep trusted CI
   implementation separate from untrusted PR code and pin both independently.
4. Use predefined reusable calls and admitted conditions/matrices for dynamic
   selection; do not promise arbitrary runtime-generated `uses` targets.
   Separate Coordinator-unavailable FullCI from definition-resolution failure;
   the latter requires an independent FullCI path or fail-closed merge policy.
5. Record two analytical axes: project/run/attempt/job and block/version/profile.
   Distinguish execution occurrences from repeated/carry-forward observations;
   never double-count parent/callee views or treat rerun snapshots as measured
   new CPU consumption. Present one expandable project pipeline in the UI.
6. Qualify changed-library/unchanged-code, nested calls, partial reruns, inaccessible
   libraries, missing graph edges, input/permission drift, environment protection,
   concurrency-group collisions and provider limits. Then propose a thin-client
   pilot; no source-repository migration or pilot target workflow change is authorized
   merely by this planning entry.

Platform premises: [caller context, permissions, runners and reruns](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations),
[literal reusable references and matrices](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows),
and [caller/reusable OIDC claims](https://docs.github.com/en/actions/reference/security/oidc).
Provider documentation is version-sensitive; revalidate it at implementation.

A bounded model executor/evaluator and durable advice evidence remain optional
behind monotonic admission. A reusable library is a supported thin-client
adoption target to design and qualify, not evidence that thin clients failed.
A typed DSL or central dispatch is a separate optional architectural decision
requiring demonstrated demand and its own failure model. Neither delays D6's
external bot APIs or moves UI chat ahead of its final priority.

### Final Optional Item: UI Configuration Chat

Task owner: CI-072, last and optional.

By the user's 2026-09-07 priority decision, implement the UI chat assistant only
after all other agreed product, repair, quality, pilot and deployment work.
It is not a prerequisite for D5 API-first administration, D6 external review-bot
commands, D7 operator UI or E1-E3 qualification. Retain schema-constrained draft
generation, explicit confirmation and deterministic admission when this final
optional item is undertaken; it grants no model-owned mutation authority.
