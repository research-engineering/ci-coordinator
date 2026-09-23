# Unactivated Target Authority Evidence

Status: as-built dormant Stage C capability

Date: 2026-08-31

Implemented requirement: `REQ-CI-RUNTIME-033`

## 1. Responsibility

`target_authority_evidence` retains one replayable proof that the dormant
target-authority relation closed for one frozen target epoch. It owns neither
the relation algebra nor either producer. It cannot read GitHub, authorize a
target owner, persist production authority, activate omission, or mutate a
repository.

```text
RelationClosed(B, D, R, O, T, P) = C
and ExactRetainedArtifacts(B, D, U, W, S, CR, CO, OP, R, O, T, P, C)
  -> UnactivatedEvidenceBundle

UnactivatedEvidenceBundle -/-> ProductionAuthorityReceipt
UnactivatedEvidenceBundle -/-> SelectedExecutionAllowed
```

The bundle is a Stage C review artifact. `REQ-CI-RUNTIME-030` remains deferred
and continues to own successor persistence, current provider evidence,
generation fencing, runtime admission, and production cutover.

## 2. Why One Canonical File

The admitted representation is one canonical JSON file with each exact owner
document embedded as a JSON object. Every admitted owner codec already emits
canonical JSON and rejects a non-canonical round trip. Therefore exact owner
bytes are recovered by canonical re-encoding without a second binary-to-text
codec.

Let `F` be a multi-file directory and `J` the single-file representation.

```text
PartialPublicationStates(J) = {absent, complete-file}
PartialPublicationStates(F) includes proper subsets of required files

RequiredAtomicity(J) needs one same-directory link operation
RequiredAtomicity(F) needs a manifest-last protocol plus recovery semantics
```

The bundle is offline, bounded, and non-production. Therefore the reduced
recovery state space dominates a multi-file layout. Base64 would add a
four-thirds size expansion and another rejection algebra without preserving
any additional information for the closed all-JSON role set. A custom archive
or binary framing format would likewise add codec authority without a current
consumer that needs streaming.

## 3. Closed Artifact Set

Exactly these roles are admitted, in this canonical order:

1. `phase_zero_baseline`;
2. `transition_delta`;
3. `expected_relation`;
4. `workflow_manifest`;
5. `source_binding`;
6. `registration_candidates`;
7. `observation_candidates`;
8. `owner_projection_policy`;
9. `registration_inventory`;
10. `observation_inventory`;
11. `raw_candidate_domain`;
12. `projection_ledger`; and
13. `relation_closure`.

Every item binds its exact owner schema, byte count, SHA-256, artifact-domain
digest, and embedded document.
Missing, duplicate, extra, reordered, incorrectly typed, or digest-mismatched
items reject the whole bundle.

This set is sufficient to replay the accepted Stage C relation:

```text
Decode(B, D) -> Apply(B, D) = U
Decode(R, O, T, P) -> Close(B, D, R, O, T, P) = C
Decode(W, S, CR, CO, OP) -> producer-domain and source-binding checks
```

It is intentionally insufficient to regenerate the producer candidates from
raw target configuration or claim live provider truth. Those bytes belong to
the later production receipt and its external owners, not this dormant bundle.

## 4. Identity And Replay Admission

The manifest binds one exact subject and adapted target epoch. Its
`authorityState` is the literal `unactivated` and is not caller-selectable.

```text
ArtifactDigest(role, schema, bytes)
  := SHA256("ci-coordinator.target-authority-evidence-artifact/v1"
            || 0x00 || role || 0x00 || schema || 0x00 || bytes)

BundleDigest(body)
  := SHA256("ci-coordinator.unactivated-target-authority-evidence/v1"
            || 0x00 || CanonicalJSON(body_without_bundle_digest))
```

Decoding is affirmative only when all of the following hold:

1. strict JSON and canonical byte equality;
2. exact schema and key sets;
3. canonical role order and exact role closure;
4. per-item canonical owner bytes, byte count, SHA-256, and domain digest;
5. successful owner-codec decoding for every item;
6. one subject and one adapted epoch across every artifact;
7. exact transition replay to the retained expected relation;
8. exact relation replay to the retained closure;
9. workflow manifest and source binding equality;
10. owner policy equality with both candidate-domain digests; and
11. exact re-encoding of every decoded owner value.

Depth and node admission is a lexical preflight over the bounded UTF-8 input.
It completes before the standard JSON decoder can materialize an object tree;
object keys do not consume the value-node budget, matching canonical encoding.

Internal consistency is not production truth. It proves only that the retained
bytes replay the frozen relation admitted by the current owner codecs.

## 5. Publication State Machine

The command publishes by content identity into an existing operator-selected
directory:

```text
Absent
  -> StagedPrivateFile
  -> FlushedAndFsynced
  -> LinkedAs(<bundle-digest>.json)
  -> ParentFsynced
  -> TemporaryRemoved
  -> ParentRefsynced
  -> Published
```

The final filename is derived from the verified bundle digest. Publication
uses a same-directory hard link as an atomic no-overwrite transition. Existing
final paths, symlinked path components, non-regular inputs, unsupported link or
directory-fsync semantics, short writes, or cleanup uncertainty fail closed.
A failure after temporary-file creation but before ownership is returned
attempts both descriptor close and private-name removal; failure of either
cleanup step is an explicit rejection.
A crash before the link leaves no final bundle. A crash after the link leaves
a complete, already-fsynced final bundle and may leave only a non-authoritative
temporary file.

The command never follows a source or destination symlink, never overwrites an
existing final artifact, and emits no artifact bytes to stdout or stderr.

## 6. Bounds

| Resource                         |                         Bound |
|----------------------------------|------------------------------:|
| Artifact roles                   |                13 exact roles |
| Per owner document               |                        64 MiB |
| Aggregate decoded artifact bytes |                        96 MiB |
| Canonical bundle bytes           |                       128 MiB |
| Bundle JSON depth                |                            24 |
| Bundle JSON nodes                |                     8,388,608 |
| Absolute path bytes              |                         4,096 |
| Output filename                  | fixed digest-derived basename |

Each owner codec applies its smaller existing bound. Exceeding any bound
rejects the bundle and leaves native FullCI unchanged. Byte admission precedes
UTF-8 decoding; structural admission precedes object-tree materialization.

## 7. Failure Algebra

```text
EvidenceOutcome := Admitted(UnactivatedEvidenceBundle)
                 | Rejected(EvidenceFinding+)

PublicationOutcome := Published(path, bundle_digest)
                    | Rejected(publication_code)
```

Failure codes distinguish JSON/canonicalization, role closure, item identity,
owner codec, subject, epoch, transition replay, relation replay, source
binding, producer domain, input stability, output ownership, atomic
publication, cleanup, and command arguments. Invalid or unknown command
arguments produce only a redacted canonical JSON rejection and never echo the
argument vector. No rejection path returns an affirmative bundle or publication
receipt.

## 8. Ownership And Dependencies

| Surface                               | Sole responsibility                                      |
|---------------------------------------|----------------------------------------------------------|
| `model.py`                            | Closed roles, artifact values, and bundle identity.      |
| `codec.py`                            | Strict canonical bundle encoding and decoding.           |
| `replay.py`                           | Cross-owner replay and exact-equality admission.         |
| `file.py`                             | Bounded no-follow input and atomic private publication.  |
| `cli.py`                              | Stable command arguments and redacted terminal outcomes. |
| `target_authority_producers/codec.py` | Producer-owned candidate and policy codecs.              |

The package may depend on public codecs from `target_authority_relation`,
`target_authority_producers`, and `workflow_authority`, plus kernel canonical
JSON and hashing. It may not depend on HTTP, SQL, runtime composition,
production admission, provider integrations, environment settings, clocks, or
planning.

## 9. Formal Safety Argument

Assume a bundle is admitted but its retained Stage C closure is false. At least
one of these propositions must hold:

1. an artifact byte differs from its manifest identity;
2. a decoded owner artifact differs from its retained bytes;
3. `Apply(B, D) != U`;
4. `Close(B, D, R, O, T, P) != C`; or
5. source or producer domains cross subject, epoch, or manifest identity.

Checks 4.1 through 4.11 reject each case. Therefore:

```text
Admitted(bundle) -> ReplayedStageCClosure(bundle)
```

The converse production claim is invalid:

```text
ReplayedStageCClosure(bundle)
  -/-> CurrentProviderTruth
  -/-> ProductionOmissionAllowed
```

## 10. Dormant Revision Triggers

- Moving or deleting durable semantic authority activates per-copy
  conservation for live data, WAL/archive, backups, quarantine, and key
  bindings. This file publication does not move an authority owner.
- Adding mutating GitHub effects activates the durable
  `Reserved -> Applying -> Uncertain -> Reconciled/Reissued` protocol with
  generation fencing. This capability performs no provider I/O.
- A real owner transfer continues to require the already implemented
  independent registration and observation inventories compared by canonical
  full-row bytes, never IDs alone.

## 11. Non-Claims And Revision

The bundle does not prove pre-adaptation capture, pilot target owner approval, live
provider completeness, test adequacy, database durability, backup
conservation, signer authority, shadow safety, generation fencing, deployment,
production omission, performance savings, or a five-minute workflow SLO.
Owner control of the publication directory is limited to the admitted POSIX
owner and mode predicate; ACL, mount-policy, host-integrity, and remote
filesystem guarantees are not attested.

Revise this contract when the role set, representation, bundle identity,
replay relation, publication state machine, bounds, or production-authority
relationship changes.
