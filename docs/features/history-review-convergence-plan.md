# History Review Convergence Implementation Plan

Status: proposed execution plan; no unchecked item is an implementation receipt.

Design: [History Review Convergence](history-review-convergence.md).
Baseline: `9547a85b1f9b2a8c79c59bf8c8633167731c9021`.
Archive implementation integration base:
`57760bfb0ec1106807d35cd72bec94b116565906`; intervening dependency-alert and
API-campaign changes are preserved. The historical ledger retains its original
source epoch and does not imply qualification of this newer implementation.

## Execution Rules

1. Preserve every pre-existing design/plan and unrelated local file.
2. Review the design and owner/readiness rows before behavior changes.
3. Partition independent writer scopes; serialize overlapping capability,
   persistence, composition and generated-contract changes.
4. Rebind every historical row, including rejected hypotheses, without treating
   source presence as native or operational qualification.
5. Use one forward migration only when a proved schema delta needs it; never
   rewrite published revisions or expand ordinary runtime erasure privileges.
6. Run bounded local static checks only. Behavioral, browser, database and
   coverage tests run in the repository-owned GitHub Actions workflows.
7. Freeze the candidate before independent validation. Repair confirmed
   blockers, then run the expensive final gate on the final candidate.
8. Do not merge, deploy, delete live data or claim production qualification
   merely because implementation and CI complete.

## Work Packages And Acceptance

| Package | Deliverable                                                         | Required independent falsifiers                                                                                                                                                         | Completion boundary                                                                                     |
|---------|---------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------|
| WP1     | Current-source historical ledger and source provenance              | Every original ID maps exactly once; R01 and coverage contrary evidence retained; rejected route/Prometheus/import hypotheses retain their original mechanisms/refuters.                | Source review, report digest receipt and approved design.                                               |
| WP2a    | Runtime scheduling of bounded detail expiry                         | Paused collection still permits expiry; shutdown cancels/awaits work; failure cannot delete statistics or escape its budget.                                                            | Native composition/service and restricted-principal cleanup evidence.                                   |
| WP2b    | Versioned allowlisted detail admission and import                   | Wrong scope/head/job; duplicate/oversize steps; disabled policy; first import once; replay; quota refusal; rollback; policy race; expired no-resurrection; enabling first import later. | Provider-to-store-to-read integration and native race proof.                                            |
| WP3     | Previewed resumable administrative erasure and explicit new dataset | Unauthorized/stale preview; uncertain replay; concurrent worker; bounded continuation; crash/restart; quota conservation; old cursor; preserved tombstone and historical receipts.      | Exact native API/DB/UI gates; backup erasure remains separate.                                          |
| WP4     | Scope, temporal presentation and cohort oracles                     | Unequal nondefault scope path; swapped fields/revisions; timestamp label/dateTime/title/value; absent provenance stays unknown.                                                         | Frontend native/coverage and API contract checks.                                                       |
| WP5     | Final OCI image vulnerability gate                                  | Wrong digest/platform; stale/missing advisory database; scanner failure; malformed/empty result; severe finding; narrowly expired exception.                                            | Release workflow static/native contract proof and exact artifact evidence when a release is authorized. |
| WP6     | Temporal and transactional proof closure                            | Current budget relation; terminal SQL CAS; source child-insert/cascade barrier; false source columns; empty lanes and cancellation.                                                     | Python unit/persistence and causal-oracle evidence.                                                     |
| WP7     | Qualification receipt, not inferred readiness                       | Required jobs complete at candidate; measured offered load/failures/quota and retained backup fences on named environment.                                                              | Report each absent or unauthorized operational witness as open.                                         |

## Writer Readiness

These rows constrain implementation, not bypass source-owner review. A writer
must fill any unresolved mechanism before its first semantic edit.

| Owner                     | Intended delta                                                          | Protected observations and derived surfaces                                                                        | Minimum whole-chain gate                                                  | Validator                         |
|---------------------------|-------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------|-----------------------------------|
| Documentation integration | New successor design, plan and traceable dispositions                   | Existing designs immutable; no change to product authority from a historical hypothesis                            | Link/structure checks plus source-backed review                           | Independent design reviewer       |
| History UI/API tests      | Add scope/temporal isolated oracles                                     | Wire API and existing user behavior unchanged; generated contract unchanged unless an owner delta proves necessary | Frontend test/coverage and type checks                                    | Independent final reviewer        |
| Runtime cleanup           | Wire existing bounded expiry behind an owned adapter                    | Pause does not stop expiry; statistics/first-import and shutdown budgets preserved                                 | Native maintenance/composition plus existing PostgreSQL cleanup witnesses | Independent final reviewer        |
| Detail import             | Resolve bounded scheduling and exact provider/SQL contract before edits | No raw data, no SQL across provider I/O, exact quota, immutable anchor, no resurrection                            | Provider/service/native persistence/read tests                            | Independent capability reviewer   |
| Dataset administration    | Resolve principal, command receipt, schema and tombstone before edits   | No ordinary runtime DELETE, no resurrection, no retired-generation reads, no live deletion                         | Native role/API/DB races and schema attestation                           | Independent capability reviewer   |
| Release evidence          | Resolve pinned scanner/database/policy contract before edits            | Exact immutable image, attestation chain, no severity suppression                                                  | Workflow/static policy and negative result admission tests                | Independent supply-chain reviewer |
| Worker/SQL proof          | Add missing current-owner causal witnesses                              | Do not tighten published schema merely to satisfy a stale report                                                   | Targeted unit/native persistence and current worker contract tests        | Independent final reviewer        |

## Order And Checkpoints

- [ ] Freeze current ledger, report provenance and a reviewed design.
- [ ] Complete WP4 and WP6's independently owned test changes.
- [ ] Implement and qualify WP2a before enabling any detail producer.
- [ ] Resolve and review WP2b's provider-to-store scheduling contract, then implement it.
- [ ] Resolve and review WP3's command/principal/restore boundary, then implement primary-store operations.
- [ ] Implement WP5 under the release owner's exact artifact contract.
- [ ] Reconcile all historical rows against the final diff and independent review.
- [ ] Run required final-candidate GitHub checks and record exact run/attempt/subject.
- [ ] Record WP7 operational witnesses or exact missing authority/evidence.

Each coherent deliverable can be published separately when independently
deployable and provable. A completed early package does not close this program.
The progress receipt must state changed files, candidate identity, review
findings, test results, remaining packages and non-claims. A plan checkbox may
be checked only from that evidence, never from agent completion alone.

WP3's chosen direction is a migration-principal maintenance CLI and a separately
declared erasure capability. Its destructive writer remains blocked until an
independent durable deletion-fence source and restore admission have exact
owners and coordinates. The missing source cannot be replaced by a row in the
database being restored. This is an unresolved safety operand, not a rejected
feature, and does not block the other independently owned packages.

## Stop And Replan Conditions

Replan the affected package on owner drift, an unresolved schema/principal
transition, an unsupported exact model selector, overlapping writers, missing
required test infrastructure, or a new mechanism invalidating the reviewed
proof. Continue unrelated safe packages. Stop before a live destructive,
deployment, secret or external-policy action lacking explicit authority.

Do not relabel an unimplemented package as rejected because implementation is
large. Do not label conditional optimizations as defects to manufacture a
negative SOTA conclusion. Retain their actual owner-bound proof boundary.
