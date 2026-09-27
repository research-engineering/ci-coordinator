# Managed Check Lifetime

Status: design

## Scope And Decision

This additive design owns managed check installation and controlled participant
lifetime. It does not replace historical developer or consumer-laboratory designs.
The execution order and qualification are in the
[implementation plan](managed-check-lifetime-implementation-plan.md).

An install-capable selected check holds its root-specific exclusive dependency
lease before admission; portable readers retain shared leases. Only a reached
installer creates pending state, and completion revalidates graph and export
identity. A later quality failure does not invalidate a completed installation.

Controlled participants inherit admitted lease descriptors across their owned
process sessions and receive independent stop channels carrying the first fixed
force deadline. They do not infer private authority from ambient environment.
Popen handle acceptance has an existing cleanup owner before interruptible work.
An owner-local pending buffer remains visible independently of interruptible
mode. A currently active error defers an owned signal until an existing
checkpoint. Each context records actual propagation, not an ambient caught
error. Normal interruptible exit flushes pending cancellation inside the caller's
catch/callback; the outer normal exit is a final safeguard. Propagating primary
exceptions, first stop time and existing hard bounds remain authoritative.

The exact-image lab admits its private receiver before execution, preserving
source-to-image identity without importing worktree scripts into the image.
A managed receiverless image rejects before module execution. Unmanaged public
entry, argv and receipt behavior are conserved by modular controls; historical
unmanaged end-to-end execution is not claimed. Owned Git wrapper removal is
empty-only; lab recursive cleanup runs in its bounded owned subprocess, with
typed residual failure and no later implicit recursive finalizer.

## Bounded Evidence Retention Admission

The finite nine managed lifecycle mutants use the existing named-suite runner,
manifest admission and pytest/JUnit classifier. Immutable suite configuration
opts in only this suite. Other suite reports and execution remain unchanged.
Retention does not strengthen the classifier into a general causal matcher.

Capture occurs after each returned witness and before its XML can be overwritten,
the patch can be restored, or detached cleanup can remove it. The only output
root is the calling repository's ignored `.ci-native/mutations/` directory, then
the fixed suite name, one fresh invocation UUID hex component, a member of the
literal nine-ID set, and baseline/mutant. The invocation is allocated once per
run with exclusive mkdir under no-follow parents. Concurrent starts cannot
reuse a directory. A later run, even at the same source revision, preserves old
evidence and allocates a different invocation; capture requires that directory
to exist rather than recreating a missing invocation.
Directory traversal, symlink parents, unsafe IDs and existing phase destinations
fail capture; no recursive deletion or arbitrary destination is provided.

Read XML with the existing bounded regular-file owner and its 8 MiB report cap.
Retain bounded output only where needed for failed-witness diagnostics; record
stream byte counts and digests without embedding or duplicating raw buffers in
JSON. The existing 10 MiB combined witness-output limit is not enlarged.
Each phase record and public summary bind the same invocation, source revision,
and manifest; mixed-invocation records are unqualified even if their ID/phase
population is otherwise complete. Each phase additionally binds patch,
source file/phase bytes, selected command, XML and retained output digests.
Missing, changed, oversized or unwritable requested capture is unqualified and
prevents aggregate success, without changing the existing kill classifier,
primary cancellation or cleanup error priority. Artifact upload is suite-local,
always attempted and retained for seven days. Raw evidence is for exact
node/phase/assertion review, not automatic proof of causality.

## Owner Boundaries

| Owner | Admitted delta and protected observations | Independent falsifier |
| --- | --- | --- |
| Controlled participants and dependency installation | Preserve lease ownership, first stop time, primary failures and installation-phase admission across child processes. | Exact inherited descriptors, independent channels, deadline cuts and installation/export identity controls. |
| Existing mutation runner/specs; small evidence file owner | Opt-in fixed-root phase capture and an independent aggregate capture gate. Reuse regular-file admission; no new process or classifier framework. | Capture before overwrite/restoration/cleanup; exact bindings; bad path/identity, missing/overflow and default-off controls. |
| Existing pytest report owner | Expose only its current read bound if required for reuse; do not change report admission. | Existing report/phase/selection tests remain unchanged. |
| Requirements/routes/tuples/catalog | Add only DEV015, RUNTIME028 and PROOFKIT008 lifetime clauses, exact suite9 and missing test selections. Preserve old-branch unrelated metadata and public guards. | Literal nine-row inventory, exact command relation and all budget ancestors. |
| Workflow and derived sources | Reuse mandatory mutation matrix, pinned Node and existing generator; retain finite evidence artifacts. | Exact matrix membership, upload scope/retention, generated input identity and unchanged required gate. |

### Invocation Repeatability

A fixed suite/ID/phase layout would let the retained first baseline prevent a
second run. Fresh invocation identity separates those lifetimes without
deleting evidence or changing the existing capture owner and runner.
Two complete consecutive runs at equal or changed HEAD, concurrent allocation,
same-invocation duplicate refusal and foreign-invocation summary refusal are
required controls. Participant lifetime, suite patches, classification,
budgets and fixed-root seven-day upload remain unchanged. This is not a generic
storage framework or evidence of an observed native failure.

Local execution is limited to owner-admitted static checks and generation.
Native tests and mutation qualification remain GitHub-only. Installation and
executable admission compose with complete bounded text-policy inputs and
active-environment resolution; neither may restore a PATH fallback or discard
an independent current requirement.

## Alternatives And Nonclaims

Successful Dev Container portable checks expose their existing ordered quality
command timings through a bounded diagnostic projection. Only admitted command
IDs, finite nonnegative elapsed values and explicit boolean outcomes are emitted.
Missing, duplicate, malformed or reordered observations remain incomplete;
diagnostic failure cannot change the check, cancellation or cleanup result.
This uses the same quality-plan execution owner, not another measurement engine.
Timing completeness is neither an authenticated receipt nor a speedup claim.

Whole-check pending would needlessly invalidate healthy completed scopes.
A new process framework, ambient carrier, renewed grace, private interpreter
introspection or blanket recursive cleanup is unnecessary for this finite scope.
Reusing the existing classifier with retained bounded evidence is cheaper than
introducing a generic expected-diagnostic schema here. Manual causal admission
must reject setup, network, unrelated assertion and outer-timeout failures.

No every-bytecode interruptibility, arbitrary noncooperative handler, after-owner
immunity, daemon/FD-dropping or all-supervisor abrupt-death guarantee is claimed.
Windows, hostile same-user isolation, independent container resource cleanup,
production readiness and global TEST6 closure remain outside this design.
Native process qualification distinguishes the supervisor's expected signal
exit from the child's physical terminal outcome; one does not prove the other.
