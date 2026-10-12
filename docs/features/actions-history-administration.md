# Actions History Administration

Status: active design; implementation and live qualification remain separate

## Decision And Ownership

Expose historical collection through the existing authenticated administration
boundary. The capability owns its configuration request and coherent status;
the application owns fresh repository authorization and an operation deadline;
HTTP and UI project those contracts without creating collection authority.

This document owns public administration semantics, not archive persistence,
retention or population traversal. Those remain in [storage](actions-history-storage.md),
[retention](actions-history-retention.md), [policy resolution](actions-history-policy-resolution.md)
and [population](actions-history-population.md). Execution order and witnesses
belong to the [population plan](actions-history-population-implementation-plan.md).

## Protected Behavior

- Compact statistical contributions survive ordinary optional-detail expiry.
- Active CI evidence retains its independent retention and eligibility rules.
- Pause stops new acquisition without deleting retained statistics.
- Configuration and rescan preserve the existing command identity, revision
  comparison, audit transaction and generation fences.
- A completed traversal describes its frozen selected interval, not all
  provider history or the absence of deleted and inaccessible runs.
- Configuration is not proof of successful collection or production capacity.

## Configuration Admission

Use an actor-free `HistoryConfigurationRequest` as the public input. Add the
authenticated principal at the application boundary to form `ConfigureHistory`.
Its existing fields, aliases, canonical serialization and audit digest remain
unchanged. Input cannot supply or override the actor. Pydantic owns field and
cross-field admission; authorization and database ordering are separate proofs.

```text
AdmittedWrite(request, principal, scope) =>
  StrictRequest(request)
  and ServerAuthenticated(principal)
  and RoleAllowsConfiguration(principal)
  and FreshRepositoryAccess(principal, scope)
  and request.scope = scope

CommittedConfiguration(command) =>
  ExactOperationIdentity(command)
  and ExpectedRevisionMatchesOrExactReplay(command)
  and Atomic(configuration, scan, audit)
```

An initial request declares the lower bound explicitly; an update cannot
silently change it. A rescan is an explicit operation on an existing dataset.
The UI must distinguish all workflows from selected workflow identities and
show the chosen interval. Neither an arbitrary old date, a repository creation
date nor an empty result proves that all available history was scanned.

### Catalog Population Source

Provisional owner decision (2026-10-11), Q-007 (a). The optional
`populationBoundSource` (`{kind: "repository_created_at", repositoryCreatedAt}`)
marks an explicit "All available" choice. It is admitted only with exactly one
population bound, initial `initialCreatedFrom` or `expandCreatedFrom`, equal to
the UTC-day floor of the asserted instant; plain updates and rescans reject it.
Absence is the only spelling of an explicit bound. Omitted and explicit `null`
serialize without the key, so every existing command keeps its digest bytes;
a present source is bound by `commandDigest`.

```text
CatalogSourceAdmitted(c) =>
  exactlyOne(c.initialCreatedFrom, c.expandCreatedFrom) = utcDayFloor(c.repositoryCreatedAt)
  and Authorized(c) precedes ProviderRead(c.scope)
  and ProviderRead(c.scope).createdAt = c.repositoryCreatedAt
  and c.repositoryCreatedAt <= DatabaseNow
```

Order inside the single administration deadline: repository authorization;
for a catalog source only, a short transaction that classifies a retained
receipt (replay or `operation_conflict`, with no provider read); then one
installation-token `GET /repositories/{id}` through the existing
`repositories.get_by_id` admission, response binding and creation-date grammar
shared with the provider inventory; then the configuration transaction. No
transaction is open during the provider read. Explicit-date requests take
none of these steps.

| Provider or database outcome                                    | Result                                                  |
|-----------------------------------------------------------------|---------------------------------------------------------|
| unavailable, malformed, foreign repository id, non-success read | 503 unavailable, retryable; no transaction              |
| creation date absent or `null`                                  | 422 `creation_date_missing`; no transaction             |
| creation date differs from the asserted instant                 | 422 `creation_date_mismatch`; no transaction            |
| asserted instant later than database time                       | 422 `creation_date_in_future`; no audit and no writes   |
| equal                                                           | ordinary configuration with recorded provenance         |

Deadline expiry is unavailable and cancellation propagates. The store
re-classifies a receipt first, so a concurrent identical commit still replays,
and it refuses (unavailable) a catalog source without a server observation of
the same scope and instant, so a caller that skips verification cannot record
`repository_created_at`. Replay of a catalog-source receipt requires its
provenance row for the receipt generation unless erasure has advanced the
generation; an explicit receipt requires none because a predecessor binary may
have committed it. The audit payload stays exactly `{commandDigest, snapshot}`.
Verification proves only that, at save time, an installation-token read of the
scoped repository reported this creation instant. GitHub unavailability refuses
"All available" while an explicit date remains available.

An exact successful receipt must match the requested scope, configuration and
`expectedRevision + 1`. A replay receipt describes the original committed
operation, not necessarily the latest configuration. Fetch current status after
mutation; never overwrite a newer visible status with an older replay receipt.

## Coherent Status

Read defaults, the optional dataset/scan pair and the current-generation recheck
count in one PostgreSQL statement snapshot. Use a left join from the required
defaults singleton so an unconfigured scope has a real response. Decode each
row with its existing persistence codec; reject missing defaults, half a pair,
foreign identities and contradictory generation or configuration revisions.
The same statement joins, laterally, the earliest current-generation row of
`ci_history_population_bounds`; it adds no lock and no provider call.

```text
StatusAdmitted(s) =>
  ExactScope(s)
  and ExactDefaults(s)
  and (DatasetPresent(s) <=> ScanPresent(s))
  and (DatasetPresent(s) =>
    dataset.scope = scan.scope = s.scope
    and dataset.generation = scan.generation
    and dataset.configurationRevision = scan.configurationRevision)
  and 0 <= pendingRechecks <= MaxHistoryRechecks
  and (BoundPresent(s) =>
    bound.scope = s.scope and bound.generation = dataset.generation
    and bound.createdFrom >= scan.createdFrom)
  and (CurrentProvenance(s) <=> BoundPresent(s) and bound.createdFrom = scan.createdFrom)
```

The snapshot carries database observation time. It is not a lease or write
receipt. Reject a recorded configuration/default/cycle time later than that
observation; a database clock anomaly is unavailable status, not invented
freshness. Future scheduled retries are valid and are not rejected by this rule.

The public projection contains configured state, revisions, resource usage and
quota, inherited/effective detail policy, frozen traversal interval, progress,
last outcome and pending rechecks. It excludes worker identities, lease tokens,
provider-page payloads and internal audit material. These exclusions are tested
at the transport boundary before public activation.

The recheck query uses an exact scope/generation and a `B + 1` overflow probe,
not a full ledger load. Overflow rejects status instead of silently reporting
`B`. The status query acquires no dataset row or advisory write lock and performs
no provider request or mutation. The enclosing unit of work still applies its
normal shared compatibility fence, schema admission and resource checks.
Those prerequisites are not additional reads of archive progress and must not
be removed to satisfy a data-query-count oracle.

## Failure And Temporal Contract

A single 20-second monotonic application deadline covers authorization and the
store call together. Authorization must return exact `True`; exact `False` is
forbidden and unavailable or malformed authorization is unavailable. Neither
failure may call the store. Cancellation propagates; no detached task continues
an administrative operation.

```text
StatusOrWriteUnavailable != ProofOfRollback
RetryAfterUncertainWrite => SameOperationId and SameCommand
```

Database conflicts and invalid population remain typed outcomes. A wrong-scope
or contradictory successful receipt becomes unavailable. Transport errors must
not disclose database, credentials or provider details. Local UI state cannot
grant access, skip role checks or replay a mutation after session recovery.

## HTTP Projection

| Operation                                   | Route                                                                          | Admission                                                                 |
|---------------------------------------------|--------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| Configure, pause, resume or explicit rescan | `POST /api/v2/economics/history`                                               | Configure role, current repository access and browser mutation integrity. |
| Read current archive status                 | `GET /api/v2/economics/repositories/{installation_id}/{repository_id}/history` | Audit role and current repository access.                                 |

Both routes reject query parameters. POST admits one exact `application/json`
content type, no content encoding, at most 4096 bytes, depth 6 and 256 JSON nodes;
duplicate keys are rejected before model admission. These limits include the
maximal admitted workflow selector and configuration request. The mutation uses
the existing two-request administrative bulkhead; reads share the existing
economics read bulkhead. The outer HTTP deadline also covers principal/role
admission; the application's20-second deadline covers repository access and
storage. A disconnected or cancelled request does not create detached work.

Responses are explicit camelCase projections with `Cache-Control: no-store`.
Mutation success is 200 with `committed` or `replayed` and the exact snapshot.
Revision, operation, capacity and fenced-dataset conflicts are409 without a
snapshot. A well-formed impossible initial interval is 400 `invalid_population`;
malformed representation is 400/422 according to the byte/model boundary.
Unauthenticated is 401 with `WWW-Authenticate`, denied is 403, unavailable or
overloaded is 503, and the body bound is 413. Errors disclose no internal cause.

Status includes the technical defaults, separately resolved effective policy,
optional dataset and scan, bounded pending count and observation time.
`populationBound` is `null` without a scan; otherwise it names the cursor's
`createdFrom` with `source` `explicit` or `repository_created_at` (with the
verified `repositoryCreatedAt` and `recordedAt`) or `unrecorded` (both `null`).
The operator UI admits catalog provenance only when the creation instant is not
later than `recordedAt`, the same relation the backend value type enforces.
The status keeps `ci-economics-history-status/v1`, following the `discovery`
precedent: strict clients fail closed, so an already-open tab rejects the
added key and shows the status as unavailable rather than partial (O-2,
resolved 2026-10-11). A version literal alone would not restore compatibility
without negotiation. A catalog refusal is 422 with a `HistoryMutationResponse`
whose closed `outcome` names the reason and whose snapshot is `null`. Scan
progress exposes interval, current window/page, revision, cycle start, retry
time, optional lease expiry, counters, last outcome and traversal completion.
A lease expiry is scheduling information, not proof a worker is running.
Worker identity, claim token and buffered provider contents are excluded by
construction. OpenAPI is generated through the existing operator contract
owner and must describe actual request aliases and response branches. This
addition does not itself enable a dataset or qualify a live deployment.

### Native Adapter Closure

`ObservationPositiveId` owns exact integer admission independently of enclosing
model configuration. In the pinned FastAPI/Pydantic adapter, a named alias can
otherwise accept `true` as `1` even though the standalone strict model rejects
it. Intrinsic `Field(strict=True)` preserves the existing ID and quota contract
across direct, nested and HTTP validation; this is not a new business restriction.
Use the library's [field-level strictness](https://pydantic.dev/docs/validation/latest/concepts/strict_mode/#at-the-field-level),
not duplicated route validators or private core-schema manipulation.

`ConfigureHistory.from_request` revalidates the actor-free request and binds
only the authenticated server actor. Its serialization belongs to the capability,
not the router. Command bytes, aliases and digest remain unchanged. Independent
witnesses must cover forged instances, actor spoofing, every shared numeric
position, and the actual mounted adapter, not only standalone model admission.

The response models, route and transitive schema owners are registered in
the existing model inventory and OpenAPI provenance manifest. Exact inventory
equality remains enforced; a generated schema alone is not proof of strict
runtime admission or complete source provenance.

## Operator Interaction

The repository Economics console owns a lazy History tab. It separates
historical collection from the recent observation window and keeps the existing
repository and session remount fence. The form offers an explicit initial UTC
date, all or selected workflows, enabled/paused state and an explicit rescan.
The provider catalog is reused, not copied. An initial import requires an
explicit choice between "All available (from repository creation date)" and
"From a specific date (UTC)"; enabling collection chooses neither and saves
nothing. Only the explicit all-available choice is represented as the
repository creation date, with the recorded catalog instant. Editing the date
afterwards returns to the explicit mode and drops the source, so a stale source
never accompanies a hand-edited date. Reloading the saved configuration clears
the range choice together with its date, so neither mode survives a reload or a
refused save without being chosen again. An existing dataset offers "Extend to all
available" only when the floored date is strictly earlier than the current
bound, under the existing expansion constraints. Missing or invalid catalog
metadata, a deep link without catalog selection, or a floor not earlier than
the current bound disables the choice with an explicit status text and no
fallback date. Selecting or recording the choice shows that GitHub may no
longer retain the oldest runs. Server refusals are shown as such; a mismatch
asks the operator to reload the repository catalog. Progress labels the current
bound as repository creation date (with the catalog instant), explicit date or
provenance not recorded, from status after reload.
Once visited, History and Observation remain mounted within that scoped console;
switching sibling tabs preserves drafts and unresolved commands but suspends
their status readers. A true scope or session-authority replacement still
disposes them. Persistence across a full page reload is not supplied here.

Advanced settings disclose retention inheritance and all quota operands.
The initial editable safety preset is 10,000 attempts, 100,000 jobs, 1,000 gaps
and 256 MiB of canonical data. It is not a measured capacity promise. Inheritance
is distinct from disabled detail, finite days and forever. Statistics remain
independent from optional detail expiry. A policy edit applies under the storage
owner's first-import rules, not as an implicit rewrite of already imported data.

The client admits generated-contract responses with strict Zod schemas and
exact scope, operation, revision, configuration and outcome relations. A400
`invalid_population` is a known rejection, not an unknown transport result;
a 422 catalog refusal is admitted only for a command that carried the source.
One in-flight mutation owns its frozen command. Unknown outcome freezes editing
and permits only explicit retry of that same command; session recovery never
replays it automatically. A new receipt triggers a current status read.

Polling retains the last validated value, marks stale data, stops after failure,
and pauses while hidden. Refreshes are single-flight with at most one queued
refresh. A queued refresh waits until the document is visible and the view is
active, including when a paused result does not permit automatic polling.
Scope or authority replacement aborts old work; a late result cannot
update the new owner. The same existing lifecycle serves recent observation and
archive status, so one generic polling hook replaces duplicate control flow.

Progress exposes pages checked, attempts visited, retained attempts/jobs/bytes,
gap count, pending rechecks, frozen date range and current window. It distinguishes
paused, allocation expiry, retry, quota and completed traversal. No percentage,
gap-free claim or worker-running claim is inferred. Only an actual status fetch
animates; reduced-motion and keyboard access remain required.

Native component tests cover draft preservation, conflict, lost acknowledgement,
same-command retry, role/CSRF, polling failure and scope replacement. Browser
witnesses use the production wrapper at desktop and mobile sizes. Static proofs
do not establish browser rendering, live import completeness or capacity.
The [recovery supplement](actions-history-recovery-hardening.md) records the
external-audit dispositions and additional repair acceptance conditions.

## Alternatives And Revision Conditions

| Alternative                               | Decision and reason                                                                                                         | Revisit when                                                                   |
|-------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------|
| Several unrelated status reads            | Reject: at READ COMMITTED they can combine two configuration epochs. One joined snapshot is sufficient.                     | The status no longer fits a bounded single statement.                          |
| Lock the dataset while polling            | Reject: observing progress must not contend with its producer's write admission.                                            | A new read actually requires a write-linearized authority claim.               |
| Duplicate the wire configuration schema   | Reject: actor-free input and actor-bound command share one capability validator while preserving a distinct trust boundary. | The public version intentionally changes semantics.                            |
| Expose the stored scan object             | Reject: its lease and buffered provider payload are neither necessary nor appropriate for administration.                   | A separately authorized diagnostic contract admits specific fields.            |
| Add a service or broker for configuration | Reject: existing authorization, transaction and idempotency boundaries cover this bounded operation.                        | Measured workload or durable asynchronous commands exceed the admitted budget. |

These choices are preferable under the current owners and constraints, not a
claim of universal optimality. Public controls remain unavailable until their
native transport, role, CSRF, cancellation and UI witnesses are qualified.
