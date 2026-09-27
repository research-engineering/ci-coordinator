# Request Outcome Observation Implementation

Status: implementation and acceptance companion; native qualification pending

Design: [request outcome observation](request-outcome-observation.md).
Work belongs to existing CI-054 in the [task register](../../ROADMAP.md).
Independent review and execution placement follow [AGENTS.md](../../AGENTS.md).
This companion sequences acceptance, not another readiness declaration or queue.

## Scope And Order

1. Preserve the [observability owner](../architecture/modules/observability.md)
   and [SLO policy](../reference/service-level-objectives.md). Slice A projects
   terminal request evidence; slice B handles authenticated metrics exposition
   failure. Keep the reviewed production and causal-test bytes unchanged while
   integrating metadata. Readiness scheduling (C) and database drain (DB1) are
   outside this batch.
2. Append RUNTIME-015's observation obligations without weakening prior text.
   Reuse RUNTIME-008's shutdown partitions and RUNTIME-035's admission/deadline
   contract. Retain all old Proofkit routes, commands and mandatory tuples; add
   only missing causal owner relations and exact-byte source-index updates.
3. Regenerate admitted self-CI projections only after inputs stabilize. Review
   the changed projection population and preserve command catalogs, required
   native families and omission authority. Root integrates once onto the actual
   base and independently checks shared application composition before freeze.
4. Run bounded metadata/static admission, then one exact-candidate independent
   CONTROL and native qualification through the existing GitHub workflow. A
   changed base or source invalidates dependent hashes and prior run identity;
   source review and local static checks do not establish native success.

## Acceptance Chain

All paths below are existing native owners under `backend/tests/unit/` unless
an explicit repository path is shown. Their commands are existing Proofkit
catalog entries, not permission to run behavior locally.

| Obligation | Witness owner and decisive control | Command |
| --- | --- | --- |
| Eligibility, completion and sink ownership | api/http/test_plan_requests.py: real parsed request, typed trusted identity, one use-case call, selected/FullCI/duplicate, independent sinks, send/cleanup cuts and literal terminal counts | python.test |
| Exclusion priority overlap | Same file: test_conflict_exclusion_survives_response_start_failure, healthy 409 versus external start cancellation/send failure, exact exception identity, conflict=1/other=0 and zero success latency | python.test |
| Projection and loss tolerance | observability/test_observability.py: four exclusions with isolated fault operands; returned-start/final-body/trailer completion, instrumentation failure and primary error conservation | python.test |
| Owned expiry and lease lifetime | api/http/test_request_admission.py: test_owner_expiry_cut_survives_send_unwind_and_cancellation_suppression, overload hard expiry and retained cancellation-release controls | python.test |
| Correlation and exposition | api/http/test_correlation.py and api/http/test_observability_route.py: correlation survives defects; authenticated rendering failure is empty503/no-store; rejected auth does not render; cancellation retains identity | python.test |
| Packaged runtime transport | runtime/test_runtime_diagnostics_server.py: actual entrypoint, native h11 and lifespan/error observations; no application-shaped surrogate for this boundary | python.test |
| Shutdown documentation consistency | runtime/test_shutdown_budget.py: independent literal partitions for the documented 30-second application assumption; both persisted stop-timeout45 recipes remain governed by runtime-composition.md | python.test |
| SLO populations and alerts | deploy/observability/ci-coordinator.rules.test.yml: every BAD label, cancelled/empty no observation, mixed coverage, reset handling and unchanged success-only latency; tracked rules admitted by digest-pinned promtool | observability.rules |

The direct-state projection matrix proves only its finite incomplete-response
operands. It is not real-route timeout attribution or every combined fault.
The conflict/send route witnesses use controlled typed authentication/use-case
boundaries, not live OIDC, signing or persistence. A counter-mutation that maps
conflict plus cancellation to cancelled must fail the new literal route vector;
no causal kill is claimed until it is executed in the admitted environment.

## Completion Boundary

Preserve auth, planning/signing, cookies, correlation, response-start fences,
no-queue permits, absolute deadlines and original exceptions. No second send,
issuer retry, response copy or newly inferred status is admitted. Completion is
server-side ASGI send evidence, not client delivery. Review the final routes and
hashes before admitting native results from that same candidate.

Acceptance requires the existing static requirements/docs/JSON/proof routes,
native Python and Promtool gates, plus exact-base review. Missing or failed
native evidence remains pending, not a pass. No live scrape, SLO attainment,
readinessC, DB1, deployment or absolute OS cleanup guarantee is established.
