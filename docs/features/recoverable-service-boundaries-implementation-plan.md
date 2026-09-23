# Recoverable Service Boundaries Implementation Plan

This plan executes the [design](recoverable-service-boundaries.md), the
operational portion of B2 in the [global closure plan](evidence-led-operational-closure-implementation-plan.md).
It does not replace the remaining webhook, logout or external-deployment work.

## Writer Readiness

| Owner and delta                                                                                    | Protected observations and independent operands                                                                                                                             | Direct falsifier and whole-chain gate                                                                                                                                                                       |
|----------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Runtime package/probe: remove unused application exports; distinguish overload from dead process   | Exact interpreter, listener/port admission, status, transport progress/deadline and connection cleanup independently contribute to the result. Readiness/startup unchanged. | Fresh-interpreter import inventory; parameterized probe statuses; actual Uvicorn with saturated connections using the admitted h11 parser; existing startup and container smoke in GitHub.                  |
| Registrar caller boundary: propagate exceptions to its existing diagnostic owner                   | Domain conflict still returns false; cancellation propagates; persistence exception still prevents selected execution.                                                      | Real registrar invoked through DynamicPlanService, sensitive exception text absent and one class diagnostic present; existing selection/registration tests.                                                 |
| Maintenance wrapper: expose unexpected failure class                                               | Primary success/failure, optional operation success/timeout/failure and cancellation remain independent; telemetry never grants authority.                                  | Assert private diagnostics, unchanged counters, one primary failure event and propagated original exception; cancellation not converted into a failure result.                                              |
| Scrape rules: recognize three absent/failing signal states                                         | Target inventory, up sample, readiness sample, replica labels and hold duration independently determine each alert.                                                         | promtool input-series tests: healthy, no targets, failed target, missing readiness, mixed replicas and recovery; syntax check alone is insufficient.                                                        |
| Local database provisioner: native file input instead of secret argv                               | Existing role names/privileges, successful reads, nonempty secrets, quoting, database owner and persistent volumes unchanged.                                               | Static command/SQL contract; real psql rejects each empty/missing role file and each nonempty partial read with failure status without creating roles/database; connected-stack creation/restart in GitHub. |
| Proof runner and deployment inventory: bounded native scratch storage and exact source projections | Read-only root/input, no network/capabilities, ephemeral storage bound; complete deployment membership and current content hashes, unchanged caller classifications.        | Exact argv contract plus native promtool; independent source discovery/hash recomputation and packaged-resource equality.                                                                                   |

Independent validation consists of native GitHub witnesses and one bounded
frozen review of the changed operation boundaries. Local lint, type, route and
documentation admission are supporting static evidence only.

## Implementation Order

1. Change `runtime/__init__.py`, `runtime/healthcheck.py` and their internal test
   imports. Extend the existing health tests; add the cold-import and actual
   server-admission cases with bounded socket/task cleanup.
2. Remove the registrar's premature exception collapse. Extend caller-level
   registration diagnostics without adding a second observer abstraction.
3. Wire the existing diagnostic observer into `runtime/maintenance_round.py`
   and composition. Extend its finite stage set and failure/cancellation tests.
   Distinguish operation-raised `TimeoutError` from an expired native deadline;
   independently falsify both branches and retain cancellation propagation.
4. Add missing-target/signal rules, promtool fixtures and their existing runner
   integration. Update the operator how-to with required job labels,
   authentication, distinctions and failure actions.
   Bound native TSDB scratch storage to the container tmpfs; refresh both
   deployment inventory projections from the complete declared source profile.
5. Change `compose.yaml` and the owned bootstrap SQL to native file custody;
   extend the existing developer-environment contract tests. No dev-state reset
   or credential rotation is part of this source change.
6. Bind new documents/tests through the existing compact Proofkit routes,
   refresh their generated digest index and run local static admission.
7. Freeze the combined candidate, run a bounded independent control and the
   native Full Check, including real server, PostgreSQL, container and promtool
   witnesses, on GitHub. Publish additive corrections after first PR opening.

## Acceptance And Remaining Work

- No removal of the Uvicorn concurrency limit or fail-closed startup guard.
- Docker health result changes only for responsive HTTP 503; public readiness
  and authorization behavior do not inherit that result.
- No FastAPI, SQLAlchemy, Uvicorn or HTTPX import from the cold health CLI.
- Every changed failure reaches one bounded diagnostic owner without secrets.
- Alert syntax and semantic fixtures pass; actual scrape/Alertmanager evidence
  remains E1-owned.
- No role password in psql argv; creation/restart preserve existing local state.
- Existing and new native witnesses pass on the exact candidate; no unsupported
  local behavioral execution is used as a substitute.

After this portion, continue B2 with explicit failed-webhook recovery,
verified-provider revocation capacity, bounded session cleanup, edge-policy
review and Swarm signal-to-drain evidence. Do not mark the full B2 row done
based on these narrower observations.
