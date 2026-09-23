# History Worker Progress Implementation Plan

Owner: runtime scheduling.
Contract and alternatives: [design](history-worker-progress.md).

## Ordered Delivery

1. Preserve the existing single-item orchestration and three-lane `run` API in
   `app/ci_history_collection.py`. Expose an explicitly validated one-item lane
   entrypoint; retire only the cumulative runtime-drain entrypoint and constants.
2. Add `runtime/history_collection_worker.py` using one retained task and four
   structured children, per-item deadlines, explicit progress yield, idle wait,
   cancellation and a retryable bounded drain. Never create a replacement for
   unsettled work or retain a transaction between items.
3. Add `runtime/background_services.py`: primary-first single-use startup,
   attempted-owner tracking, concurrent conjunctive drain and primary-only health.
   Keep `RuntimeResources` as the unique engine/client cleanup owner.
4. In composition remove only history collection from periodic maintenance;
   connect its worker after authoritative reconciliation. Pass the existing idle
   interval and shared background-drain budget. Delivery and other maintenance
   schedules remain unchanged.
5. Add bounded worker-liveness metrics and an alert with positive/negative
   fixtures. Worker failure must be visible without rewriting reconciliation
   health or reducing required validation.
6. Add `REQ-CI-RUNTIME-054`, exact Proofkit bindings, index and roadmap routes.
   In current `REQ-CI-RUNTIME-048`, retain application guarantees and explicitly
   transfer only the replaced runtime scheduling clause. Preserve all
   pre-existing design and implementation-plan bytes. Refresh both deployment
   inventory digests after changing an alert rule or its fixture.

## Acceptance Matrix

| Boundary         | Required native witness                                                                                          |
|------------------|------------------------------------------------------------------------------------------------------------------|
| Application item | Invalid lane rejected before effects; existing authority and outcome matrix unchanged                            |
| Scheduling       | Progress beyond 16 items, no cross-item deadline, one active item per lane, other lanes progress while one waits |
| Idle/error       | No tight-loop polling, transient failure recovers, own timeout differs from an inner `TimeoutError`              |
| Startup          | Failed/stopped initial reconciliation starts no worker; partial later startup still owns cleanup                 |
| Shutdown         | All owners signalled; one pending/false/exception result prevents disposal; retry retains original tasks         |
| Independence     | Optional worker failure leaves primary readiness unchanged and emits its own signal                              |
| Durable state    | Existing real PostgreSQL lease, retry, stale-success/stale-failure and cancellation tests remain routed          |

Freeze the complete vertical diff and dependency-complete read authority for one
independent review under `AGENTS.md`. Run local static gates, exact-range Proofkit
selection and native GitHub tests. Repair published PRs only additively, then
squash after exact-head admission. Release the exact green master and compare
bounded live progress, aborts, idle outcomes and resource costs on the original
pilot target generation. Report unavailable browser or capacity evidence explicitly.
