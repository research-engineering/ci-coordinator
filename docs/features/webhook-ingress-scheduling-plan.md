# Webhook Ingress Scheduling Implementation Plan

Status: implementation and validation plan; source delivered except the step 6
startup check; native witnesses run in GitHub CI; CI-014 open
Date: 2026-10-11

Design: [webhook ingress scheduling](webhook-ingress-scheduling.md).
Normative owners: [GitHub ingestion](../architecture/modules/github-ingestion.md),
[runtime composition](../architecture/modules/runtime-composition.md),
[HTTP API](../architecture/modules/api-http.md) and
[observability](../architecture/modules/observability.md).
Execution remains under CI-014 in the [roadmap](../../ROADMAP.md). Live
capacity qualification remains under CI-069. Edge admission remains under
CI-059 and CI-062.

Implementation starts only after the design passes independent review under
[AGENTS.md](../../AGENTS.md). The provisional owner decisions (2026-10-11) in
the design bind this plan. A change to any of them revises the design first.

## Ordered Work

One source delivery, after design acceptance. Paths are relative to
`backend/src/ci_coordinator/`.

1. **Verifier pool.** In `runtime/webhook_admission.py`:
   - remove the module-global `_PROCESS_ADMISSION_GATE`;
   - give each `ProcessWebhookAdmission` one `NoQueueAdmission(N)` from
     `kernel/admission.py` and a `CapacityLimiter(N)`;
   - hold the permit from verification through `complete_webhook_ingestion`;
   - map refusal and broken workers to the existing `WebhookAdmissionUnavailable`.

   The code-owned constant is `WEBHOOK_VERIFIER_PERMITS = 4`. Composition
   rejects `N < 2` or `N > MAX_CONCURRENT_BODY_REQUESTS`. No settings surface
   is added.
2. **Webhook pre-authentication stage.** In `api/http/body_limits.py` and the
   webhook policy in `api/http/routers/github_webhooks.py`, add a per-policy
   pre-body admission step that runs after the existing content-length checks
   and before the path lease and body read:
   - content type and encoding checks, moved here from the router (400);
   - signature header shape check (401);
   - then the count and declared-byte buckets, with the bucket mechanism of
     `api/http/public_request_limits.py` reused rather than duplicated.

   A refusal returns 503 `webhook ingestion overloaded`. The router keeps its
   checks as defense in depth. Single-defect results are unchanged. Results
   for requests with several defects follow the admitted precedence table
   in the design.
3. **Webhook response deadline.** In `api/http/routers/github_webhooks.py`,
   add `WEBHOOK_RESPONSE_DEADLINE_SECONDS = 8` and one explicit
   `RequestAdmissionPolicy` for the webhook path with that `timeout_seconds`
   and the existing `webhook ingestion unavailable` timeout body. In
   `api/http/app.py`, add it to the explicit policies. The existing
   `config_identities` exclusion (`:454-464`) then drops the derived
   webhook policy that has no timeout. `api/http/request_admission.py` is
   unchanged.
4. **Recheck lane rotation.** In `app/ci_history_collection.py`, keep one
   process cursor (the last claimed scope key) per recheck lane and pass it
   to `claim_recheck`. Extend the port (`ci_economics/history_ports.py:110`)
   and the adapter (`persistence/ci_history_adapters.py:195`). In
   `persistence/ci_history_recheck_claims.py`, rotate the admitted candidate
   list to start after the cursor before trying scope locks. The
   per-scope row order, try-locks, `SKIP LOCKED`, the bound check and
   exhaustion recovery are unchanged. Backfill and discovery claims are
   unchanged.
5. **Back-channel logout envelope.** In `api/http/routers/control_plane_identity.py`,
   resize only the back-channel logout policy to the declared envelope budget.
   Login start and callback keep their buckets. `integrations/keycloak/tokens.py`
   already checks the header shape before key lookup; witnesses pin that order.
6. **Connection-share admission.** In `runtime/composition.py`, enumerate
   every lifecycle-owned background population and its maximum concurrent
   connections. These include the history lanes, the maintenance round with
   its archive delivery workers, reconciliation and preparation. Reject
   composition with a typed code when `N + B + F_min > database_pool_size`,
   where `F_min = 4`. A population without a per-item connection bound blocks
   this step until one is proved.

   Delivered differently, as a provisional owner decision (2026-10-11). The
   counted `B` is 22, so the check would reject the example pool of 16. Only
   the inventory `runtime/connection_shares.py` and its witness
   `unit/runtime/test_connection_shares.py` are delivered. They pin `B = 22`,
   the required pool of 30, rejection at 29 and admission at equality. No
   startup rejection is enforced; see the design's Source Delivery section.
7. **Metrics.** In `observability/runtime_metrics.py`, add:
   - `ci_coordinator_webhook_admission_total{outcome}` with the closed outcomes
     `header_rejected`, `preauth_limited`, `permit_refused`, `worker_unavailable`,
     `signature_rejected`, `body_rejected`, `claimed`, `duplicate`, `conflict`,
     `store_unavailable`;
   - `ci_coordinator_public_request_limited_total{route_class}`;
   - one fixed-bucket permit hold histogram.

   No identity, repository or payload label is allowed. The existing
   `CIWebhookDeliveryUnavailable` alert is unchanged.
8. **Requirements and Proofkit.** Replace only the webhook single-worker clause
   of `REQ-CI-RUNTIME-008` with the pool clause. Add the webhook response
   deadline to its absolute-deadline clause and the webhook and logout
   route-class budgets to its public-limit clause. Add the new series to
   `REQ-CI-RUNTIME-015`. Bind each new witness through exact Proofkit routes.
   `REQ-CI-RUNTIME-041` is unchanged. The `REQ-CI-RUNTIME-054` lane contract
   is unchanged; the recheck rotation is bound to it as an ordering witness
   without changing its text.
9. **Documents.** Update the ingestion, HTTP API and runtime composition module
   specifications, the design status and the CI-014 roadmap row. In
   [history worker progress](history-worker-progress.md), replace "existing
   due-time ordering" with the per-lane order. State that the recheck
   cursor is a scope key, not work, a transaction or a connection. Keep the
   design's declared budgets labelled unmeasured.
10. Run admitted static checks locally and obtain independent review. Behavioral
   witnesses run in GitHub CI under the
   [verification placement rule](../architecture/cross-cutting/testing-and-proofkit.md#6-required-gates-and-execution-placement).

## Migration Outline

The selected delivery needs no migration: the existing archive inbox already
covers the only durable post-acknowledgement consumer. A future consumer that
needs its own compact inbox follows the
[database migration protocol](../architecture/modules/database-migration-protocol.md)
in full:

- an expand revision after the current head (`20261011_0018` at this base);
- a new immutable capability ID and descriptor;
- an exact catalog contract and runtime attestation;
- least-privilege runtime grants (SELECT and INSERT; UPDATE only of state
  columns);
- the compatibility profile and schema capability admission;
- fixtures and reset;
- mutation manifests;
- every pinned head (`scripts/container_runtime_smoke.py`,
  `scripts/tests/fake_docker.py`,
  `.github/workflows/runtime-image-qualification.yml` and tests).

Its row holds compact fields only: the canonical delivery ID as a key with
`ON DELETE CASCADE` to its source, the installation and repository scope, a
due time, a generation and an attempt count. The producer writes it in the
acknowledging transaction. The consumer reuses cyclic scope rotation with a
fixed row and time quantum.

## Causal Acceptance

Witness paths are relative to `backend/tests/`. Unit and route witnesses use
deterministic schedulers and blocked fakes. PostgreSQL witnesses use the real
restricted runtime principal.

| Case | Required independent observation | Wrong implementation it kills |
| --- | --- | --- |
| P1 | Two concurrent valid deliveries both claim while the first is held in its worker (`unit/runtime/test_webhook_admission.py`) | process-global lock retained; `CapacityLimiter(1)` |
| P2 | Delivery `N + 1` receives 503 `webhook ingestion unavailable` with no claim and no worker start | waiting acquisition; 429 mapping; worker started before permit |
| P3 | With commits blocked, `N` in-flight commits cause the next delivery to be refused | permit released before commit (current behavior) |
| P4 | A broken worker fails only its own request; a concurrent sibling completes | shared failure state; pool torn down |
| P5 | Cancelling one request terminates its worker and releases its permit | permit leak on cancellation |
| P6 | Composition rejects `N = 1`, `N = 9` and `N + B + F_min = pool + 1`, and accepts equality | off-by-one comparison; `B` missing the archive delivery workers |
| H1 | Single defects keep today's status: invalid declared content length 400, declared length over the maximum 413, bad content type 400, missing signature 401. Combined defects follow the admitted table: declared oversize with no signature stays 413; unknown-length oversize with no signature is 401 and `receive` is never called; length mismatch with no signature is 401; a full path admission with no signature is 401 (`unit/api/http/test_body_limits.py`, `test_github_webhooks.py`) | header checks reordered against the declared-length checks; header checks after the path lease or body read |
| H2 | A header-rejected request consumes no token and never calls `receive` | consume-then-check; body read before header checks |
| H3 | An empty bucket returns 503 `webhook ingestion overloaded` before `receive`; the byte bucket charges the declared length and an unknown length the maximum | bucket after body read; count-only bucket |
| L1 | Forged logout tokens (known key ID with a bad signature, unknown key ID) within the envelope rate do not prevent a valid token from being applied, and never reach the session store (`test_control_plane_identity_route.py`, `unit/integrations/keycloak/test_tokens.py`) | 16/4 bucket retained before verification; store call before verification |
| L2 | An exhausted envelope returns the typed 429 without touching the store or a key fetch | envelope bucket consulted after token verification or store access |
| L3 | With tokens available, a logout token with a disallowed header field, algorithm, type or key ID shape is rejected with zero `get_key` calls and no store call (`unit/integrations/keycloak/test_tokens.py`) | key fetch moved before the header shape checks in `integrations/keycloak/tokens.py:178-189` |
| D1 | With the global request deadline 15 seconds and a commit blocked past the webhook work deadline, the response is 503 `webhook ingestion unavailable` at the webhook deadline under a controlled loop clock, and the permit is released (`unit/api/http/test_request_admission.py`, `unit/runtime/test_webhook_admission.py`) | webhook policy without `timeout_seconds`; derived policy not excluded; permit leaked on deadline cancellation |
| D2 | A delivery whose commit succeeds but whose response is cut by the deadline returns 503, and its redelivery returns `duplicate_delivery` with exactly one claim, audit event and inbox row (`integration/persistence/test_runtime_delivery_state.py`) | processed acknowledgement after a cancelled commit; commit retried by the timeout path |
| F1 | Rows A1 and A2 of scope A are due before row B1 of scope B; three recent-lane claims return A1, B1, A2. A single-scope backlog still drains in `(next_attempt_at, workflow_run_id)` order. A repair-lane claim between them does not change the recent-lane order. When the scope after the cursor is locked by another transaction, the next due scope is claimed (`integration/persistence/test_history_rechecks.py`, `unit/app/test_ci_history_collection.py`) | oldest-due-row scope order retained (A1, A2, B1); cursor not advanced after a claim; cursor shared across the recent and repair lanes; claim stops at a locked scope |
| M1 | Each refusal and result path increments exactly one closed outcome; labels contain no identities (`unit/observability/test_observability.py`) | missing pre-auth outcome; free-form labels |
| R1 | Existing atomic claim, audit, observation and inbox, duplicates, body aliases and conflicts remain routed unchanged (`integration/persistence/test_runtime_delivery_state.py`, `test_history_delivery_ingestion.py`) | regression of the protected observables |
| R2 | Preparation is still offered only after a successful commit (existing witness) | offer before commit |

Each mutant must fail the intended assertion, not setup, import or timeout.

Delivered witness locations differ from the table in four places:

- D1 composes the route in `unit/api/http/test_github_webhooks.py` under a
  virtual loop clock;
- L2 uses `unit/api/http/test_public_request_limits.py` with an injected clock;
- M1 spans `unit/runtime/test_webhook_admission.py`,
  `unit/api/http/test_body_limits.py`, `test_public_request_limits.py` and
  `unit/observability/test_observability.py`;
- P6 adds `unit/runtime/test_connection_shares.py`.

A derived webhook policy that is not excluded fails app construction on the
unique-policy check, not an assertion.

## Live-Only Evidence (CI-069)

The following cannot be proven in native CI. CI-069 measures them in the
authorized environment against the declared budgets:

- arrival and burst distributions;
- payload sizes;
- permit hold times and the Erlang-B falsifier;
- RSS with workers;
- audit-head wait;
- pool wait per class;
- archive pending age and drain;
- valid logout success under invalid traffic;
- rolling-deploy drain;
- all-replica provider and credential budgets.

The edge filter is qualified under CI-059/CI-062. Until then, valid-delivery
capacity under invalid traffic beyond the declared budgets stays unclaimed.

## Closure

Freeze the complete diff for one independent review, then run exact-head
native CI. CI-014 stays open until the merged exact source is qualified. Live
capacity stays with CI-069.
