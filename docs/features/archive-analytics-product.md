# Archive Analytics Product

Status: bounded implementation contract; native qualification pending

Owner: `ci_economics` owns metric, forecast and slowdown meaning; `app` owns
repository authorization; `persistence` owns bounded scalar reads; HTTP owns
transport. Product authority: ROADMAP D4/D7-V1 and D4/D7-F1. Delivery order and
pending gates: [implementation plan](archive-analytics-product-plan.md).

## Minimum Sufficient Model

Disposition: construct a finite aggregation relation, a chronological forecast
relation and a slowdown state machine. These public, cross-owner derived values
are material. Existing archive identity, timing and population contracts are the
as-is model. There is no existing archive analytics endpoint. The intended delta
is a read-only projection, not a new collection, billing or alerting subsystem.

Protected observations: archive writes/retention, generation fencing, audit-role
and repository access, paired savings authority, planning and execution remain
unchanged. All old source/design/plan files remain unchanged in this lane.

For a request Q, select installation/repository/current generation, a half-open
UTC whole-day window of at most 366 days, optional workflow ID, exact job name,
and explicit purpose. Dates refer to run creation, not calendar billing or job
completion. Reruns count once per exact attempt; unique run counts deduplicate
run IDs, but runner time includes each attempt's work. Run/attempt populations
describe the workflow/time scope before job filtering; matching-attempt counts
describe the observed selected jobs. An absent job in a partial attempt cannot
prove that the requested job did not execute.

Daily buckets contain archived runs, attempts, complete/partial/unavailable/
conflict attempts, known missing jobs, unknown job-population attempts, selected
jobs, failures (failure/timed_out/startup_failure), cancellations, valid duration
and queue sample counts, missing/inconsistent timings and conflict exclusions.
Every known timestamp pair must be monotonic. Duration requires start/end;
queue requires create/start. A missing third timestamp need not invalidate the
available interval. Milliseconds are floored per job before addition. Runner
milliseconds are summed elapsed occupancy, not CPU or wall-clock critical path.
No valid samples yields null, never zero. Empty days remain explicit unknown
coverage, not observations of idle infrastructure. SQL reads the existing
bounded header JSON and scalar job columns; only daily aggregates and scalar
population evidence leave the database. Job blobs and ephemeral details are
not read. Scalar projection integrity relies on
the existing archive writer; this query is not a second full canonical attestor.

Purpose classification is a bounded, injected repository/generation-bound map
of exact (workflow ID, job name) to one or more categories with version and
provenance. Missing entries are unknown; multi-category entries are mixed.
Selecting lint includes the whole duration of a mapped mixed job, once, and
never describes it as lint-only time. No classification is inferred from names.
The applied map and its digest are returned. Root owns configuration wiring.

## Forecast Contract

Supported profile: current retained, selected run-creation daily occupancy,
conditional on unchanged workload and collection. It is not total provider CI
usage or a calendar-month bill. Provider history coverage remains unknown.
Use an expanding historical daily mean, not ML. Lookback is Q's window; horizon
is 1..30 days. A whole-day series must have complete nonconflicting attempts,
at least the configured selected job samples per day and no excluded duration
samples. Empty/partial days suppress the forecast. No missing values are filled.

Chronological nonoverlapping horizon-sized folds follow an initial training
prefix. Four calibration folds precede four evaluation folds. Each fold trains
only on its preceding prefix. Each evaluated interval uses only earlier fold
errors. The minimum lookback is `14 + 8 * horizonDays`: 70 supported daily
buckets for a seven-day forecast, 254 for a thirty-day forecast. Missing days,
including unobserved weekends, cannot be treated as known zero-usage days.
Radius is the largest preceding absolute horizon error; lower bound is
clamped to zero. Return fold boundaries, predictions, actuals, interval coverage,
mean absolute error, cutoff and sample support. Minimum empirical coverage is
80 percent over evaluated folds; failed calibration suppresses the final value.
This is an empirical band, not a claimed probabilistic guarantee. Backtests
reconstruct chronology from the current archive; refinement/import availability
at the historical cutoff is not reconstructible and is explicitly not claimed.
Observed within-workflow definition/event changes suppress a forecast.
Missing definition provenance does not itself suppress a descriptive conditional
forecast; structural change outside stored facts remains unknown. The current
provider decoder explicitly writes `workflowBlobSha=None`, so demanding a known
blob for any descriptive result would make the feature unusable on that path.

No tariff is supplied in this slice: monetary estimates are unavailable, and
paired comparisons remain the sole savings authority.

The result algebra rejects an available forecast without a value, ordered
`lower <= prediction <= upper` band, sample support and calibration/evaluation
folds. An unavailable forecast cannot carry any final estimate/band. Historical
backtest diagnostics may remain visible when final calibration fails. The HTTP
outcome is exclusive: exactly one report or unavailable reason is present.

## Slowdown Contract

Descriptive selection requires exact workflow ID and exact job name. It does
not require workflow blob provenance. The separate `cohort.compatible` field
qualifies only the narrow profile with one known workflow blob SHA and one event
per workflow throughout the selected window. Other source,
matrix inputs, runner class/image, CPU, cache, dependencies and concurrency are
not fixed by this profile and remain unknown contributors. Missing or changing
provenance is visible alongside the descriptive signal, never a claim of a
compatibility-qualified regression. The signal detects observed duration change,
not code regression or causal runner degradation.

Compare the first seven complete daily buckets' job-weighted mean with each
subsequent supported day's mean. Default entry requires both 20 percent and
1000 ms increase for three consecutive days. Recovery requires three consecutive
days below half the entry thresholds; the middle band holds state. Thresholds,
minimum daily samples and persistence days are bounded request parameters.
Unknown days reset streaks; they cannot prove recovery. Recompute from the fixed
baseline on every read; stable event keys deduplicate repeated identical reads.
No durable notification, alert registry or CI authority is introduced.

## Cost Decision And Writer Readiness

The cheapest viable route is database-side bounded header extraction plus scalar job
aggregates, existing history UoW and before/after dataset revision fencing.
Direct engine access would bypass compatibility admission; full canonical job
decoding would increase transfer/CPU and couple analytics to details. A new
index/migration, materialized rollup, seasonal library or generic engine is not
needed for the admitted limits. Reconsider if native query plans/timeouts show
that the bounded route cannot serve representative data, or calibration fails
to support a useful administrator decision. No global optimum is claimed.

Limits: 100000 scoped attempts, 1000000 jobs inspected before job filtering,
366 returned buckets, 64 mapping entries, 1 MiB serialized response, 4096 query
bytes, two admitted concurrent HTTP requests, 5-second SQL statement timeout
and 20-second application deadline. Exceeding a row limit returns explicit
unavailable, never silently sampled totals. Read-committed snapshots are accepted
only if the entire dataset value is unchanged before/after all scalar reads.
Concurrent archive changes or generation/state changes fail closed.

The initial 2000-attempt/client-header proposal was rejected after root review:
100 runs/day already exceeds it in an ordinary month. SQL now folds per-attempt
job aggregates into daily buckets without materializing history in Python.
For illustration, 100 attempts/day with 20 jobs each fits a 365-day row budget
(36500 attempts/730000 jobs); 1000 attempts/day does not. These are arithmetic
capacity examples, not native latency qualification. A job-dense month may hit
the job limit before the attempt limit. Request a shorter interval or exact
workflow; job-name filtering does not hide the pre-filter scan budget. Indexed
rollups, larger budgets and full-history scalability remain deferred until
native query-plan/cost evidence justifies them. This is not complete full-history
analytics, and timeout handling does not prove ordinary year queries are fast.

## Wire Handoff

Register `build_ci_history_analytics_router(CiHistoryAnalyticsRouteDependencies)`
and `HISTORY_ANALYTICS_REQUEST_LIMIT` in shared composition. Inject
`CiHistoryAnalyticsService(authorizer, store, purpose_mapping)` using keyword
arguments and `TransactionalHistoryAnalyticsStore(history_uow_factory)` using
the existing `PostgresHistoryUnitOfWork`. The optional map resolver is a bounded,
in-memory configuration lookup `(RepositoryScope, generation) -> PurposeMapping
or None`, not a new provider or network call.

Reuse the current `CiEconomicsAuthorizer` implementation for `authorizer`; do
not substitute cached UI membership or map existence for scope authorization.
The service requires exact `True` from `allows_scope(actor=authenticated_actor,
scope=query.scope)` before resolving a mapping or reading storage. The map must
carry the exact installation ID, repository ID and generation. Construct its
`entries` and `purposes` as tuples in Python, or load configuration JSON with
`PurposeMapping.model_validate_json`; strict Python validation intentionally
rejects JSON lists. Mapping absence leaves jobs unknown and returns explicit
`purpose_mapping_unavailable` for named/mixed purpose requests. It does not
disable ordinary workflow/job reads or authorize collection/configuration.

GET `/api/v2/economics/repositories/{installation_id}/{repository_id}/history/analytics`
requires `generation`, `createdFrom`, `createdUntil`; optional `workflowId`,
`jobName`, `purpose`, `horizonDays`, `minimumDailySamples`,
`degradationRelativeBps`, `degradationAbsoluteMs`, `persistenceDays` have typed
OpenAPI bounds. Use exact UTC-midnight ISO timestamps. Authentication is audit
session/bearer plus current repository authorization. Unknown/duplicate inputs
return 400; authentication/access/dependency errors retain 401/403/503 and
no-store. A supported read returns `{outcome: available, report, unavailable:
null}`; a typed budget/generation/data limitation returns HTTP 200 with
`{outcome: unavailable, report: null, unavailable: {reason}}`. Forecast and
degradation statuses are independent inside the report. All keys are camelCase;
units/population/query/map/revisions/cutoff accompany the values.

| Writer owner | Independent operands and intended delta                                                                                                                 | Protected/derived surfaces                    | Falsifier and whole-chain gate                                                                                             |
|--------------|---------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------|----------------------------------------------------------------------------------------------------------------------------|
| Domain       | Scope/window/map, all attempt populations, job sample counts/sums, known definition/event, forecast prefix/errors, slowdown baseline/thresholds/streaks | Existing economics/planning; new typed output | Change each operand independently; sparse, partial, conflict, time leakage, mixed classification and recovery native cases |
| Persistence  | Current dataset, bounded headers, scalar job timestamps/conclusions and complete key joins                                                              | Existing schema/retention; domain input       | Foreign generation/repository, cap overflow, reversed timing, revision race, no job blobs; native PostgreSQL cases         |
| Application  | Exact actor/scope authorization, store result binding and deadline                                                                                      | Existing role/access policy; HTTP result      | Denied/unknown authorization performs no read; foreign result rejected; native service cases                               |
| HTTP         | Existing session/bearer audit admission, closed query names, request/response budget                                                                    | New OpenAPI route only                        | Unknown/duplicate query, invalid window, denied identity, safe errors/no-store; native route cases                         |

Readiness is closed for authoring this bounded delta under the supplied writer
assignment. Root is the independent validator and must rebind the new bytes,
wire shared composition, regenerate API types and run native owner gates before
qualification. Static lint/type checks do not close these gates.
