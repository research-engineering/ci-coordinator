# Webhook Recovery Boundaries Implementation Plan

Status: delivery plan

Date: 2026-09-06

Implement the [recovery boundary](webhook-recovery-boundaries.md) without
changing request admission, signing, durable identity or selective execution.

| Step | Owner / files                                   | Acceptance                                                                                                                                         |
|------|-------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------|
| 1    | Recovery specification and operator how-to      | Enumerate current consumers, REST coverage, unrecoverable gaps and exact incident closeout. No implicit queued work or autonomous recovery.        |
| 2    | `deploy/observability/ci-coordinator.rules.yml` | Add one warning from the existing HTTP counter; no new runtime dependency or metric labels.                                                        |
| 3    | `ci-coordinator.rules.test.yml`                 | Native promtool accepts an exact failed-delivery series; rejects each wrong selector, zero growth, absence and reset; verifies bounded resolution. |
| 4    | Current navigation and runtime Proofkit routes  | New documents are reachable and every changed owner retains its required commands. Pre-existing designs/plans stay unchanged.                      |
| 5    | Bounded review and exact-head GitHub proof      | Review the conditional claims and isolated alert operands; native checks must pass on the published head.                                          |

Local checks are static only. The existing `observability.rules` command uses a
container and therefore runs in GitHub, not on the developer machine. Publishing
this change does not authorize live redelivery, credential use, endpoint repair
or deployment. Administrator-owned recovery and consumer readback remain
external acceptance obligations.

Native closeout also requires the byte-exact deployment disposition projection
in both machine resource locations. Keep the existing exhaustive inventory
oracle. Separate the lease witness's five-second expiry from its local lock
timeout, without relaxing the before/after-lock or zero-effect assertions or
changing production timeout policy; run all four native PostgreSQL modes.
