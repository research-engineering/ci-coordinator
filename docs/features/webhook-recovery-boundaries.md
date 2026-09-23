# Webhook Recovery Boundaries

Status: operational contract refinement

Date: 2026-09-06

## Decision

Use explicit operator redelivery for failed deliveries in the current mode,
with a bounded failure alert and consumer-specific closeout. Do not add an
autonomous replay service before its effects and recovery authority are owned.
This is not a guarantee of complete event history or automatic recovery.

This refinement owns delivery recovery and its alert, not webhook admission,
planning, reconciliation, or economics definitions. Those remain with
[ingestion](../architecture/modules/github-ingestion.md),
[reconciliation](../architecture/modules/reconciliation.md), and
[CI economics](../architecture/modules/ci-economics.md).
The [implementation plan](webhook-recovery-boundaries-implementation-plan.md)
and [operator procedure](../how-to/recover-webhook-deliveries.md) have separate roles.

## Current Consumer Matrix

| Event or consumer                                         | Durable effect / recovery                                                                             | Limit                                                                                                                  |
|-----------------------------------------------------------|-------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------|
| `push`, admitted `pull_request` and `merge_group` actions | Delivery identity; normalized seeds only classify the synchronous response.                           | No downstream seed queue or automatic pre-CI plan is created.                                                          |
| Non-completed `workflow_run` actions                      | Delivery identity and synchronous classification.                                                     | No retained execution timeline is promised.                                                                            |
| Completed `workflow_run` and `workflow_job`               | Delivery identity and normalized observation commit together.                                         | A rejected request can leave no observation. Raw request payloads are not an inbox.                                    |
| Already registered reconciliation subject                 | REST polling checks its exact run and attempt within its retained lease, deadline and attempt budget. | It does not discover every unregistered workflow or recover every historical event.                                    |
| Economics for a terminal reconciliation subject           | The collector registers from durable reconciliation subjects and reads stable REST job snapshots.     | Missing job webhook timing can leave queue and attempt-wall values partial or unknown. REST occupancy is not CPU time. |

The source authorities are `complete_webhook_ingestion`,
`DurableReconciliationRegistrar`, `register_eligible` in the collection
repository, and `derive_attempt_economics`. In particular, economics collection
registration does not depend on receipt of a completed webhook.

## Conditional Safety Argument

Let `D` be the canonical durable body claim, `A` its audit claim, `O` its
applicable observation, and `S` an exact registered execution subject.

```text
ProcessedAck => SuccessfulCommit(D and A and Applicable(O))
FailedResponse does not imply NotCommitted
DeliveryAck does not imply QueuedPlan
SelectedIssuance => DurableRegistration(S)
WebhookAbsent does not imply ProviderSuccess(S)
MissingTimingEvidence => PartialOrUnknownTiming, not InventedZero
```

The first implication applies to processed or exact-duplicate responses, not
every HTTP 2xx response: an ignored event is not evidence for a consumer. A
repeated body under another unsigned delivery GUID can use its existing
canonical claim; it does not promise a row for every supplied GUID. The commit
claim is not a promise of indefinite retention. A timeout can occur after
commit. Redelivery therefore passes through the existing signature,
body-identity and durable idempotency boundary; operators must not delete
deduplication rows or edit payload bytes to obtain another effect.

The [transactional adapter](../../backend/src/ci_coordinator/persistence/runtime_adapters.py)
returns a successful claim only after committing its unit of work. That
[unit of work](../../backend/src/ci_coordinator/persistence/webhook_ingestion_unit_of_work.py)
binds the delivery, audit and observation operations to one connection.
The [repository](../../backend/src/ci_coordinator/persistence/runtime_delivery_repository.py)
requires the complete pair and marks incomplete writes for rollback; the
[base transaction](../../backend/src/ci_coordinator/persistence/unit_of_work.py)
preserves uncertain commit and post-commit cleanup failures as failures, not
proof of non-commit. The existing
[PostgreSQL witnesses](../../backend/tests/integration/persistence/test_runtime_delivery_state.py)
cover committed observation/audit replay, body aliases and rollback on audit
conflict. A protocol signature alone would not prove these properties.

The registration and REST path explain why a lost webhook does not itself
authorize omission of a required check. They do not prove end-to-end fallback,
future seed consumers, provider availability, or complete telemetry. Those
require their own exact-subject evidence.

## Detection And Recovery

GitHub does not automatically retry failed webhook deliveries. Its documented
redelivery window is the past three days; this is a provider limit, not a local
retention guarantee or operator response objective.
[Failure handling](https://docs.github.com/en/webhooks/using-webhooks/handling-failed-webhook-deliveries),
[redelivery](https://docs.github.com/en/webhooks/testing-and-troubleshooting-webhooks/redelivering-webhooks).

`CIWebhookDeliveryUnavailable` is a warning when the existing HTTP counter
increases over five minutes for exactly `job="ci-coordinator"`, `method="POST"`,
`route="/webhooks/github"`, and `status_class="5xx"`. It has no volume threshold:
one observed failed delivery can matter. A five-minute keep-firing interval
helps diagnosis after traffic stops without creating a durable incident receipt.

The alert observes application responses only. A missing series, network loss,
proxy/Uvicorn rejection before instrumentation, or a lost scrape can conceal a
delivery failure. Existing scrape/readiness alerts and GitHub delivery history
remain necessary. No metric includes payload, delivery, repository, user or
credential identities.

Recovery requires a newly successful provider delivery plus the corresponding
consumer evidence. A redelivery request accepted with HTTP 202 is not delivery
completion. The GitHub App API uses App JWT authority, not an installation
token; the numeric delivery-attempt ID is distinct from the delivery GUID.
[App webhook API](https://docs.github.com/en/rest/apps/webhooks).

If the provider no longer retains an event, preserve the evidence gap. A fresh
workflow execution is a new subject, not a reconstruction of the old one.

## Alternatives And Revision Conditions

| Alternative                                               | Current disposition                                                                                                                                                              |
|-----------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Ignore failed deliveries because REST exists              | Rejected: REST does not restore all timing or undiscovered event history.                                                                                                        |
| Increase the ingress queue indefinitely                   | Rejected: finite memory and provider response deadlines remain; outage recovery is not queue sizing.                                                                             |
| Persist an untrusted raw inbox before signature admission | Rejected: adds a new untrusted storage/secret lifecycle without closing network or pre-ingress loss.                                                                             |
| Automatically request provider redelivery                 | Deferred: useful at scale, but requires bounded enumeration, current App authority, duplicate/uncertain-effect handling and durable progress.                                    |
| Operator redelivery with explicit limits                  | Selected for current mode: reuses provider capability without adding a service or mutating-provider authority. Operator effort and the short recovery window are accepted costs. |

Reopen this decision when a webhook becomes required planning/authorization
input, a consumer requires non-reconstructible events, incident volume or an
admitted recovery objective exceeds manual capacity, autonomous recovery is
promised, or provider retention, App ownership or metric identity changes.
Automatic redelivery must then join the provider-effect lifecycle with
`Reserved -> Uncertain -> Reconciled/Reissued` and generation fencing. A future
seed consumer also needs atomic inbox publication before an acknowledgement
can promise queued work. This decision does not waive review of either change.

## Evidence Boundary

Native promtool fixtures falsify the exact selector operands, zero growth,
absence, reset and recovery. They do not establish deployed scraping, alert
delivery, administrator response time, restored webhook service or a successful
pilot. The current live endpoint remains administrator-deferred.

## Native Proof Prerequisites

The alert changes the byte-bound deployment document inventory. Its normative
and packaged entrypoint disposition must project the new exact source digest;
the exhaustive inventory witness remains unchanged.

The native lease authority witness must cross actual database lease expiry
while holding a row lock. An equal five-second lease and lock timeout allow
the timeout to preempt the intended post-lock observation. The witness alone
sets a 15-second transaction-local lock timeout, retains its five-second lease
and pre-expiry blocking assertion, and keeps the 20-second scenario bound.
Thus `lease < test lock timeout < scenario bound`; production lock, statement
and transaction budgets are unchanged. All four operation modes must still
return claim-lost with no durable effects after the lock is released.
