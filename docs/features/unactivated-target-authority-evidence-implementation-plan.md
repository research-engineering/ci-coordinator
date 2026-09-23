# Unactivated Target Authority Evidence Implementation Plan

Status: executed dormant Stage C implementation plan

Date: 2026-08-31

Design authority:
[Unactivated Target Authority Evidence](../architecture/modules/target-authority-evidence.md)

## 1. Objective

Complete Stage C retention without making the deferred Stage D production
cutover partially active.

```text
Current := RelationKernel + WorkflowAuthority + IndependentProducers
Delta   := StrictProducerCodecs + ReplayableBundle + AtomicOfflinePublication

ObservableRuntimeDelta(Current, Delta) = empty
```

## 2. Preconditions

1. PR #105 is present on `master` and its post-merge Full Check is green.
2. `REQ-CI-RUNTIME-030` remains deferred.
3. Native FullCI remains the sole target execution authority.
4. No database, HTTP, runtime, or provider-mutation owner is in this slice.

## 3. Implementation Sequence

### Step A: Correct owner codecs

1. Add strict round-trip codecs for registration candidates, observation
   candidates, and owner projection policy.
2. Reject duplicate keys, unsafe JSON scalars, unknown fields, wrong nested
   relation shapes, non-canonical bytes, and every owner constructor failure.
3. Export only the three complete artifact codecs and their schema constants.

Exit: every producer artifact round-trips exactly; one-field mutations either
change the content identity or are rejected.

### Step B: Implement the closed bundle

1. Define the 13-role closed artifact algebra and aggregate bounds.
2. Build bundles only from exact typed owner values.
3. Embed exact canonical owner documents with byte count, SHA-256, domain
   identity, and owner schema; reject any owner round-trip mismatch.
4. Preflight byte, depth, and node bounds before JSON object materialization,
   then decode strict canonical JSON and reconstruct every owner artifact.
5. Replay transition, relation closure, source binding, and producer-domain
   equalities before returning an admitted value.

Exit: removing, adding, swapping, duplicating, relabelling, reordering, or
mutating any artifact cannot produce an admitted bundle.

### Step C: Add offline publication

1. Read one bounded regular input file through a stable no-follow descriptor.
2. Decode and re-encode before publication.
3. Stage private bytes in the destination directory, flush and fsync them,
   atomically hard-link the digest-derived final name without overwrite, fsync
   the directory, and remove the temporary name. Attempt both close and unlink
   for any temporary file whose mode initialization fails before ownership is
   returned, and reject any cleanup uncertainty.
4. Emit only a redacted JSON status containing code, digest, and final path;
   invalid arguments must not reach the default echoing parser error path.
5. Return a nonzero status for every admission or publication failure.

Exit: a final path is absent or contains one complete verified bundle; no
partial final path can be admitted.

### Step D: Wire repository proof

1. Add `REQ-CI-RUNTIME-033` with explicit non-claims.
2. Add Proofkit requirement bindings for every owner, implementation, and test
   surface.
3. Add architecture traceability, context map, dataflow, index, module
   ownership, and import-boundary entries.
4. Correct stale roadmap statements after PR #105 and keep Stage D deferred.
5. Add the console entrypoint, classify it as non-runtime in the finite
   entrypoint disposition, and add no runtime dependency.

Exit: every changed owner surface is reachable through the documentation and
Proofkit graphs, and forbidden dependencies fail mechanically.

### Step E: Falsify

Required witness classes, with owner-level suites supplying owner semantics:

- producer codec round-trip and strict negative corpus;
- exact 13-role closure and canonical ordering;
- artifact deletion, duplication, addition, relabelling, swap, document
  mutation, count mismatch, digest mismatch, and non-canonical owner bytes;
- baseline, delta, expected, inventory, raw-domain, projection, closure,
  manifest, binding, candidate-set, and policy one-field mutations;
- same subject with wrong epoch and same IDs with different full rows;
- stale source binding and candidate-domain mismatch;
- aggregate and owner-specific resource overflows, including proof that
  structural overflow rejects before object-tree materialization;
- input symlink, non-regular input, changing input, destination symlink,
  output collision, short write, link failure, fsync failure, and cleanup
  failure;
- CLI argument and execution redaction with nonzero failure outcomes; and
- import-boundary, requirement, Proofkit, documentation, lint, type, unit,
  integration, mutation, and branch-head gates.

## 4. Pilot Boundary

The first real bundle must use a native Phase-0 baseline captured from an
unadapted pilot target owner epoch and a separately approved adapted transition. The
existing exploratory pilot branch may supply fixtures, but it cannot
retroactively become the Phase-0 owner.

This PR may include frozen synthetic or repository-owned test fixtures. It does
not open or merge a pilot target pull request, change pilot target required checks, or execute
selected CI. A later shadow canary retains native FullCI and measures wall
time, runner occupancy, selected jobs, fallback reason, and unsafe omissions.

## 5. Rollback

Before production admission, rollback removes the offline command and bundle
consumer. No runtime or target behavior changes because no production path
imports the package. Existing evidence files remain inert and unactivated.

## 6. Completion Predicate

```text
Complete := ProducerCodecsRoundTrip
         and BundleRoleClosure
         and ExactReplayAdmission
         and AtomicPublicationWitnessed
         and ImportBoundariesEnforced
         and ProofGraphClosed
         and FullCheckGreenOnExactHead
         and FreshReviewHasNoP0ToP2
```

## 7. Non-Claims

Completion does not claim live provider truth, production database retention,
successor receipt admission, generation cutover, deployment readiness,
production omission, measured CI savings, or a five-minute worst-case workflow.
