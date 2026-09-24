# Actions History Population Implementation Plan

Status: active implementation; not a release receipt

Design: [history population](actions-history-population.md).
Parent delivery: [retention implementation](actions-history-retention-implementation-plan.md).

## Implementation Sequence

| Step   | Owner and files                                                                                                 | Required observation                                                                                                                                                                                                                           |
|--------|-----------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1      | `ci_economics/history_cursor.py`                                                                                | Frozen arbitrary history interval, bounded inner discovery, exact scope/page admission and explicit truncation.                                                                                                                                |
| 2      | `ci_economics/history_attempts.py`                                                                              | Numeric attempt traversal with fixed ceiling, exact response identity and constant memory.                                                                                                                                                     |
| 3      | `ci_economics/archive_retention.py`                                                                             | Total policy/expiry algebra and immutable first import, independent from active evidence.                                                                                                                                                      |
| 4      | Archive field/payload contracts and storage                                                                     | Retained attempt/job operands, strict bytes, unique contributions, quota, lease/CAS, forward migration and runtime grants.                                                                                                                     |
| 4a     | `history_rechecks.py`, recheck storage and native witnesses                                                     | One bounded interval per run, replay-safe hint union, recent-source reserve, finite acquisitions including crashes, and atomic gap/retry/progress.                                                                                             |
| 4b     | `ci_history_detail_cleanup.py` and its PostgreSQL witnesses                                                     | Bounded set-based expiry, exact released bytes, immutable statistical parents and rollback inside and outside the cleanup savepoint.                                                                                                           |
| 5      | Existing provider/application and maintenance owners                                                            | Bounded authorized reads, durable result/gap before checkpoint advance, independent active evidence and retry recovery.                                                                                                                        |
| 5a     | `app/ci_history_collection.py`, capability ports and observability                                              | One bounded claim per backfill/recent/repair lane, whole-read access deadline, exact response identity, abort/cancellation and separate provider/transition metrics.                                                                           |
| 5b     | Durable incremental feed and recent discovery                                                                   | Supported accepted deliveries become atomic delivery/audit/source/inbox; new runs progress during backfill, with explicit loss/recovery boundaries and unchanged active-evidence meaning.                                                      |
| 5b.i   | `persistence/ci_history_delivery_codec.py`                                                                      | Exact source identity/hash/retention closure; job-only and unknown-workflow sources remain unsupported hints, with no queue or statistical authority.                                                                                          |
| 5b.ii  | New inbox schema/catalog and producer/consumer adapters                                                         | One compact source-dependent row; atomic ingestion and queue transfer, indexed pending work, source expiry without statistical deletion, exact runtime grants and no cyclic history sweep.                                                     |
| 5b.iii | `app/ci_history_delivery.py`, capability ports, metrics and maintenance composition                             | Bounded active-scope metadata,16-scope rotation, four workers, per-scope and whole-round deadlines, exact SQL re-admission, sibling progress and separate transfer/age metrics. Process rotation is not durable authority or restart fairness. |
| 6      | HTTP, Settings, observation and analytics                                                                       | API/UI parity, configured history, first-import retention, coverage and real scan progress.                                                                                                                                                    |
| 6a     | `history_commands.py`, `history_administration.py`, `ci_history_queries.py`, `app/ci_history_administration.py` | Actor-free request, coherent bounded status, fresh authorization before storage, one deadline, exact receipt validation and unchanged command/audit bytes. See [administration design](actions-history-administration.md).                     |
| 6b     | HTTP dependencies/routes and generated contracts                                                                | Server-derived actor, role/CSRF checks, strict input, bounded body/admission, safe status projection and API-first configuration parity.                                                                                                       |
| 6c     | Archive controls and progress views                                                                             | Initial interval, all/selected workflows, enable/pause/rescan, retention override/inheritance and visible truthful coverage; no mutation replay after session recovery.                                                                        |
| 6d     | Explicit earlier-bound expansion through existing configuration owners                                         | Admit a strictly earlier whole-second UTC bound only on a current dataset with unchanged configuration; restart backfill without erasure or changing the recent frontier, fence stale claims and retain exact replay. Use field-level serialization exclusion so absent/null expansion preserves exact old command JSON and digest while non-null expansion changes both. Reject expansion while a committed backfill page is pending; retain existing rescan/selector behavior and distinguish pending work from a stale-revision conflict. Expose expansion through API/UI with its reread cost; reject narrowing and simultaneous expansion/rescan. |
| 6e     | Provider inventory projection and historical collection form                                                    | Admit optional repository creation metadata, project it through API and UI, and offer an explicit UTC-day shortcut for initial or earlier-bound import. Preserve manual entry, reject malformed timestamps, fence selection by repository and browser authority revision, and never label the source date as archive completeness. |
| 7      | Native and live qualification                                                                                   | Exact-head GitHub tests, independent review and actual Swarm lifecycle.                                                                                                                                                                        |

Steps1-3 do not constitute a delivered archive. Do not enable a control that
claims historical collection until steps4-6 are wired and qualified. Group
the coherent feature in a publication batch rather than opening one PR per
helper. Keep sidebar-menu work and other independently deployable UI changes
in their own owner scope.

## Native Counterexamples

- Reject cross-scope and cross-cursor pages, naive or subsecond boundaries,
  mutable attempt ceilings, bool-as-integer inputs and malformed source values.
- Traverse more than seven days and saturated intervals with shared boundaries
  and fractional source timestamps; retain `provider_truncated` without claiming
  completeness. Test 201 sources on pages 1/2/3, including serialized page 2 resume.
- Verify equivalent canonical cursors resume the same next provider request.
- Enumerate attempt 1 through the frozen latest attempt with constant retained
  cursor size. Duplicate or foreign completion must not advance progress.
- Test disabled/days/forever retention, exact deadline boundaries, missing
  first import, changed applied policy, replay and no resurrection after expiry.
- At durable integration, test both lock orders for cleanup/import/policy
  updates, rollback, lease expiry, duplicate contribution, job/byte quota,
  preserved summaries after detail deletion and unavailable old attempts.
- Check recheck progress under duplicate/expanded hints, exhausted acquisitions,
  exact lease expiry, changed selection/configuration and reserve exhaustion.
  Source promotion cannot reset a retry. The terminal holder and expired-lease
  recovery compete on one revision; progress requires committed data or a gap.
  First-failure handoff avoids a second retry counter in the historical cursor.
- Assert the exact allowed external-effect prefix at every abort boundary,
  not only the absence of a final write. Test authorization and provider
  stages that each fit the budget but jointly exceed the single monotonic
  deadline. Fast recent/repair lanes must complete while backfill remains
  blocked, not merely enter provider reads concurrently. Shared fixtures need
  an admitted positive baseline and native execution before these oracles close.
- For retained delivery admission, independently substitute each query/body
  identity operand, digest, source time, retention boundary, scalar type and
  canonical encoding. A coherently changed source must change its fingerprint;
  missing workflow provenance must never become a usable queue hint. Test
  source bytes and database memoryviews, job-only input, unknown keys, null
  conclusion and fractional timestamps without changing the existing encoder.
- For incremental inbox delivery, prove producer rollback and same-body replay,
  source restoration, transfer plus receipt atomicity, replay after queue
  deletion, queue capacity and generation fences. Source expiry may cascade
  only to its transient inbox, never to rechecks or permanent statistics.
  Exercise source/foreign-key/inbox lock ordering against configuration and
  consumption through separate restricted-principal connections. Verify that
  excluded sources stay pending and that exclusion of already queued work is
  explicit cancellation; reenabling uses the configured historical population,
  with out-of-population recovery left explicit rather than silently claimed.
- For delivery scheduling, cover finite and malformed scope populations,
  removed/added scopes between rounds, the16-scope attempt cap and four-worker
  peak, a blocked first scope with successful later scopes, handled errors,
  per-scope/round timeout and abort before each next effect. Demonstrate that
  a process restart replays only pending work and cannot reset a delivered
  receipt. Assert source age and transfer counts separately from run statistics.
- Compare empty, expired-pending and expired-acknowledged sources. The bounded
  expiry sample must distinguish the first two while creating no expired-source
  queue or receipt; acknowledged sources contribute no sample. Exercise repeat
  sampling, batch bounds, valid-sibling progress and cleanup with preserved
  permanent parents. Histogram observations are not distinct lost-run totals.
- For administration, independently mutate every identity, revision, pair,
  clock and count relation. Denied, unavailable or malformed authorization
  must make zero store calls; authorization plus storage share one deadline.
  Test exact replay after a newer configuration, cancellation, foreign receipts,
  missing defaults and half-pairs. PostgreSQL must return one joined snapshot
  without acquiring a dataset write lock, reject the bounded count overflow and
  leave all state unchanged on a read. Preserve the enclosing shared schema
  compatibility fence. Native transport tests separately prove that the client
  cannot inject an actor or receive a lease token/provider payload; isolate each
  required field, role, CSRF, byte/model bound and outcome/snapshot pair. Verify
  that the largest admitted selector fits the body budget and that excess
  concurrent writes are rejected rather than queued.
- Before native publication, run the complete requirement/architecture trace
  admission in addition to Proofkit command admission and selective planning.
  Register each concrete service and both request/response routes in their
  independently checked inventories. A targeted route pass is not this closure.
- For History controls, bind initial UTC range, every retention variant, workflow
  selection and quota operand to exact request bytes and successful receipts.
  Test pause/rescan, rejection, lost acknowledgement and identical explicit retry.
  Current-scope status and authority replacement must fence stale reads and
  writes through the production console wrapper. Reuse existing observation
  polling witnesses for the extracted shared lifecycle, and independently verify
  the history wrapper. Native browser checks cover the desktop, mobile and 320px
  matrix, real refresh animation, reduced motion, disclosure, keyboard and axe.
  Use lexical JSON for deliberately invalid scalar fixtures: canonicalization
  must not normalize or reject the counterexample before its target validator.

Existing discovery witnesses are reused only for their unchanged predicates;
new outer-window and attempt relations need their own assertions. Record all
new proof-like paths in requirement/command routes. Static admission is not
behavioral execution. Run behavior and PostgreSQL tests in GitHub only.

## Release Boundary

Qualify live archival imports, authorization and retention on the authorized
Swarm deployment. Source and CI receipts do not substitute for this boundary.
An unavailable deployment blocks its qualification, not independent source
work, and never grants permission to weaken network or authentication controls.
Preserve all other global program rows.
