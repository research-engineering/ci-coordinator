# Operate Production Observability

Use this procedure after the service, Prometheus scrape, rules file, and
Alertmanager route are deployed.

## Configure Scrape Identity

Use `job_name: ci-coordinator` and discover each serving replica separately.
Keep a stable `instance` label per target; scraping only a load-balanced virtual
address does not establish per-replica coverage. Mount the metrics bearer as a
file readable only by the scraper:

```yaml
scrape_configs:
  - job_name: ci-coordinator
    scrape_interval: 30s
    scheme: https
    authorization:
      type: Bearer
      credentials_file: /run/secrets/ci-coordinator-metrics-token
    static_configs:
      - targets: [ci-coordinator-replica.example:443]
```

Replace the example target with the admitted discovery inventory. Keep the
default TLS verification; do not expose the metrics credential to browser users.
Monitor Prometheus and Alertmanager from an independent signal path. The rules
cannot detect the loss of their own evaluator or infer a removed replica from
an inventory that no longer names it.

## Verify The Signal Path

1. Check liveness and readiness separately:

   ```bash
   curl --fail-with-body https://ci-coordinator.example/healthz
   curl --fail-with-body https://ci-coordinator.example/readyz
   ```

2. Confirm the scraper receives the standard exposition. For a manual check,
   use a protected header file containing `Authorization: Bearer <token>` so the
   credential does not enter the command arguments or shell history:

   ```bash
   curl --fail-with-body --header @/run/secrets/ci-coordinator-metrics-header \
     https://ci-coordinator.example/metrics
   ```

3. In Prometheus, verify `ci_coordinator_ready`,
   `ci_coordinator_plan_requests_total`, and
   `ci_coordinator_http_request_duration_seconds_count` have fresh samples for
   every serving replica.

4. Confirm the `ci-coordinator.safety`, `ci-coordinator.availability`,
   `ci-coordinator.telemetry`, `ci-coordinator.latency`, and
   `ci-coordinator.history` rule groups report
   healthy evaluation timestamps.

5. Route one synthetic test alert through the production Alertmanager receiver
   and record the acknowledgement outside this repository.

The container probe accepts a timely HTTP 200 or 503 on its admitted listener.
A 503 can be Uvicorn rejecting excess concurrency before application dispatch;
it proves neither readiness nor its own cause. Check `/readyz`, request rates
and admission metrics before deciding whether to remove traffic or restart.

## Diagnose An Alert

| Alert                                    | First evidence                                                                                          | Required action                                                                                                                                                                               |
|------------------------------------------|---------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `CIUnsafeOmissionObserved`               | Authenticated workbench shadow result and pair-owned audit event                                        | Keep or return enforcement to FullCI, preserve evidence, and identify the missing obligation or witness.                                                                                      |
| `CIReplayMismatchObserved`               | Workbench replay status and `ci-coordinator-audit-replay`                                               | Stop rollout expansion, verify the ledger from the last trusted prefix, and treat unresolved integrity as a release blocker.                                                                  |
| `CIReconciliationTerminalFailure`        | Readiness dependency, reconciliation round counters, and process log correlation                        | Remove the replica from service, preserve logs, restart only after the failure class is understood, and verify another healthy round.                                                         |
| `CIPlanAvailabilityFastBurn`             | Plan result rates split by `dependency_unavailable` and `issuance_unavailable`                          | Check PostgreSQL, GitHub App/JWKS reachability, signer construction, and issued-plan persistence; FullCI in target workflows remains the fallback.                                            |
| `CIPlanAvailabilitySlowBurn`             | The same result split over 30m and 6h                                                                   | Open a tracked reliability repair before the remaining budget reaches the release threshold.                                                                                                  |
| `CIPlanLatencyFastBurn`                  | Route histogram and dependency latency                                                                  | Compare request duration with database and GitHub availability; do not increase the timeout until the blocking owner is measured.                                                             |
| `CICoordinatorNotReady`                  | Generic `/readyz` status, authenticated readiness dependency metrics and correlated private diagnostics | Identify the failing dependency from private evidence before repair; the public response intentionally omits identifiers. Liveness success does not override readiness.                       |
| `CICoordinatorTargetsAbsent`             | Scrape discovery and `up{job="ci-coordinator"}`                                                         | Restore the expected target inventory; silence from a missing job is not availability.                                                                                                        |
| `CICoordinatorScrapeFailed`              | Failed target status, TLS and metrics authentication                                                    | Restore scraping for that instance; a 401 requires credential repair, not a service restart.                                                                                                  |
| `CICoordinatorReadinessTelemetryAbsent`  | Successful scrape without a matching readiness series                                                   | Verify target identity, metric relabeling and artifact compatibility; another replica's samples do not close the alert.                                                                       |
| `CIHistoryCollectionFailuresObserved`    | Item outcomes by lane and target                                                                        | Separate `store_unavailable` from `unexpected_error`; inspect database/readiness and redacted diagnostics before considering a retry.                                                         |
| `CIHistoryWorkerStopped`                 | Worker-liveness gauge by lane and live scrape target                                                    | Inspect unexpected worker termination and redacted diagnostics. Correct the cause and use an approved service restart; never reset the archive to clear this signal.                          |
| `CIHistoryProviderFailuresObserved`      | Provider results by lane, outcome and target                                                            | Check access, response binding, malformed/incomplete/unstable reads and deadlines. Verify durable history state separately; `applied` can record a deferral.                                  |
| `CIHistoryDeliveryFailuresObserved`      | Inbox transfer outcomes and private diagnostics                                                         | Investigate store failures, scope/round timeouts or unexpected errors; verify source-to-inbox/recheck state rather than assuming delivery loss.                                               |
| `CIHistoryOldPendingSamplesObserved`     | Selected-page age bucket and count by target                                                            | Inspect active dataset, generation, initial import/replay, capacity and lock contention. Repeated old samples do not measure the full backlog or prove a stuck worker.                        |
| `CIHistoryExpiredPendingSamplesObserved` | Nonzero expired-sample observations by target                                                           | Inspect retained source, inbox receipt and authenticated history gaps; determine whether authorized recovery is possible. Samples can repeat the same sources and do not prove distinct loss. |

## Find The Failed Replica

`CIReconciliationTerminalFailure` intentionally retains one service-level
`max` page. Resolve target identity in Prometheus before following its recovery
procedure; do not assume that an alert without instance labels names a replica.

```promql
ci_coordinator_reconciliation_background_terminal_failure{job="ci-coordinator"} == 1
```

Keep the returned `job` and `instance`, correlate fresh readiness/dependency
samples and private logs, and confirm that the target still belongs to the
admitted discovery inventory. A recovered gauge during the fifteen-minute
alert hold may return no failed target. Inspect the alert-time range rather
than restarting an arbitrary healthy replica. The gauge has no exporter-owned
labels; `job` and `instance` are scrape metadata. Changing the rule to per-target
paging would change grouping, volume and pending timers and is not authorized
by this procedure.

## Investigate History Warnings

The [worker scheduling contract](../features/history-worker-progress.md) adds
`ci_coordinator_ci_history_worker_running{lane}`. It is one while the lane task
is alive, including its idle wait, and zero after it stops. It is not provider
success, backlog completeness or planning readiness. The alert requires a live
scrape target and two minutes of a stopped known lane; it resolves when that
lane returns. Missing series do not prove that workers are configured. Check
deployment identity and the existing scrape-availability alerts in that case.

The [history warning design](../features/history-operational-alerts.md) owns
initial thresholds and their non-claims; its
[implementation plan](../features/history-operational-alerts-plan.md) records
the separate native qualification gates. Inspect the following Prometheus
queries over the alert-time interval, retaining target metadata:

```promql
sum by (job, instance, lane, outcome) (
  increase(ci_coordinator_ci_history_items_total{job="ci-coordinator"}[15m])
)
```

```promql
sum by (job, instance, lane, outcome) (
  increase(ci_coordinator_ci_history_provider_results_total{job="ci-coordinator"}[15m])
)
```

```promql
sum by (job, instance, outcome) (
  increase(ci_coordinator_ci_history_delivery_outcomes_total{job="ci-coordinator"}[15m])
)
```

For the old-selected-source warning, count observations above the inclusive
one-hour bucket only where both operands belong to the same target:

```promql
sum by (job, instance) (
  increase(ci_coordinator_ci_history_pending_age_seconds_count{job="ci-coordinator"}[15m])
  - ignoring(le)
  increase(ci_coordinator_ci_history_pending_age_seconds_bucket{job="ci-coordinator",le="3600.0"}[15m])
)
```

For expired samples, count observations containing at least one expired pending
source, not the number of expired sources or lost deliveries:

```promql
sum by (job, instance) (
  increase(ci_coordinator_ci_history_expired_pending_sample_count{job="ci-coordinator"}[15m])
  - ignoring(le)
  increase(ci_coordinator_ci_history_expired_pending_sample_bucket{job="ci-coordinator",le="0.0"}[15m])
)
```

The age metric observes the oldest eligible source in each selected inbox page,
not the full backlog's current oldest source. The expired histogram observes
at most 100 sources per bounded sample; repeated samples are not distinct
losses. Prometheus increases are extrapolated estimates, not exact event
cardinality. An operand without enough samples in the window omits its target
from these queries. Cold starts and uneven scrape histories can distort the
estimated difference; inspect paired series and timestamps before asserting
impact. Verify full metric families and scrape freshness before interpreting
silence or treating an estimated observation count as a durable event count.

Item/provider/delivery warnings require at least three estimated observations
over fifteen minutes and five minutes pending. The old-page warning uses the
same observation threshold; a nonzero expired sample warns without pending.
All five hold for fifteen minutes after the expression clears. A held warning
does not assert continuing failures. `none_due`, `busy`, `claim_lost`,
`capacity_reached`, cancellation, inactive collection and no traffic alone
are not outages. `partial`, `provider_not_terminal`, unavailable historical
attempts and unavailable archived job population are not selected failures.

`CIHistoryDetailCleanupFailuresObserved` separately observes failed or timed-out
`ci_history_detail_cleanup` maintenance operations with the same fifteen-minute
window, three-observation threshold, five-minute pending period and fifteen-minute
hold. Cleanup remains required when collection is paused; pausing imports does
not suppress this warning. Inspect per-target increases of
`ci_coordinator_maintenance_operations_total{operation="ci_history_detail_cleanup"}`
and private diagnostics. A successful empty batch and counter reset are not
failures. Logical detail expiry still hides expired content if physical cleanup
is delayed, but unreclaimed bytes can consume storage quota.

Read the authenticated dataset/configuration state and gaps after target
drilldown. No per-repository identity exists in these metrics: determine scope
through authorized read models and private evidence, never by adding repository
or exception labels. Check provider quota only with provider-owned evidence;
these result labels do not identify quota exhaustion. Do not reset checkpoints,
change access, alter retention, reactivate paused datasets or increase capacity
solely because a warning fired. Any recovery remains subject to its own owner
and authorization.

Calibrate thresholds against normal import/replay age, worker cadence, scrape
coverage and actionable incidents before changing them. These rules do not
prove enabled-dataset progress, throughput headroom, retention recovery,
production alert delivery or receiver capacity. Native rule fixtures are not a
production scrape or notification receipt; the deployment owner must still
qualify delivery, acknowledgement and sustained workload separately.

## Correlate One Plan Request

1. Read `X-Correlation-ID` from the coordinator response.
2. Find the JSON completion event with that `correlationId`.
3. Use `issuedPlanRecordId` to locate the durable issued plan and pair-owned
   audit evidence in the authenticated workbench.
4. Use `repositoryId`, `workflowRunId`, and `runAttempt` to locate the exact
   reconciliation subject and GitHub Actions run.
5. Compare `planId` with the signed envelope and stored planning evidence.

The correlation identity is diagnostic only. Authorization still requires the
admitted OIDC or operator credential and exact repository scope.

## Close The Incident

An alert may be resolved only when:

```text
CauseIdentified
and UnsafeAuthorityRemoved
and RequiredDependencyHealthy
and FreshSignalObserved
and DurableEvidenceConsistent
```

For safety alerts, a quiet metric after restart is insufficient because a
counter reset can hide the previous process observation. Close against durable
workbench and audit evidence, then verify a fresh reconciliation round.
