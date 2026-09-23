# Bounded Runtime Recovery Implementation Plan

Status: delivery plan

Date: 2026-09-06

## Scope

Implement the [bounded runtime recovery design](bounded-runtime-recovery.md).
Preserve predecessor design and plan payloads. Deliver two owner-local repairs
without adding a scheduler, persistence model, dependency or runtime setting.

## Ordered Work

| Step | Owner/files                                               | Change                                                                                                                                                                      | Acceptance                                                                                                                                                          |
|------|-----------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1    | `production_admission/file.py` and its existing unit test | Add nonblocking descriptor open; exercise a real writerless FIFO in a bounded child.                                                                                        | Exact non-regular rejection; stable regular bytes and old integrity guards remain.                                                                                  |
| 2    | `runtime/maintenance_round.py`                            | Preserve startup-only primary admission; after startup, run existing bounded maintenance concurrently with the parent primary and rethrow its ordinary failure after drain. | Cleanup progresses through later primary failure/waiting; cancellation and completed-round health retain their meaning.                                             |
| 3    | `test_maintenance_round.py`                               | Add causal Event barriers, a held child during ordinary failure, watchdog-source discrimination and failed-startup recovery.                                                | Reject serial start, swallowed primary errors, cancellation substituted by timeout, cancelled cleanup, premature startup and work admitted after an existing abort. |
| 4    | Current indexes and runtime Proofkit routes               | Bind the successor and plan to runtime requirements 017 and 038 and existing native commands.                                                                               | Exact source digests, no dropped predecessor route, reachable docs and complete selective routing.                                                                  |
| 5    | Frozen review and GitHub Full Check                       | Review the concrete owner deltas and oracle distinctions; use native CI for execution.                                                                                      | Exact-head required gates pass; unavailable independent review remains explicit rather than being reported as success.                                              |

## Proof Boundary

The smallest sufficient local gate is Ruff, mypy, import policy, JSON,
documentation and Proofkit admission. Behavioral FIFO, asyncio, scheduler and
PostgreSQL tests execute only in GitHub. A passing cache-freshness PR is not
evidence for this separate candidate. Normal publication is additive and merge
is squash-only after required exact-head evidence.

Review load/cleanup deadlines, runtime resource ownership and typed exception
behavior together. The intentional concurrent-task and failed-round publication
tradeoffs are in the design; no physical deletion or readiness latency SLO may be
inferred from the scheduler unit witnesses. Provider/deployment capacity and the
remaining audit rows stay in the global roadmap.
