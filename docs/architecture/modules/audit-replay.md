# Audit Replay Module Specification

Status: module specification

Date: 2026-07-16

## 1. Owned Invariant

Every durable decision is recorded in an append-only ledger and can be replayed
from immutable evidence without live GitHub access.

## 2. Public API And Ownership

The bounded context separates deterministic event semantics from durable I/O.
Its domain API is:

```text
prepare_audit_event(event_input) -> PreparedAuditEvent
build_prepared_audit_event(prepared, previous_event) -> AuditEventRecord
append_event(existing_records, event_input) -> AuditAppendResult
verify_audit_chain(records) -> AuditChainVerificationResult
replay_audit_evidence(records, filter, include_payload) -> AuditReplayResult
replay_subject(records, subject_type, subject_id) -> AuditReplayResult
list_subject_events(records, subject_type, subject_id) -> AuditEventRecord[]
scan_audit_replay(repository, filter) -> AuditReplayScan
iter_verified_replay_events(repository, scan, include_payload)
  -> AsyncIterator[AuditReplayEventSummary]
```

`append_event` is the pure append classifier used by deterministic callers and
tests. Durable writes use this port:

```text
AuditEventRepository.append(prepared) -> AuditAppendResult
AuditEventRepository.snapshot() -> AuditLedgerSnapshot
AuditEventRepository.load_page(after, through, limit) -> AuditEventRecord[]
```

`persistence.audit_repository` implements the port inside a compatibility-admitted
PostgreSQL unit of work. It serializes append through the ledger-head row, stores
the exact prepared payload bytes, and commits the event with its owning state
transition when the event class is pair-owned. `replay_cli` freezes one exact
head, verifies bounded pages through that head, and then performs a second
bounded pass that streams the already verified epoch. It never materializes the
production ledger as one tuple. The audit domain therefore imports neither
SQLAlchemy nor GitHub adapters.

## 3. Replay Semantics

Replay means complete-ledger integrity verification followed by an evidence
projection for the selected filter. Subject filtering cannot hide corruption in
another part of the chain. Recomputing a plan or another domain projection from
the retained evidence belongs to an explicitly versioned subject-specific replay
handler, not to generic ledger verification.

## 4. Ledger Rule

```text
payload_hash = hash(canonical_json(payload))

input_hash = hash({
  schema_version,
  idempotency_key,
  subject_type,
  subject_id,
  event_type,
  payload_hash
})

event_hash = hash({
  schema_version,
  idempotency_key,
  subject_type,
  subject_id,
  event_type,
  created_at,
  actor,
  sequence,
  previous_event_hash,
  payload_hash,
  input_hash
})

next.previous_event_hash = event_hash
```

`created_at` and `actor` are event provenance, not idempotent input identity;
they affect `event_hash` but not `input_hash`.

Verification must validate the complete chain before subject filtering.

Production replay freezes `(lastSequence, lastEventHash, maximumSequence)` from
one repository snapshot. It rejects a head whose sequence differs from the
maximum stored sequence, reads only `sequence <= lastSequence`, and advances
only after verifying one page against the previous normalized record. The
PostgreSQL schema owns global idempotency-key uniqueness; page verification owns
content, sequence, and link continuity.

```text
N := frozen lastSequence
k := 0
while k < N:
  page := load_page(after=k, through=N, limit=P)
  verify_extension(previous, page)
  k := page[-1].sequence

PeakRetainedRecords <= P
ConcurrentAppend(sequence > N) does not change ReplayEpoch(N)
```

The CLI verifies the complete epoch before emitting an affirmative report. Its
second pass re-verifies every page and streams matching events directly to the
output writer. Thus repository replay memory is independent of ledger
cardinality, apart from bounded page and serialization buffers. A caller-owned
in-memory writer may itself retain output; that is not a repository replay
memory claim.

The audit wire scalar domain composes the kernel JSON domain with these event
schema predicates, where `M = 2^53 - 1`:

```text
AuditTimestamp(s) iff:
  s matches YYYY-MM-DDTHH:mm:ss.sssZ
  and 0001 <= YYYY <= 9999
  and s is a valid proleptic-Gregorian UTC instant
  and canonical_millisecond_utc(parse(s)) = s

AuditSequence(n) iff:
  n is an integral JSON number
  and 1 <= n <= M

AuditText(s) iff:
  s is non-empty
  and s contains only Unicode scalar values

AuditInputShape(input) iff:
  input has exactly the seven declared input fields
  and every field is an enumerable data property
  and input has no symbol fields or implicit serialization behavior

AuditPayloadScalarDomain(payload) iff:
  every scalar leaf in payload is a null, boolean,
      AdmissibleJsonNumber, or Unicode scalar string

AuditPayloadWithinResourceLimits(payload, profile) iff:
  payload satisfies the kernel-owned AdmissibleJsonResources measure
      parameterized by profile

AuditPayloadWithinByteLimits(payload, profile) iff:
  length(canonical_json_utf8(payload)) <= profile.maxPayloadCanonicalBytes

AuditTextWithinByteLimits(s, profile) iff:
  length(utf8(s)) <= profile.maxVariableTextUtf8Bytes

AuditInputAdmissionV1(input) iff:
  AuditInputShape(input)
  and AuditPayloadScalarDomain(input.payload)
  and AuditPayloadWithinResourceLimits(
      input.payload, ci-audit-event-json-resources/v1)
  and AuditText(input.idempotency_key)
  and AuditText(input.subject_id)
  and AuditText(input.event_type)
  and AuditText(input.actor)
  and AuditTimestamp(input.created_at)

AuditByteAdmissionV1(input) iff:
  AuditPayloadWithinByteLimits(
      input.payload, ci-audit-event-persistence-bytes/v1)
  and every variable AuditText field satisfies
      ci-audit-event-persistence-bytes/v1

PersistableAuditInputV1(input) iff:
  AuditInputAdmissionV1(input)
  and AuditByteAdmissionV1(input)

AuditRecordAdmissionV1(record) iff:
  record has exactly the fourteen declared enumerable data properties
  and AuditInputAdmissionV1(ProjectInput(record))
  and AuditSequence(record.sequence)
  and every hash and derived id matches its declared format

PersistableAuditRecordV1(record) iff:
  AuditRecordAdmissionV1(record)
  and AuditByteAdmissionV1(ProjectInput(record))
```

An integral float sequence is normalized to an integer because JSON has one
number type and the wire representation cannot preserve host numeric subtypes.
Booleans, zero, non-integral numbers, and unsafe integers are not sequences.
In-domain `ci-audit-event/v1` records retain their bytes and hashes;
out-of-domain records are rejected rather than rehashed.
`AuditPayloadScalarDomain` and `AuditPayloadWithinResourceLimits` are orthogonal
predicates: neither implies the other. Their conjunction is owned here only as
audit input admission. The generic measure is owned by `modules/kernel.md`; the
exact audit-v1 policy and failure shape are owned solely by
`docs/specs/ci-coordinator-core/audit-json-resource-profile.v1.json`.
The independent persistence-byte predicate and its exact limits are owned solely
by `docs/specs/ci-coordinator-core/audit-persistence-byte-profile.v1.json`.
Structural admission does not imply byte admission, and byte admission does not
replace scalar or structural admission.
Every native entrypoint must produce the same stable resource failure and must
not hash an exhausted payload. Diagnostic precedence is separate from logical
admission because conjunction itself is commutative:

```text
FirstAuditInputFailureV1:
  exact input shape -> metadata domain/bytes -> payload scalar/resource admission
  -> payload byte admission

FirstAuditRecordFailureV1:
  exact record shape -> payload scalar/resource/byte admission -> metadata domain/bytes
  -> hash and chain verification
```

Record replay prioritizes bounded payload admission so corrupt metadata cannot
force host-dependent work or hide a typed resource failure. Runtime constants in
`audit_replay/json_resources.py` are executable projections of the profile, so
unrelated kernel callers are not narrowed by the audit-only profile. The
v1 profile defines the initial published domain. Once v1 is retained outside
disposable test state, any domain change requires a v2 reader and an
explicit migration under `REQ-CI-CORE-010`.

Construction and verification must take one exact detached input or record
snapshot before validation and hashing. Stores must use only the admitted
record after construction; rereading caller-owned properties would invalidate
the snapshot proof. Replay filtering, summaries, and snapshot loading must use
the normalized records returned by the same verification operation, never the
original caller-owned records. The internal `snapshot_audit_event_input`
admission primitive exists so recorder orchestration can detach the complete
attempt, including nested payload data, before it reads an existing ledger.
Mapping admission snapshots the key sequence, rejects duplicate keys, and reads
each required value at most once. Replay snapshots and validates the exact
filter plus payload-disclosure options before any asynchronous store access and
applies one detached control state to the whole report.
Any producer that persists decision state before appending its audit event must
admit and detach the complete audit input before the first store mutation.
Resource exhaustion therefore cannot leave a planning context without its
corresponding audit attempt; transaction-wide atomicity remains a persistence
obligation.

Durable repositories accept only an exact single-use prepared audit input produced before
connection checkout and transaction entry. Preparation owns one detached
canonical payload byte sequence plus the payload and input hashes derived from
those bytes. Building a record transfers the detached payload snapshot exactly
once; reusing a consumed capability is a typed domain error. Ledger-head
serialization may add only predecessor-dependent facts;
it must not traverse, canonicalize, or reread caller-owned payload data. Stored
payload bytes are the prepared bytes, not a later reserialization.
That obligation is admitted by `REQ-CI-CORE-012`: context and audit
classification occur inside one serialized store-owned commit. An audit
idempotency conflict commits neither effect. A retry that finds only one effect
repairs the missing counterpart only when retained facts uniquely reconstruct
it; only an already complete pair is a duplicate. An issued plan can reconstruct
its missing issuance event because the full signed record is retained. An
issuance event without the signed envelope cannot reconstruct the missing plan
and therefore produces a typed conflict rather than a newly signed substitute.
Likewise, dynamic verification evidence does not retain source delivery
provenance, so an audit-only dynamic context is not reconstructible and must
return conflict. Only retained dynamic context state can reconstruct its audit
counterpart.
The caller supplies only subject state. The store-owned pair constructor derives
the audit attempt from the retained record; accepting a caller callback would
reintroduce an independent write capability that could survive rollback.
Pair-owned event types are rejected by generic append, so audit-only `issued`
or `verified` states cannot be created through the public ledger port.

## 5. Event Classes

- config candidate submitted.
- config validation failed.
- config epoch activated.
- plan requested.
- planning context built.
- deterministic plan produced.
- verifier result produced.
- signed envelope issued.
- runner snapshot captured.
- shard plan selected.
- workflow observation recorded.
- operator override applied.
- replay performed.

## 6. Failure Behavior

```text
MissingInteriorEventWithSuccessor => replay failed
MutatedEvent => replay failed
ReorderedEvent => replay failed
UnsupportedEventVersion => replay failed or migration required
MissingTerminalEventWithoutTrustedCommitment => not detectable by local chain verification
SnapshotHeadMismatch => replay failed
MissingOrChangedPageDuringOutputPass => replay failed; no valid completion
```

## 7. Proof Obligations

| Obligation                                                | Falsifier                                                                                             |
|-----------------------------------------------------------|-------------------------------------------------------------------------------------------------------|
| Chain corruption is detected before filtering.            | Subject replay passes after unrelated event mutation.                                                 |
| Production replay memory is page-bounded.                 | Repository or CLI retains all `N` events before verification or output.                               |
| Replay epoch is stable under append.                      | An event with sequence greater than the frozen head changes the report.                               |
| Output cannot bypass complete verification.               | A positive report is emitted before the first pass reaches and matches the frozen head.               |
| Sequence and link evidence detect non-terminal deletion.  | A retained successor accepts a missing predecessor.                                                   |
| Replay does not require live GitHub.                      | Replay calls GitHub API.                                                                              |
| Versioned events are admitted.                            | Unknown event shape silently ignored.                                                                 |
| Scalar leaf admission matches the machine profile.        | A shallow admitted structural context hashes an out-of-domain scalar.                                 |
| Structural payload admission matches the machine profile. | Depth 65 or node 10,001 is hashed.                                                                    |
| Exhaustion precedes hashing.                              | A resource-exhausted payload reaches `payload_hash`, `input_hash`, or `event_hash`.                   |
| Persistence-byte admission matches the machine profile.   | A metadata or canonical payload boundary differs from the reviewed corpus.                            |
| Durable admission precedes transaction entry.             | A byte-invalid input checks out a connection, begins a transaction, hashes, locks, or executes SQL.   |
| Stored payload identity is exact.                         | The repository recanonicalizes an input instead of writing the prepared bytes used by `payload_hash`. |
| Caller mutation cannot change admitted identity.          | A store rereads an accessor or proxy property after construction.                                     |
| Paired subject state and audit evidence commit together.  | A persistence failure exposes either member of the pair.                                              |
| Pair-owned event classes have one append authority.       | Generic append retains `dynamic-ci-plan.issued` or `dynamic-ci-plan.verified`.                        |
| Decision inputs are sufficient.                           | Plan cannot be rebuilt from ledger.                                                                   |

## 8. Implementation Mapping

Domain owners:

```text
ci_coordinator/audit_replay/event.py
ci_coordinator/audit_replay/chain.py
ci_coordinator/audit_replay/recorder.py
ci_coordinator/audit_replay/replay.py
ci_coordinator/audit_replay/paged_replay.py
ci_coordinator/audit_replay/checkpoint.py
ci_coordinator/audit_replay/checkpoint_admission.py
ci_coordinator/audit_replay/subjects.py
ci_coordinator/audit_replay/ports.py
ci_coordinator/audit_replay/json_resources.py
```

The audit-replay module implements pure event construction, idempotency
classification, complete chain verification, replay filtering, and store-port
semantics. It does not reconstruct subject-specific commands or recompute their
domain projections.

A complete valid paged replay retains its exact terminal event and can produce
one content-addressed checkpoint candidate bound to database identity, artifact,
ledger schema, positive JSON-safe terminal sequence, event identity and hash,
and observation time. The terminal identity is admitted only when
`lastEventId = "audit_" + lastEventHash[0:32]`; independently valid field
formats cannot authenticate a contradictory pair. `checkpoint_admission`
separately verifies canonical
Ed25519 evidence and validity; an admitted clock-skew interval is applied
identically to admission and the returned capability's validity predicate.
Candidate generation does not prove that an external system signed, retained,
monitored, or can recover the checkpoint.

A bounded non-empty successor verifier proves sequence, hash-link, event
content, and suffix-local idempotency uniqueness from the authenticated tip.
It cannot prove idempotency uniqueness against the removed prefix without the
storage-enforced invariant already required by the repository contract.

Adapter owners:

```text
ci_coordinator/persistence/audit_repository.py
ci_coordinator/persistence/unit_of_work.py
ci_coordinator/replay_cli/__init__.py
```

The persistence module owns durable append, transaction coupling, and ledger
recovery. The CLI owns argument admission, database lifecycle, stable JSON output,
and exit codes; it owns no replay semantics.

## 9. Acceptance Tests

- event mutation, non-terminal deletion, and reorder tests.
- filtered evidence replay of a plan subject without network access.
- event version rejection tests.
- hash-chain golden fixtures.
- payload number, Unicode scalar, timestamp, and sequence boundary fixtures.
- exact payload depth/node boundary fixtures with stable resource codes.
- exact input-shape, metadata Unicode, and single-snapshot adversarial tests.
- cross-page mutation, missing-page, oversized-page, frozen-head append, and
  bounded streaming-writer tests.
- forged scan, re-signed contradictory checkpoint identity/hash, checkpoint
  signature, foreign identity, expiry, successor-link, and bounded
  successor-count tests.

## 10. Non-Claims

The implemented local witnesses do not prove deployment identity, backup,
restore, failover, production availability, semantic plan recomputation, or
sufficiency of current event payloads for every future subject-specific replay
handler. A generated checkpoint candidate does not prove external custody.
Without its independently authenticated and retained envelope, local chain
verification does not detect terminal truncation or a fully recomputed suffix.
In-memory records detach payloads from caller-owned input, but nested JSON
values are not a deep-immutable host representation; persistence and replay
boundaries therefore snapshot and re-admit them. Native conformance covers only
the admitted executable cases, including the normative scalar, structural, and
byte boundaries. Structural limits do not bound ledger cardinality, transport
bodies, or total durable storage.
