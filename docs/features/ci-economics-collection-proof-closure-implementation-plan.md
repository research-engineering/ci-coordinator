# CI Economics Collection Proof Closure Implementation Plan

Status: accepted implementation plan
Design: [Collection proof closure](ci-economics-collection-proof-closure.md)

## Acceptance

```text
PublicResultMatchesCollectionCommit
and InternalReplayIsIdempotent
and ContradictoryReplayIsRejected
and DuplicateCompletionCapturesAtMostOnce
and BothExpiredClaimLockOrdersAreWitnessed
and CommitFailureCannotPublishCaptured
and ConcurrentChildrenFinishBeforeResourceDisposal
and ExistingRetentionLifecyclePasses
and RequiredGitHubChecksPassForPublishedHead
```

## Writer Readiness

| Owner                          | Delta                                                                                                            | Protected observations                            | Derived surfaces                          | Validator                                                         |
|--------------------------------|------------------------------------------------------------------------------------------------------------------|---------------------------------------------------|-------------------------------------------|-------------------------------------------------------------------|
| `ci_economics` collection port | Narrow public result to captured/claim_lost                                                                      | Exact claims and failure algebra                  | application result type                   | typecheck and collection tests                                    |
| `persistence`                  | Project a winning collection CAS to captured internally; publish it only after public adapter commit and cleanup | Evidence replay, conflict, rollback, lease policy | collection adapter                        | real PostgreSQL interleavings and commit-boundary fault injection |
| `observability`                | Remove unreachable public replay label                                                                           | Bounded labels, failure containment               | independent app/metric inventories        | existing equality witness plus explicit expected set              |
| persistence test fixtures      | Extract reused factories without behavior change                                                                 | Existing scenario inputs and oracles              | old and new test modules                  | diff parity and GitHub persistence suite                          |
| documentation/proof routes     | Record the amendment and exact witness paths                                                                     | Historical design bytes                           | current module, index, requirement routes | documentation graph and Proofkit                                  |

## Ordered Work

1. Add the design and this plan; update the current module contract and index.
2. Narrow `SnapshotRecordResult` to collection outcomes. Give the internal
   evidence method its own two-value return annotation. Return `captured`
   internally after a successful holder CAS; retain rollback on lost authority.
   Bind the public success theorem to `TransactionalCiEconomicsStore` after
   commit and required cleanup, not to its private repositories.
3. Remove `replayed` from the application and metric inventories. Keep their
   dependency direction and test their exact six-value public set.
4. Move the existing subject, snapshot, store, database-clock, and terminal
   reconciliation factories into one private shared integration-test helper.
   Retain their bodies and literal fixture identities.
5. Add PostgreSQL witnesses for independent-transaction semantic replay and
   conflict; concurrent duplicate completion; expired-holder-first locking;
   and reclaimer-first locking with database-observed blocking. Confirm that
   a newly acquired claim can subsequently capture as a non-vacuous control.
   Own concurrent actors with `TaskGroup` before disposing engines. Inject
   pre-commit rejection and post-commit acknowledgement failure through the
   public adapter, then inspect committed rows and repeat the same claim.
6. Bind the new helper and test module to `REQ-CI-RUNTIME-038`, including the
   PostgreSQL command; add the design and plan to the same requirement.
7. Inspect all changed routes as structured owner-keyed records and run local
   lint, no-emit typechecking, imports, JSON/docs, requirements, and selective
   planning. Run behavioral witnesses only through GitHub.
8. Rebase the unpublished branch onto the merged thin-consumer batch, then
   publish one PR. Obtain a bounded independent review and green required
   checks for the exact provider head before squash merge.

## Test Obligations

| Scenario                                  | Independent oracle                                                                                                                                    |
|-------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------|
| Same evidence in a later transaction      | Internal replay; original header and child rows unchanged                                                                                             |
| Same identity with changed provider facts | Conflict; persisted original remains readable                                                                                                         |
| Two independent completions               | Exactly one captured and one claim_lost; one complete snapshot                                                                                        |
| Commit boundary failure                   | No public captured result; pre-commit failure rolls back, post-commit uncertainty retains both effects; repeat observes the corresponding claim state |
| Expired holder locks first                | Reclaimer observes no claim while locked; old holder cannot persist                                                                                   |
| Reclaimer locks first                     | PostgreSQL reports the stale holder blocked by the reclaimer PID; after commit old holder loses                                                       |
| New generation                            | Revision, generation, attempt, owner, token advance; successor capture succeeds                                                                       |
| Metric algebra                            | Exact six collection outcomes; equal app and metric inventories; unknown maps to other                                                                |
| Existing retention chain                  | Capture through purge and non-resurrection still passes                                                                                               |

No production database migration or historical design edit is required.
Rollback is an ordinary additive revert of this result projection and its new
witnesses; it does not alter persisted schema or retention deadlines.
