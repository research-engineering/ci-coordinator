# Webhook Ingress Scheduling

Status: reviewed design; source delivered, native witnesses and exact-source
qualification pending (CI-014 open); connection-share enforcement not adopted
Date: 2026-10-11

Owners: [GitHub ingestion](../architecture/modules/github-ingestion.md)
(acceptance contract), [runtime composition](../architecture/modules/runtime-composition.md)
(verifier pool, background populations and drain), [HTTP API](../architecture/modules/api-http.md)
(pre-authentication budgets and bulkheads), [persistence](../architecture/modules/persistence.md)
(compact inbox pattern), [observability](../architecture/modules/observability.md)
(outcome series) and [capacity qualification](../architecture/modules/capacity-qualification.md)
(budget identity consumed by CI-069).
Delivery: [implementation plan](webhook-ingress-scheduling-plan.md).
The [roadmap](../../ROADMAP.md) remains the sole execution register: CI-014
owns source and native behavior, CI-069 owns live capacity qualification, and
CI-059/CI-062 own edge admission and the deployed environment.

## Scope And Authority

This document satisfies the design-first clause of the roadmap section
"Webhook Concurrency And Asynchronous Processing". It closes no clause by
itself. No source, schema, wire or requirement changes with it. It extends,
and does not replace, the
[webhook recovery boundaries](webhook-recovery-boundaries.md), the archive
inbox in [Actions history storage](actions-history-storage.md#selected-delivery-design),
[history worker progress](history-worker-progress.md) and the
[module contract](../architecture/modules/github-ingestion.md).

All coordinates were validated at
`62d7765d32f6bbb19848b1bba319b71bf9c379f9`. Paths below are relative to
`backend/src/ci_coordinator/` unless stated otherwise.

## Source Delivery

The implementation delivers plan steps 1 to 5 and 7 to 9 in source:

- the verifier pool `N = 4` in `runtime/webhook_admission.py`;
- the webhook pre-body stage and the 8-second deadline policy in
  `api/http/routers/github_webhooks.py`, through a generic pre-body hook in
  `api/http/body_limits.py`;
- the recheck lane rotation;
- the back-channel logout envelope;
- the closed outcome, refusal and permit-hold series.

The coordinates in the as-is analysis below describe the base before this
delivery.

Step 6 is not adopted as designed. Counted from source, the lifecycle-owned
background populations hold `B = 22` concurrent connections. The maintenance
round runs reconciliation and all nine bounded operations concurrently:

- reconciliation 1;
- economics collection 4;
- archive delivery 4;
- observation discovery 2;
- six single-connection cleanups.

The history lanes hold 4 and preparation 1. With `N = 4` and `F_min = 4`,
`N + B + F_min = 30`, so the declared pool of 16 cannot satisfy the sum, and
a startup rejection would refuse the example deployment. This is a
provisional owner decision (2026-10-11):

- no startup rejection, no new pool and no separate engine is added;
- `runtime/connection_shares.py` records the source-counted inventory, and
  a witness pins it;
- foreground contention under background and webhook load remains a recorded
  residual. It is the "foreground pool wait rises" revision trigger below,
  measured by CI-069.

Raising the pool, bounding maintenance concurrency or a separate engine each
need a new owner decision.

## As-Is Analysis

### Correction Of The Scoping Premise

The scoping report for this task stated that no inbox exists. That is false.
A compact PostgreSQL inbox already exists and is atomic with acknowledgement:

- table `ci_history_delivery_inbox` (`persistence/_schema_ci_history_delivery.py:15-65`);
- producer `ensure_history_delivery_inbox` (`persistence/ci_history_delivery_ingress.py:18-43`),
  called inside `_PostgresWebhookIngestionRepository.commit`
  (`persistence/runtime_delivery_repository.py:53`, calls at `:195`, `:209`, `:224`);
- the store commits the whole unit of work before it reports a claim
  (`persistence/runtime_adapters.py:173-192`);
- in-process consumer `CiHistoryDeliveryService` (`app/ci_history_delivery.py:10-13`):
  16 scopes per round, 4 workers, 10 seconds per scope, 50 seconds per round,
  100 rows per page (`persistence/ci_history_delivery_transfer.py:32`). It runs as
  a bounded maintenance operation (`runtime/composition.py:513`);
- pending age is already exported (`observability/runtime_metrics.py:532-536`).

A completed `workflow_run` with a usable history hint therefore commits the
claim, its audit event, the observation and the inbox row in one transaction.
The T10 loss in the [temporal audit](../adoption/temporal-and-oracle-audit-2026-09-06.md)
is caused by the process gate described below, not by a missing inbox.

### Validated Facts

| Clause | Status | Evidence |
| --- | --- | --- |
| Fair processing across installations/repositories | Missing at ingress; present for archive work | One process-global gate `_PROCESS_ADMISSION_GATE = Lock()` (`runtime/webhook_admission.py:32`). A non-blocking acquire returns 503 (`:75-76`) and the worker limiter is `CapacityLimiter(1)` (`:71`). The archive inbox consumer rotates scopes cyclically from a process cursor (`app/ci_history_delivery.py:29`, `:50`, `:84-88`). Backfill and discovery claims order one scan row per scope by due time (`persistence/ci_history_claims.py:59`). Recent and repair recheck claims order scopes by their oldest due row and take one row (`persistence/ci_history_recheck_claims.py:48-65`, `:88-92`), so they are not repository-fair. All use per-scope try-locks. |
| Gate scope | Verification only | The gate covers HMAC verification, parsing and normalization in the worker process (`runtime/webhook_admission.py:79-88`). It is released (`:91-92`) before `complete_webhook_ingestion` commits (`:98`). |
| Recent versus history work | Partial | Four single-flight history lanes (`runtime/history_collection_worker.py:43-45`, `:62-99`). Webhooks, maintenance, lanes and UI share one engine (`runtime/composition.py:350-354`). |
| Foreground protection | Partial | Per-route bulkheads exist for configuration, economics, history, Activity, readiness and discovery (`api/http/app.py:426-525`). The webhook policy is derived from its body-limit policy and has no `concurrency_limit` (`:455-464`). Uvicorn admits 128 concurrent requests per process (`runtime/__main__.py:14`, `:55`). |
| Bytes | Per request and per process | Body limit 25 MiB (`github_ingestion/resources/webhook-ingestion-profile.v2.json:10`). Eight concurrent bodies per path and a growing retained-body budget, default 32 MiB, shared by every body route (`api/http/body_limits.py:20-21`, `:80-83`, `:100-109`). |
| CPU/RSS | Partial | Verification and parsing run in a cancellable AnyIO worker process (`runtime/webhook_admission.py:79-88`). No CPU or RSS budget is declared. The capacity profile already names `webhook-normalization-cpu-p999-microseconds`, `webhook-parent-serialization-p999-microseconds` and `process-resident-memory-bytes` (`capacity_qualification/resources/capacity-qualification-profile.v1.json`). |
| Connections | Partial | Admitted pool size and timeout (`runtime_settings/admission.py:393-397`); the example deployment uses 16 connections and 5 seconds (`.env.example:11-12`). There is no per-class share. Session lookups admit 16, not 32 (`control_plane_identity/browser.py:37`, `:198`). |
| Global commit serialization | Present, intentional | Every webhook commit appends to the audit ledger under the head-row lock (`persistence/audit_repository.py:150`). The lock is held until COMMIT. Its cost is the T12 measurement obligation. |
| Queue age | Partial | Archive inbox page age exists. The preparation queue is in memory (`runtime/planning_preparation.py:12-26`) and has outcome counters only. No webhook admission outcome series exists; the HTTP counter only separates status classes (`observability/runtime_metrics.py:638-640`). |
| Provider limits | Partial | Rate-limit evidence is projected per adapter (`integrations/github/_rate_limits.py`). There is no all-replica provider budget. |
| Cancellation | Present (local) | `cancellable=True` (`runtime/webhook_admission.py:86`), request deadline middleware, history item deadline (`runtime/history_collection_worker.py:13`, `:78-85`). |
| Retry/poison | Partial | Authenticated malformed bodies return 400 before any claim (`github_ingestion/use_cases.py:73-109`, `api/http/routers/github_webhooks.py:205-215`). GitHub does not redeliver automatically. Recovery is operator redelivery ([recovery boundaries](webhook-recovery-boundaries.md#detection-and-recovery)). |
| Drain | Present (process) | Shutdown partitions ([runtime composition](../architecture/modules/runtime-composition.md)). The preparation queue is discarded on close (`runtime/planning_preparation.py:74-84`). Archive work survives in PostgreSQL. |
| Durable acceptance before acknowledgement | Present | A processed response requires the committed claim (`github_ingestion/use_cases.py:129-138`) and maps to `WebhookProcessedResponse` (`api/http/routers/github_webhooks.py:70-75`, `:169-187`). |
| Pre-authentication threat budget | Missing | Each identity path has one process-local bucket, burst 16 at 4 per second (`api/http/routers/control_plane_identity.py:146-159`). It is consumed before any verification (`api/http/public_request_limits.py:75`, `:77-86`, `:100-115`). The middleware order puts it outside request admission and body limits (`api/http/app.py:301-325`; the last added middleware is outermost). The webhook route has no pre-authentication limiter. |

Two further facts constrain the design:

- GitHub expects a response within 10 seconds of delivery
  ([best practices](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks)).
  The example request deadline is 15 seconds (`.env.example:9`). A commit that
  completes between 10 and 15 seconds is durable but recorded by GitHub as
  failed; redelivery is then an exact duplicate.
- A Uvicorn concurrency rejection happens before application instrumentation,
  so the existing delivery alert cannot observe it.

## Post-Acknowledgement Consumer Inventory

`ProcessedAck => DurableWork(c)` must hold for every consumer `c` that is not
best-effort by an explicit decision. Each current consumer is listed below.

| Delivery | Consumer | Durable form at acknowledgement | Coordinates |
| --- | --- | --- | --- |
| Every admitted delivery | Idempotency and audit | Delivery claim and audit event in the acknowledging transaction | `persistence/runtime_delivery_repository.py:53-165` |
| Completed `workflow_run` with history hint | Archive transfer | Observation and compact inbox row in the same transaction | `persistence/ci_history_delivery_ingress.py:18-43`; `app/ci_history_delivery.py` |
| Completed `workflow_run` and `workflow_job` | Economics evidence joined with REST snapshots | Retained observation row in the acknowledging transaction, read on demand; no hand-off needed | `persistence/ci_economics_repository.py:375-387`; `REQ-CI-RUNTIME-038` |
| `push`, admitted `pull_request`, `merge_group` seed | Planning context preparation | Best-effort by explicit decision `REQ-CI-RUNTIME-041`; the wire reports `best_effort_preparation` | `runtime/planning_preparation.py:36-54`; `api/http/routers/github_webhooks.py:181-185` |
| Non-completed `workflow_run`, ping, no-op, unsupported | None | Claim only | `github_ingestion/use_cases.py:139-146` |

Any future consumer that needs work after acknowledgement must add its own
compact row in the acknowledging transaction before an acknowledgement can
promise that work. A process-local queue may serve it only as an explicitly
labelled best-effort path.

## Provisional Owner Decisions

Each item is a provisional owner decision (2026-10-11). Independent review may
revise it; the falsifiers below reopen it.

1. **PostgreSQL inbox in the same process.** The concrete form is the existing
   per-consumer compact-inbox pattern. No new raw-payload inbox is added. The
   compact rows are committed in the acknowledging transaction and drained by
   lifecycle-owned in-process workers.
2. **Declared conservative budgets.** The budgets below are declared and
   unmeasured until CI-069 measures them. They are design inputs, not capacity
   claims.
3. **Bounded verifier pool with no queue.** The pool replaces the
   process-global gate. A separate pre-authentication limiter guards it.
4. **Separate budget per public route class, with cheap pre-verification.**
   An edge filter is recorded as a deployment dependency owned by
   CI-059/CI-062. Cheap webhook pre-verification changes the error
   precedence only for requests with several defects; the change is
   admitted below.
5. **Seed preparation stays best-effort.** `REQ-CI-RUNTIME-041` is unchanged.

## Alternatives

| Alternative | Cost | Disposition and falsifier |
| --- | --- | --- |
| Status quo with operator redelivery | None in source. Every concurrent delivery during one verification receives 503 and stays lost unless REST recovery or an operator restores it. | Rejected. Under the declared burst it refuses most deliveries, and the roadmap requires bounded fair processing. |
| Compact PostgreSQL inbox per consumer, in-process workers, verifier pool | Pool processes and RSS; one static connection-share check; new metrics. No schema change in the first source delivery. | Selected. Falsifier: at the declared arrival rate, a measured parse/normalize share of acknowledgement latency prevents pool `N` from meeting the blocking target `epsilon`. |
| Verified-envelope inbox: store verified raw bytes and acknowledge before parsing | Breaks the sequencing law that malformed bytes never reserve a delivery key. Turns synchronous 400 and ignored results into poison or terminal inbox states. Adds raw-payload retention and privacy duties (CI-010/CI-011) and up to 25 MiB per row. | Rejected. Revision trigger: the selected falsifier fires and an owner admits the wire, retention and sequencing changes. |
| Separate worker deployment on the same PostgreSQL | A second deployable, its credentials, rollout and drain; the same commit boundary. | Deferred. Revisit when measured CPU or RSS for background work must be isolated from the HTTP process or scaled independently. |
| External broker | A new durability domain, a database-to-broker hand-off, operations and custody. | Rejected without measurements. Revisit only when the audit head, inbox or database is the measured limiting resource and PostgreSQL cannot drain within the queue-age budget. |
| Bounded waiting queue with deadline in front of the verifiers | Memory for waiting bodies; deadline coupling with the provider's 10-second limit. | Deferred. Revision trigger: measured simultaneous arrivals exceed `N` often enough that blocking exceeds `epsilon` while the CPU budget is unused. |

The comparison is bounded to these alternatives and makes no global-optimum
claim. The selected trajectory is the simplest one that removes the observed
loss cause while preserving the protected observables.

## Verifier Pool: Successor To The Single-Worker Clause

`REQ-CI-RUNTIME-008` currently states that webhook admission executes in at
most one no-queue cancellable worker process per backend process. The
implementation delivery replaces only that webhook clause.

```text
WebhookAdmissionPermit := NoQueueAdmission(N), owned by one ProcessWebhookAdmission
permit acquired  before verification
permit released  after the commit result (claimed, duplicate, conflict, unavailable)
worker processes <= N, each cancellable
busy permit      -> 503 {"ok": false, "error": "webhook ingestion unavailable"}
broken worker    -> 503, the same body; other permits are unaffected
```

The saturation response keeps today's status and body. No 429 is used, so the
existing `CIWebhookDeliveryUnavailable` alert still counts every refusal.
Holding the permit through the commit bounds webhook database connections by
`N` without a second mechanism. The audit-head lock still serializes commits.

`N` comes from the CPU and RSS budgets. Treat the pool as an Erlang-B loss
system with offered load `a = lambda * s`, where `lambda` is the declared arrival
rate and `s` the per-delivery hold time:

```text
N = min { n >= 2 : ErlangB(n, a) <= epsilon }
subject to  N <= cpu_cores_for_webhook_verification
            N * worker_base_rss + amplification * retained_body_budget <= webhook_rss_share
            N <= MAX_CONCURRENT_BODY_REQUESTS (8)
```

With the declared `lambda = 25/s`, `s = 20 ms` and `epsilon = 0.2%`,
`a = 0.5`. Then `ErlangB(3, 0.5) = 1.27%` and `ErlangB(4, 0.5) = 0.16%`, so the
declared `N = 4`. Erlang-B assumes independent arrivals. Correlated job bursts
violate that assumption, so measured simultaneous arrivals are the falsifier.
If the constraints cannot hold with `N >= 2`, startup admission rejects the
configuration rather than silently falling back to one worker.

## Pre-Authentication Budgets Per Route Class

The F-06 adjudication, recorded in the CI-014 and CI-069 roadmap rows, found
that invalid traffic can keep the back-channel logout bucket empty. Valid
Keycloak deliveries then receive 429, and revocation waits for session expiry.
The same structure applies to every public pre-authentication path. Source code cannot fully separate forged from
valid traffic on the same path. Before a request is read, nothing
distinguishes them. After it is read, only the credential check does, and
that check is the work the budget protects. Each route class therefore gets
its own budget, the work admitted before a credential check is made cheap,
and scarce resources move behind that check.

| Route class | Pre-body budget | Cheap pre-verification | Behind the credential check |
| --- | --- | --- | --- |
| GitHub webhook (new) | Count bucket and declared-byte bucket, charged only after the header checks. An unknown length is charged the 25 MiB maximum. Rejection is 503 `webhook ingestion overloaded`, existing wire. | Before the path lease and the body read: declared content length (400/413, unchanged and still first), then content type and encoding (400), then exactly one well-formed `X-Hub-Signature-256` header (401). A request with one defect keeps today's status and body. The order across several defects changes as admitted below. | Verifier permit, parsing, database commit |
| Back-channel logout | One envelope bucket sized from the cost of a bounded body read plus in-process verification; replaces the 16/4 bucket | Existing header shape, algorithm and type checks precede key lookup. Unknown-key lookups stay inside the existing [Keycloak refresh failure budget](keycloak-refresh-failure-budget.md). | Fence lock and session deletion |
| Login start | Existing separate 16/4 bucket | None possible: there is no credential | Fence anchor read |
| Login callback | Existing separate 16/4 bucket | The sealed transaction cookie is opened before the provider code exchange (`control_plane_identity/browser.py:257-259`) | Code exchange, session insert |

**Admitted webhook error-precedence change.** This is part of provisional
owner decision 4 (2026-10-11). Today the body middleware decides
declared-length defects first (`api/http/body_limits.py:90-99`). It then
decides path or retained-body overload (`:100-109`) and defects found while
reading the body (`:119-127`, `:195-201`). Only then does the router check
content type (`api/http/routers/github_webhooks.py:139-140`). The signature
is checked last, after the process gate and inside the worker
(`runtime/webhook_admission.py:75-88`). The new stage decides header defects
without a lease and without reading the body. So a header defect now takes
precedence over every defect found by taking a lease, reading the body or
taking the verifier permit:

| Combined defects | Today | After |
| --- | --- | --- |
| Unknown length, body over 25 MiB, no signature header | 413 `request body too large` | 401 `invalid signature` |
| Declared length 10, 12 body bytes, no signature header | 400 `invalid webhook` | 401 `invalid signature` |
| Unknown length, body over 25 MiB, form content type | 413 `request body too large` | 400 `invalid webhook` |
| Eight bodies already in flight on the path, no signature header | 503 `webhook ingestion overloaded` | 401 `invalid signature` |
| Verifier busy, no signature header | 503 `webhook ingestion unavailable` | 401 `invalid signature` |
| Declared length over 25 MiB, no signature header | 413 `request body too large` | Unchanged |
| Malformed `Content-Length`, form content type | 400 `invalid webhook` | Unchanged |

Keeping today's precedence would mean reading or leasing the body before
the header checks, which is the cost this stage exists to remove. A GitHub
delivery always carries a declared length and a signature header, and its
content type is fixed by the hook configuration. Only requests with several
defects at once change status. No wire schema changes. Falsifier: a
consumer whose recovery depends on the body-defect status of a request that
also lacks a valid signature header or content type.

The admitted threat budget, per replica, is:

```text
ValidLogoutDelivered requires
  invalid_rate + valid_rate <= envelope_refill
  and concurrent_slow_bodies < MAX_CONCURRENT_BODY_REQUESTS
  and uvicorn_slots_available
Otherwise: valid delivery capacity is unclaimed and the edge filter is required.
```

Forged logout tokens never reach the fence lock, the database or a Keycloak
key fetch outside the existing failure budget. Removing the 16/4 bucket also
protects a burst of valid back-channel calls, for example after an
administrator-wide logout, from 429s whose Keycloak retry behavior is not
claimed. Slow-body and process-wide connection exhaustion are ingress-wide
abuse domains. The
network-level discriminator is a deployment dependency: GitHub hook source
ranges ([meta API](https://docs.github.com/en/rest/meta/meta)), a private
Keycloak back-channel path and proxy request buffering. CI-059 and CI-062 own
it. Process-local buckets multiply with the replica count; no replica-wide
budget is claimed.

## Fairness And Resource Shares

The fairness unit depends on where the work runs.

- **Ingress before verification cannot be scope-fair.** The installation and
  repository come from the authenticated payload. Deriving a scheduling key
  from unverified bytes would make scheduling caller-controlled. Ingress is
  therefore bounded per route class and by the pool, not per scope.
- **Post-acknowledgement work is repository round-robin.** Every compact-inbox
  consumer reuses the archive pattern:
  - it lists the bounded scope population;
  - it visits scopes cyclically from a process cursor that advances before each
    attempt;
  - each scope gets a fixed quantum of rows and time;
  - a busy scope is skipped (try-lock, `SKIP LOCKED`) without consuming
    another scope's turn;
  - rows inside a scope are taken in one stable indexed order (the archive
    uses pending generation, then delivery ID).

  For a stable population of `S` scopes, `S` consecutive admissions visit
  every scope. This is plain round-robin with a fixed quantum and no deficit
  carry-over, not weighted fairness.
- **Backfill and discovery lanes keep due-time order** over one scan row per
  scope and lane, `(next_attempt_at, installation_id, repository_id)`, with
  scope try-locks (`persistence/ci_history_claims.py:35-72`). A claim advances
  that scope's single due time, so for scopes due at the same time this is a
  repository cycle.
- **Recent and repair recheck lanes are not repository-fair today.** They
  order scopes by their oldest due row (`MIN(next_attempt_at)`,
  `persistence/ci_history_recheck_claims.py:48-65`) and claim only that row
  (`:88-92`, `:120-126`). Counterexample: rows A1 and A2 of repository A are
  both due before row B1 of repository B. The claims then run A1, A2, B1,
  and a repository with a large due backlog delays every other repository
  by its backlog length. The implementation adopts the archive rotation for
  these lanes without a schema change:
  - the collection service keeps one process cursor per recheck lane, the
    last claimed scope key;
  - the claim rotates the admitted due-scope candidate list, which is
    already fully listed and bounded by `MAX_HISTORY_DATASETS`, to start
    after that key;
  - inside a scope, rows keep today's `(next_attempt_at, workflow_run_id)`
    order.

  For the counterexample the claims run A1, B1, A2. A scope with due work
  waits at most `S - 1` claims of its lane in one process. The cost is that
  a globally older row in another scope may wait up to `S - 1` claims
  behind younger rows. Cursors are process-local, so replicas rotate
  independently and no all-replica order is claimed.
- **All history lanes are repository-weighted**, so an installation with
  more repositories gets more turns. Installation-token provider budgets are per installation and
  bound that effect at the provider. All-replica App-level limits and
  credential refresh are shared resources. They are modelled in the
  implementation as one budget, not as one budget per process.
- **Connection shares by construction.** No new pool is added. Startup admits
  `N + B + F_min <= database_pool_size`:
  - `N` is the webhook permit, held through the commit;
  - `B` is the sum of every lifecycle-owned background population's maximum
    concurrent connections, counted from source in the implementation;
  - `F_min` is the declared foreground reserve.

  A background population whose connection use per item is not bounded blocks
  this check until it is.

## Declared Budgets

Every value is declared and unmeasured until CI-069 measures it. Changing a
value is a design revision, not a tuning detail.

| Budget | Declared value | Source or derivation | CI-069 measurement |
| --- | --- | --- | --- |
| Arrival peak | 25 deliveries per second sustained for 60 seconds; bursts of 100 within one second | Minimum scenario of eight developers and two repositories: per push about `2 + 3W + 4J` deliveries with assumed `W = 4` workflows and `J = 20` jobs | Per-route arrival series, burst histogram, simultaneity |
| Payload size | p99 at most 128 KiB; maximum 25 MiB | Maximum from the provider cap in the ingestion profile; p99 assumed from typical job/run/push payloads | Body-size histogram per event family |
| Acknowledgement latency | p99 at most 2 seconds; local hard deadline 8 seconds (work deadline 7.2 seconds) | Provider 10-second delivery limit, minus 2 seconds for transfer outside the process | `request-latency-p999-microseconds` for the webhook route; provider-observed duration |
| Verifier pool | `N = 4`, blocking `epsilon <= 0.2%` at the declared load | Erlang-B derivation above, `s = 20 ms` assumed | Permit refusals, hold-time distribution |
| Verifier RSS | `N * 96 MiB + 10 * 32 MiB`, at most 704 MiB | Assumed worker base RSS and parse amplification over the retained-body budget | `process-resident-memory-bytes` with workers |
| Retained body bytes | 32 MiB per process; eight bodies per path | Existing settings and middleware | `retained-request-body-bytes` |
| Webhook pre-auth budget | Count burst 200, refill 50 per second; bytes burst 32 MiB, refill 8 MiB per second | Twice the declared arrival peak; bytes bounded by the retained-body budget | Refusals by route class |
| Logout envelope budget | Burst 64, refill 32 per second | Cheap verification cost; above the current 4 per second | Valid logout success under invalid load |
| Connection shares | Pool 16: `N = 4`, `F_min = 4`, `B = 22` counted from source; the sum 30 exceeds 16 and is not enforced (see Source Delivery) | Example deployment pool size | `database-pool-wait-p999-microseconds` per class |
| Archive inbox drain | At most 1,600 sources per round (16 scopes of 100 rows); one round per reconciliation interval (example 30 seconds) | Existing constants | Pending age p99 at most 5 minutes; backlog growth |
| Queue bytes | No raw-payload queue; compact rows only; 32 in-memory preparation epochs | Selected decision 1; `REQ-CI-RUNTIME-041` | Inbox row and index size growth |

## Webhook Response Deadline

The webhook route gets its own deadline through the existing
`RequestAdmissionPolicy.timeout_seconds`
(`api/http/request_admission.py:27`, `:117-123`). Today the webhook policy is
derived from its body-limit policy without a timeout
(`api/http/app.py:455-464`), so the global request deadline applies. That
deadline is 15 seconds in the example deployment (`.env.example:9`). The
selected value is `WEBHOOK_RESPONSE_DEADLINE_SECONDS = 8`. The effective
hard deadline is the smaller of 8 and the global deadline. The middleware's
send reserve, `min(1, 8 / 10)`, puts the work deadline at 7.2 seconds
(`:124`, `:147`). When
the work deadline expires, the existing timeout response is sent: 503
`webhook ingestion unavailable`, the existing wire.

Cancellation at that deadline has these consequences and limits:

- The verifier worker is cancellable and is terminated. Its permit is
  released when the cancelled handler unwinds.
- If COMMIT was already sent when the commit await was cancelled, the
  outcome is ambiguous. The response is still 503, never a processed
  acknowledgement. Redelivery resolves it at the idempotency boundary: a
  duplicate if committed, a claim if not.
- Cleanup is cooperative. Rollback, connection return and worker
  termination run while the handler unwinds and can outlast the deadline.
  If unwinding reaches the hard deadline, the middleware abandons the
  request without a response (`api/http/request_admission.py:171-173`). A permit still held during
  cleanup counts against `N`. Excess load is therefore refused, not
  admitted twice. No cleanup duration bound is claimed beyond the existing
  pool and shutdown timeouts.
- The deadline starts when the application receives the request. Proxy,
  TLS, accept-backlog and network time before that point is outside it. The
  local deadline therefore does not prove that GitHub sees a response
  within 10 seconds; CI-069 measures that.

## Crash-Cut Algebra

Let `D` be the delivery claim, `A` its audit event, `O` the applicable
observation, `I` the applicable compact inbox row, `P` the best-effort
preparation offer and `K` the acknowledgement. Commit `C` writes `D`, `A`, `O`
and `I` atomically.

```text
ProcessedAck => SuccessfulCommit(D and A and Applicable(O) and Applicable(I))
FailedResponse does not imply NotCommitted
DuplicateAck  => an earlier SuccessfulCommit(D and A and Applicable(O) and Applicable(I))
```

| Cut | Durable state | Response | Recovery |
| --- | --- | --- | --- |
| Before verification: pre-auth or permit refusal, cancellation | None | 503 or lost | Refused-delivery owner below |
| Worker death or broken pool | None; other permits continue | 503 | Same |
| Authenticated but malformed body (poison) | None; no key reserved | 400 | Terminal. Redelivery returns the same 400. No inbox poison state exists. |
| Before COMMIT | None, after rollback | 503 | Refused-delivery owner |
| Ambiguous COMMIT | Unknown | 503 | Redelivery reaches the same idempotency boundary: a duplicate if committed, a claim if not. Same GUID with a different body is 409. |
| After COMMIT, before acknowledgement (crash, webhook deadline, or more than 10 seconds at the provider) | Committed | Lost, 503 at the webhook deadline, or late | Redelivery is an exact duplicate; the duplicate path re-ensures `I` (`persistence/runtime_delivery_repository.py:195`, `:224`). Work is already pending. |
| After acknowledgement, preparation offer lost | Committed; `P` lost | 200 `best_effort_preparation` | None by decision 5; the request-time planning path is unchanged |
| Inbox consumer stale or killed | Rows pending | Not applicable | The next round re-admits under the scope lock and dataset generation. A delivered generation fences reprocessing. An erased dataset cannot be recreated. |
| Source expiry | The inbox row cascades with its observation (90 days) | Not applicable | The existing bounded expired-pending sample records it; this is not loss accounting |

Payload retention: no raw body is retained after the request. The body exists
only in process memory under its body lease. Guarded replay is provider
redelivery only, through the same signature, body identity and idempotency
boundary. No local replay exists because no raw payload is stored.
Automatic redelivery stays deferred under the recovery-boundaries decision
until it joins the provider-effect lifecycle `Reserved -> Uncertain ->
Reconciled/Reissued` with generation fencing.

## Refused-Delivery Recovery Owner

GitHub does not redeliver automatically. Every refusal therefore needs an
owner.

| Lost delivery | Recovery owner | Limit |
| --- | --- | --- |
| Completed `workflow_run` in a selected history dataset | Recent and discovery lanes find the run through bounded REST polling ([recent recovery](actions-history-recent-recovery.md)) | Bounded recent window; webhook timing may be partial |
| Completed `workflow_job` for a registered subject | The economics collector reads stable REST job snapshots | Missing webhook timing stays partial or unknown, never zero |
| Seed | No consumer requires it; planning uses the request-time path | None |
| Anything else, or work outside the windows above | Operator redelivery after the `CIWebhookDeliveryUnavailable` alert ([procedure](../how-to/recover-webhook-deliveries.md)) | Provider redelivery window of three days; Uvicorn-level refusals are invisible to the alert |

Falsifier: a refused delivery whose effect a consumer requires, that no REST
lane reconstructs and that was not redelivered within the provider window.
Such a case reopens automatic redelivery under the recovery-boundaries
revision conditions.

## Protected Observables

- `ProcessedAck => SuccessfulCommit` as stated above.
- Exact duplicate, body-alias and conflict (409) semantics.
- The wire schemas of `WebhookProcessedResponse`, `WebhookIgnoredResponse` and
  `WebhookErrorResponse`, including the `downstream` enum. Saturation and
  pre-authentication refusal reuse existing 503 bodies.
- Signature verification over the exact bytes before any parse.
- The sequencing law of the module contract: malformed or oversized bytes never
  reserve a delivery key.
- `REQ-CI-RUNTIME-041` preparation bounds and labels.
- `REQ-CI-RUNTIME-054` lanes and the archive transfer contract.
- The shutdown budget partitions.
- No new framework, broker or deployable.

## Falsifiers And Revision Triggers

Reopen this design when any of the following is observed or proposed:

- A measured parse/normalize share of acknowledgement latency means pool `N`
  cannot meet `epsilon` at the declared load (revisits the verified-envelope
  inbox).
- Measured simultaneous arrivals cause blocking above `epsilon` while CPU is
  available (revisits the bounded waiting queue).
- Measured audit-head wait dominates acknowledgement latency (T12; revisits
  commit structure, not the broker).
- Archive pending age or backlog grows under the declared arrivals (revisits
  the drain quantum or a dedicated worker).
- Foreground pool wait rises with webhook or background load, which falsifies
  the connection-share sum.
- A new post-acknowledgement consumer is proposed without a compact row in
  the acknowledging transaction.
- Valid back-channel logout fails while invalid traffic stays within the
  declared envelope budget.
- Provider-observed delivery durations exceed 10 seconds while the local
  webhook deadline holds (revisits the deadline value or edge buffering).
- A recheck scope with due work waits more than `S - 1` claims of its lane
  in one process.
- The provider's response deadline, payload cap or redelivery window changes.
- A webhook becomes required planning or authorization input.

## Non-Claims

This design proves no capacity, fairness under restart, replica-wide abuse
control, provider behavior or deployment readiness. The declared budgets are
not measurements. Erlang-B figures are a sizing heuristic under an independence
assumption. Native CI can prove the gate, budget and fairness logic with
deterministic schedulers and PostgreSQL races. It cannot prove capacity;
CI-069 owns that.
