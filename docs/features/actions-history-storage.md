# Actions History Storage

Status: implementation design; writers and deployment not yet qualified

Owner: `ci_economics` owns historical meaning; `persistence` owns the PostgreSQL
refinement. [Population](actions-history-population.md) owns traversal and
[retention](actions-history-retention.md) owns lifetime policy. This document
owns their transactional storage boundary, not a replacement policy engine.

## Decision And Protected Behavior

Use the existing PostgreSQL database, compatibility admission, bounded units of
work and maintenance infrastructure. Do not create a broker or another service.
Permanent archive and recheck tables have no foreign key to active evidence,
its collection queue or expiry tombstones. The new transient delivery inbox
alone is a child of its retained observation. Existing observation contents,
planning and evidence retention keep their present behavior. Migration creates
no enabled subscriptions or historical
contributions. It seeds only the technical singleton default policy (365 days,
revision1); later operator changes require an audited command.

The archived statistical payload is separate from richer detail. Persist a run
header and individually queryable job contributions; do not repeat the full job
array in both the parent and children. This makes per-job filtering possible
without loading a repository's complete history or retaining expired detail.

## Storage Ownership

| Relation           | Meaning and bound                                                                                                                                                                                              |
|--------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Default policy     | One audited singleton policy and revision; no reserved repository identity.                                                                                                                                    |
| Dataset            | One durable scope anchor, generation, separate configuration/data revisions, pause/erasure state, explicit override or inheritance and row/byte budgets.                                                       |
| Scan               | One resumable historical scan per dataset, exact generation/configuration, revision, bounded pending page/attempt state and hard lease.                                                                        |
| Attempt statistics | Unique scope/generation/run/attempt, exact head and workflow provenance, population quality, conflict flag and immutable first import.                                                                         |
| Job statistics     | Unique child job ID, permanent declared metric/cohort operands, independent of detail expiry.                                                                                                                  |
| Attempt detail     | Bounded allowlisted payload, immutable first-detail-import anchor and applied policy/revision; cleanup preserves the statistical parent.                                                                       |
| Gap                | Bounded scope/scan/interval or exact attempt observation; missing access or 404 is not deletion authority.                                                                                                     |
| Recheck queue      | Bounded pending run intervals for recent hints and historical repair, with separate source reserve, retry eligibility and exact hard-lease authority.                                                          |
| Delivery inbox     | At most one compact child per supported retained run observation, with its exact source fingerprint, pending hint and delivered dataset generation. No raw payload, cursor or permanent statistical authority. |

One page holds at most 100 discovered runs. The persisted pending page lets a
worker consume one attempt at a time before advancing the outer cursor, without
creating an unbounded per-run queue. A successful or explicitly unavailable
attempt is durable before its cursor advances. Completion retains the exact
last cursor and frozen interval with an explicit terminal flag, rather than
replacing all scan coordinates with null. This preserves the population shown
to an administrator and the frontier needed for subsequent incremental scans.
The flag is also an indexed storage projection so idle completed scans are not
repeatedly locked as runnable work. A transient provider failure
retries under bounded scheduling; it is never silently recorded as success.

## Transaction Boundary

### Incremental Delivery

#### Source Admission

The archive's source decoder is a projection of the existing retained
observation contract, not another webhook parser. Validate the entire bounded
canonical envelope and its independent query columns before constructing a
queue hint. For completed runs, require equal scope/run/attempt/head in both
representations and the existing semantic hash of the canonical body. Reuse
the existing job decoder for job rows; job-only and unknown-workflow sources
produce explicit unsupported outcomes rather than invented provenance.

Bind the source fingerprint to the decoder version, all source columns,
canonical-body digest, original recording time and exact source expiry.
Changing an unused display field still changes that fingerprint even when it
does not change the queue hint. Equivalent PostgreSQL timezone presentations
represent the same instant; different canonical body bytes remain distinct.
This admission establishes neither provider completeness nor queue mutation.

#### Selected Delivery Design

Use one compact PostgreSQL inbox, populated by the ingestion owner in the
same transaction as delivery identity, its audit event and normalized source.
This replaces the unimplemented receipt-plus-cyclic-sweep proposal. It is a
design selection for the current archive, not measured capacity or permission
to enable an unqualified writer.

For a supported run observation, successful ingestion now requires all four
records. This deliberately strengthens the existing three-record commit
obligation: an inbox failure rolls back ingestion and returns unavailable.
Do not describe the additional SQL, storage and failure dependency as free or
as unchanged ingress behavior. Keep the HTTP result algebra and existing
signature, body-deduplication and source-retention semantics unchanged.

```text
AcceptedSupportedSource => Atomic(Delivery, Audit, Observation, Inbox)
CommittedWork and LostResponse => SafeDuplicateAdmission
Transferred(source, generation)
  => Atomic(ExactSourceBinding, CurrentDatasetAdmission, RecheckAdmission, Receipt)
```

The row contains only canonical delivery identity, source fingerprint,
scope/run/attempt/workflow, run creation time, original recording/expiry times
and a delivered generation/time pair. It contains no log, actor, runner name,
credential, raw webhook or duplicate canonical body. Identity is the canonical
stored delivery ID, including the existing same-body/new-GUID mapping.
Job-only and unknown-workflow observations have no usable hint and create no
inbox row; this is unsupported provenance, not successful history collection.
The producer bypasses archive decoding entirely for its already-owned job
observation. A job cannot produce a run hint, so adding an archive-only decoder
must not narrow its existing ingress admission or introduce unnecessary CPU
work. The standalone source decoder still validates job rows when explicitly
asked to classify retained data; that read contract is not a new ingress gate.

The inbox has one primary/foreign key to the retained observation, with
`ON DELETE CASCADE`. Thus inbox cardinality cannot exceed retained source
cardinality; one source contributes at most one fixed-size metadata row.
This transient transport dependency does not connect source expiry to the
independent recheck queue, archived attempts/jobs or permanent statistics.
Its extra row/index/WAL cost must be included in deployment capacity evidence.
This relation bound is not an absolute database-size or throughput guarantee.

Insertion reuses the admitted source decoder. Exact replay preserves the
receipt. A different live source fingerprint cannot overwrite it. A source
restored through the existing duplicate-ingestion repair is a new retained
source epoch only after the old source and its child have gone; its new
recording/expiry pair is not confused with the old receipt. There is no
inbox-only operation that renews source retention or source identity.

#### Transfer, Selection And Lock Order

The consumer first chooses an admitted dataset, takes its nonblocking scope
lock and locks the current dataset row. It then reads at most 100 eligible
inbox rows, joining their exact sources, and locks only those inbox rows.
Each source is revalidated before transferring its hint. Source fingerprint,
independent query columns, canonical body and retained lifetime must agree.
No SQL transaction spans provider I/O.

Use generation-based pending indexes, with separate scope and scope/workflow
access paths. An initial pending generation is zero; transferred generation
is a positive exact dataset generation. Query only pending generations below
the current admitted generation and current selected workflows. Do not read
completed receipts merely to discover whether new work exists. Expired source
rows cannot transfer even when physical cleanup is delayed.

Queue admission and the inbox receipt share one transaction and dataset
generation. Full queues, unavailable or paused datasets and excluded workflows
cannot acknowledge a source. A failed transaction leaves work pending. The
receipt survives recheck completion or retry exhaustion for the source's
remaining lifetime, so periodic collection cannot reopen the same retry budget.
Generation changes require the separately authorized dataset lifecycle; inbox
processing cannot recreate or reactivate an erased dataset.

Use one transaction-scoped advisory guard for each canonical delivery ID,
shared by source insertion/restoration, source cleanup and inbox transfer.
Acquire it before any source or inbox row mutation. This closes the otherwise
hidden foreign-key race: inserting a child may check and lock its parent after
inserting the child, while a cascading parent deletion locks parent first.
Relying only on the apparent application statement order would be insufficient.
Do not grant UPDATE on immutable source columns merely to obtain a row lock.

Producer order is `audit head -> delivery guard -> source -> inbox`.
Archive transfer takes `dataset scope -> dataset row -> delivery guard -> inbox`
and never acquires the audit head afterwards. Configuration takes
`global admission -> dataset scope -> audit head` and does not mutate inbox.
Source cleanup selects a bounded nonlocking candidate page, obtains delivery
guards in canonical ID order without waiting, then deletes only still-expired
guarded sources. Transfer uses the same guard order for a multi-source page;
a busy guard is skipped. Expiry is rechecked in the deleting statement, so a
re-created nonexpired source cannot be removed through a stale candidate ID.
These orders have no reverse guard/row edge and ingress never takes a dataset
scope lock. New source mutation paths must share this guard or reopen the proof.

A busy repository is skipped without consuming another repository's turn.
Scheduling considers the bounded dataset population, and per-dataset transfer
has a finite row/time budget. Record pending age, transfer counts, contention,
expired source coverage and capacity outcomes separately from collection
success. Index eligibility does not itself prove fairness or tail latency.

Expiry observability is a separate bounded sample, not durable loss accounting.
For an active dataset, inspect at most the transfer page limit of pending,
selected current-generation sources independently of the eligible transfer
page. Report how many sampled sources have reached expiry at the query's
database statement time. A null sample means the probe did not run; zero means
none in that prefix. Record the sample in a histogram, not a distinct-source
counter: polling may observe one source repeatedly. Physical cleanup and rows
outside that prefix remain explicit coverage limits. An empty sample cannot
prove that no source ever expired. This extra read neither acknowledges an
expired source nor lets it block later eligible transfer; existing source TTL,
cleanup and permanent statistical parents are unchanged. Revisit this bounded
choice if operations needs exact durable loss accounting or measurements show
the added query exceeds the admitted per-scope budget.

The maintenance adapter lists at most 257 active dataset identities, rejecting
an overflow of the256-dataset admission bound. This is a bounded metadata read,
not a traversal of archived runs. Each serial process-local round rotates
through at most 16 scopes with four structured worker tasks, a 10-second budget
per scope and a 50-second whole-round budget. A worker advances its local cursor
before attempting a scope; busy, failed or timed-out work cannot monopolize the
first position on the next round. The current dataset is re-admitted under
its SQL scope lock, so the listing snapshot conveys no write authority.

For a stable population of N active scopes, N consecutive cyclic admissions
visit every scope. When every round admits its 16 selected scopes, this needs
at most 16 rounds. Abort, listing failure or whole-round expiry may reduce that
count; there is no unconditional wall-time bound. This conditional rotation
bound is not successful delivery, provider recovery or fairness under repeated
process restarts. A restart may revisit earlier scopes; committed
receipts preserve safety and uncommitted sources remain pending. Concurrent
replicas may overlap their attempts but share the existing SQL authority. No
process-local cursor, metric or task completion is durable acknowledgement.

The bounds are conservative initial limits, not measured optimal throughput.
A serial worker lets one slow scope delay every other scope; unbounded fan-out
has no finite connection budget; a broker introduces another commit boundary.
Four workers over the existing inbox are the smallest selected bounded pool
for the initial multi-repository workload. Revisit these limits using offered
load, pool wait, guard contention and tail-latency measurements; do not infer
capacity from asynchronous syntax. Pending age uses the source timestamp and
the same database statement time that selected it. It is not process elapsed
time or CPU usage.

#### Configuration And Recovery Semantics

Unselected sources remain pending, without occupying recheck capacity, and
become eligible if selected before their source expiry. Already transferred
sources remain acknowledged across policy, quota, pause and selector changes.
Removing a workflow cancels its pending rechecks under the existing
configuration transaction; re-enabling it starts the existing non-destructive
historical rescan of the configured creation-time population. This is explicit
cancellation and reconstruction, not resurrection of every old delivery.

Consequently an A-to-B-to-A selector change recovers provider-visible runs in
that historical population, while cancelled older runs outside its lower bound
require an explicit broader rescan. The API/UI must show the population and
the cancellation consequence before applying a selector change. Neither a
matching selector digest nor a later pause/resume reopens exhausted retries.
This simpler policy avoids per-delivery selector histories and unbounded
configuration-time rewrites; it intentionally does not promise their behavior.

The migration seeds no inbox history. Initial all-history traversal establishes
the requested baseline; all supported deliveries accepted by the successor
ingestion implementation are durably discoverable. Bounded recent discovery
repairs missed new-run notifications. It cannot prove recovery of every old-run
rerun; missing provider deliveries or expired sources require delivery recovery
or an explicit historical rescan and remain a coverage limitation.

#### Alternatives And Native Proof

The rejected cyclic sweep had new-event latency proportional to
`ceil(N/B) * P` over all retained sources. An indexed source stamp would avoid
that scan but change the published economics relation and update authority.
The selected child avoids both changes, at the cost of one bounded SQL
projection and a declared transient lifetime dependency. An external broker
would still need a proved database-to-broker handoff and new operations; no
current measurement requires that cost. Revisit selection if the shared audit
head, inbox or database becomes the measured limiting resource, or if durable
transport must outlive source retention or acquire an independent failure domain.

Native witnesses must cover atomic insertion/rollback, lost response,
same-body/new-GUID deduplication, source restoration, late commits, queue/receipt
atomicity and replay after recheck deletion, full queues, paused/excluded scopes,
selector cancellation and bounded reconstruction, generation fencing, source
expiry/cascade, two-repository progress, and producer/consumer/configuration
lock races, including a child insert racing a cascading parent deletion.
Admission through the restricted runtime principal is mandatory.
No claim of these native results or delivery activation follows from this design.

### Collection Completion

All configuration, import, cleanup and erasure mutations take the same scoped
dataset lock before child rows. Initial dataset creation additionally takes
the existing style of global admission lock before the scope lock. Never hold
a database transaction across provider I/O. Claim acquisition commits before
the bounded provider read; completion opens a new transaction.

A completion compares the exact scope, dataset generation, configuration
revision, scan revision, worker, lease token, claimed state and allowed result.
The locked workflow selector independently admits statistical and unavailable
attempt completions. An excluded workflow cannot consume archive quota or
produce a gap; only the existing checked skip transition advances past it.
Read trusted database time after locking. The terminal scan CAS additionally
requires an unexpired lease at its own database check. Statistics, optional
detail, quota deltas, gaps and cursor advancement commit together. A lost CAS
rolls all these writes back.

```text
AcceptedCompletion
  => CurrentGeneration and CurrentConfiguration and ExactClaim
  and LeaseTimeValidAtCas and Atomic(Contribution, Gap, Quota, Cursor)

same prior scan revision and competing CAS
  => at most one committed successor
```

No Pydantic validator, local clock or provider response can replace those
database predicates. Pause invalidates outstanding claims without erasing
statistics. Resumption issues fresh claims. Reclaim may replace an expired
lease, never restore the old worker's authority.

Use scalar references to one materialized singleton clock CTE in each CAS.
All lease predicates then compare the same database instant without adding an
unjoined relation to `UPDATE FROM`. A singleton cross join would be equivalent
as relational algebra, but its implicit form fails the repository's SQLAlchemy
Cartesian-product guard. Warning suppression would hide other unsafe queries;
separate volatile clock calls would lose the single-instant predicate.

Configuration revision changes on policy/state commands; data revision changes
on statistical, detail or gap mutations. Scan revision changes on progress and
lease transitions. Previewing existing-data policy application or erasure binds
generation and both dataset revisions. Do not use unchanged configuration
revision as evidence that the affected record population is unchanged.

Changing quotas, detail policy or pause state preserves the current historical
checkpoint, while clearing its old lease and rebinding the new configuration
revision. Changing the workflow selector restarts the frozen population from
its original lower bound through current database time: previously skipped
runs may now be selected. An explicit rescan follows the same non-destructive
restart. Existing attempt/job rows, import clocks and applied detail policies
remain unchanged. Ordinary configuration cannot reopen an erasing or erased
dataset; that requires a separately authorized generation transition.

The closed configuration command requires an initial lower bound exactly when
the expected revision is zero. A rescan applies only to an existing dataset.
This relation is a data-boundary invariant; Pydantic rejects inconsistent
requests before persistence. An otherwise valid initial lower bound later
than trusted database time returns `invalid_population`, without an audit or
configuration write. Neither invalid case is a storage-availability failure.

### Historical Repair Handoff

A failed historical attempt advances only after the same transaction records
its exact `provider_deferred` gap and admits that attempt to the bounded recheck
queue. A page-discovery failure has no exact attempt to transfer and retains
the historical cursor. Queue or gap capacity exhaustion rolls back the whole
handoff and preserves the cursor. The final historical lease CAS governs the
gap, queue mutation, quota and advancement together.

Ordinary duplicate hints cannot rewind a progressed queue or reset its retry
budget. An explicit historical repair may revisit a completed attempt inside
the original interval, but only inside that exact live parent-claim transaction.
For example, a queue originally covering 1..4 and now awaiting 3 must return to 2
when the current historical claim transfers failed attempt 2. A stale or repeated
parent claim cannot commit another rewind. The pending lower bound becomes
`min(current next, transferred attempt)`; a lower pending attempt starts its own
retry budget, while an unchanged pending attempt retains its budget and due time.
The queue CAS validates the original hint, not only the merged interval, because
the merged interval alone loses which completed attempt was transferred.

This refinement avoids another persisted repair epoch: the existing parent
revision and terminal CAS already provide single-commit handoff authority.
Reconsider it if repairs become independent of historical claims. Prove rollback
at the terminal SQL deadline and replay after advancement before enabling it.

When a configuration changes its workflow selector, delete excluded pending
queue rows under the same scope lock before acquiring the audit head. Keep all
statistical contributions. Otherwise excluded rows could indefinitely consume
the bounded queue even though no worker may claim them. Audit failure rolls
this deletion back; pause, quota and policy-only changes do not clear the queue.

## Idempotency And Conflict

The natural statistical key excludes the head SHA: a changed head for the
same scope/run/attempt must conflict, not create a second contribution. Exact
replay has zero row/byte delta and preserves both import clocks. Canonical
comparison excludes locally observed time. A contradictory result preserves
the first contribution and records conflict; readers must not aggregate it as
exact. Resolving such a conflict requires a separately admitted operation.

A partial import cannot masquerade as complete. A replacement may refine the
stored contribution only when it preserves every known header and job operand,
contains every previously observed job, and independently satisfies population
admission. Null means unknown, not a contradicting observation. A previously
complete population also fixes its member set. Conflicting common facts or
membership contradicting a complete population mark conflict. A weaker or
compatible but incomparable response preserves the prior record; the latter
reports `incomparable` and does not claim that its extra facts were imported.
This small information-order refinement avoids synthesizing completeness from
a union of partial pages. Reconsider union/refinement only if measured partial
responses prevent useful convergence. Refinement preserves both import clocks
and charges only the exact row/byte difference under the dataset lock.

## Retention And Capacity

Use [source-tagged policy references](actions-history-policy-resolution.md)
for default and repository revisions, and one applied policy/reference per
imported detail record. Prospective changes do not silently rewrite old
records. Applying a policy to existing details is an explicit previewed scope
operation. It preserves first import and cannot revive a detail whose original
deadline has already passed, even if physical cleanup is delayed.

Dataset quotas count attempts, jobs, gaps and retained statistical/detail/gap
canonical bytes. Control state is separately bounded: one 4KiB configuration
and one 64KiB scan per admitted dataset, with the global dataset count capped
before initial scope creation. A single statistical contribution is at most
8MiB, regardless of a larger configured dataset budget. No provider response
can expand these request or storage bounds. Data
insertion and quota reservation share the scoped transaction. Replays reserve
nothing. Quota exhaustion pauses advancement and reports capacity, rather than
evicting permanent statistics. Configured logical byte budgets are not claims
about exact PostgreSQL disk consumption: indexes, row overhead, WAL, backups,
bloat and free-space alarms require the separate deployment capacity receipt.

Each public page, cleanup batch and transaction has a finite row/byte limit.
The capability owns canonical header/job encoding and its byte bounds; provider
and PostgreSQL use the same representation. Provider enumeration reserves the
maximum header envelope and stops before retaining jobs beyond the aggregate
budget. It validates the observed page before that cutoff and reports the
retained prefix as partial, with the provider total unchanged. This sacrifices
at most one header envelope of capacity to avoid a second aggregate encoder or
allowing a valid provider result to fail later as an apparent storage outage.

Use indexed keyset reads, including repository, workflow and source-time
filters; never load all rows to paginate, aggregate or erase them in Python.
Keep null/partial/conflicting durations distinct from numerical zero.

## Erasure And Recovery

### Bounded Detail Expiry

Detail expiry is a separate maintenance operation, not dataset erasure. It
may reclaim expired details from active or paused datasets; erasing and erased
datasets remain under the erasure owner. It selects at most 100 due parents
from the current generation after the dataset lock and trusted database-time
read. The applied policy, immutable first import and stored deadline must
agree before deletion. Null deadlines mean no automatic expiry, not immediate
expiry. Ordinary expiry changes only the detail state to `expired`.

Use a set-based child delete with returned identity and `octet_length`, a
set-based parent update and one dataset quota/revision CAS. Do not read up
to 100 eight-MiB payloads into Python or issue one delete per payload. Returned
child identities must exactly equal the selected parent keys. A missing child,
quota underflow, malformed retention or lost update aborts the savepoint, even
if the caller handles that error inside its outer transaction. Outer rollback
still rolls back the whole batch.

```text
ExpiredBatch => DeletedDetailKeys = AdmittedDueParentKeys
  and ReleasedCanonicalBytes = Sum(ActualDeletedPayloadBytes)
  and Unchanged(AttemptStatistics, JobStatistics, FirstImport, AppliedPolicy)
```

This preserves a terminal expiry record and prevents ordinary re-import from
restarting retention. A bare bulk delete is insufficient because it loses that
state transition and quota accounting. A generic lifecycle engine provides no
additional required behavior. The focused PostgreSQL witness compares exact
before/after rows, bounded continuation, narrow runtime grants, failure inside
a savepoint, outer rollback and generation isolation. Source and static checks
do not establish that those native cases have executed.

### Dataset Erasure

A guarded full-dataset erasure increments the generation and blocks writers
before bounded deletion begins. The scope fence survives data cleanup. Reads
never expose a retired generation. Old claims and ordinary rescans cannot
recreate erased rows; starting a new dataset requires explicit authorization.
Do not reset occupied quota before the corresponding rows are removed.

This first scope does not claim fine-grained privacy erasure or deletion of
backups. Restore must reconcile current deletion fences before accepting
writers or exposing archived data. All-copy conservation and backup expiry
remain deployment obligations; a live SQL delete is not a complete erasure
receipt.

## Acceptance

Use one new forward Alembic revision with an independently attested capability
and least-privilege runtime grants. Preserve all published revisions. Required
PostgreSQL witnesses cover both lock orders for import/expiry, import/pause,
import/erasure, competing claim completion/reclaim, duplicate insertion and
quota exhaustion. Also prove rollback, strict stored-byte/column consistency,
bounded keyset pagination and complete detail cleanup preserving job operands.
The terminal-CAS witness observes the contribution, gap and quota writes inside
the transaction, crosses the real database lease deadline, then requires their
complete rollback and permits only a freshly reclaimed successor to commit.
An earlier pure-time check alone cannot discharge this witness.

The new catalog reuses the existing economics attestation adapter, including
its conservative check of the installed economics guard routine. This is an
explicit dependency, not a claim that archive storage owns that routine. A
standalone archive database would require revisiting that reuse. Runtime ACLs
grant column-level updates rather than scope/attempt identity updates. Their
installation requires drained sessions under the existing deployment fence;
this change does not claim rolling coexistence with the predecessor ACL.

Gap insertion and replay use the already-held transaction-scoped repository
lock and locked dataset row. They must not request `FOR UPDATE` on the
immutable gap child: PostgreSQL requires an UPDATE privilege for that clause,
while the declared gap ACL deliberately allows only SELECT/INSERT/DELETE.
All gap writers enter through scope-locking completion or recovery paths.
The smallest correction therefore removes the redundant child lock, not the
least-privilege boundary. A two-connection commit/rollback witness verifies
that another transaction cannot acquire the same scope, observes no
uncommitted contribution, and records exactly one gap and quota increment.
Revisit this proof before introducing any writer that bypasses that scope.

Cleanup witnesses distinguish administrative fixture writes from application
authority: configuration and cleanup execute as the restricted runtime role;
only controlled setup or corruption uses the administrative connection.
Otherwise a test can fail at runtime-principal admission before exercising
its intended expiry, rollback or concurrency predicate. Native GitHub results
must close these oracles; static admission alone does not.

API and UI controls remain disabled or absent until the public-adapter and
maintenance path is implemented. Native GitHub success, real Swarm lifecycle
and production capacity are separate receipts. Reconsider the single pending
page only if measured throughput cannot meet the historical-import budget;
parallelism must retain these transactional and population invariants.
