# Developer Watch Phase Admission Plan

Status: implementation authorized; native acceptance pending

Date: 2026-09-13

The [design](developer-watch-phase-admission.md) owns phase semantics and
writer-readiness. This plan owns the single cohesive batch and its acceptance.
Baseline: `0cd037d4ba98be6a28ec4fa22c752eed7f192862`.

## Ordered Batch

1. Record design and writer-readiness before source changes. Preserve earlier
   convergence documents, unrelated worktrees and all provider state.
2. Add the successful-empty Compose absence subtype. Keep normal identity and
   file access strict, with bounded diagnostics and no raw provider output.
3. Narrow watch transition observation to that subtype. Use one current vector
   at expected rebuild handoffs; retain source/output, identity and budget guards.
4. Replace generic-error-as-transient test oracles. Add empty-to-valid, permanent
   absence, malformed/multiple/provider/foreign failure, round-coherence and
   source-replacement counterexamples. Keep existing readiness assertions.
5. Add fixture-only supervision and transfer barriers. Verify SIGINT/SIGTERM
   cleanup with actual receipts after supervision and ambiguity fencing during
   interrupted transfer. Keep unrelated signal and forced-stop witnesses.
6. Run only authorized static Ruff/mypy checks. Commit the coherent source,
   tests and these documents. Return SHA, static evidence and unresolved native
   predicates; no local test execution, collection, Docker, server or publication.

## Acceptance And Ownership

Root independently rebinds the final commit and runs the corresponding Compose,
watch-witness and watch-process tests through repository-native GitHub checks,
followed by the native source-watch material and cancellation routes. Independent
review follows the current repository `AGENTS.md` selection policy. Static
success is not unit, subprocess, Compose, provider or production qualification.

The sensitive oracles must reject: treating all Compose errors as pending,
accepting absence as deleted content, dropping either service from an identity
vector, resetting the deadline, merging observations across rounds, admitting
replacement as restart, substituting child readiness for supervision, or
clearing ownership from an unknown transfer result. Root owns mutation replay
and final acceptance. Any counterexample reopens this batch's decision.

## Closeout Boundary

The source and sensitive fixture changes are authored. The isolated writer ran
frozen dependency installation and bounded Ruff lint/format and mypy checks on
the six changed Python files. Their static success is not execution of the
authored tests. No production signal owner or stop budget was changed. Root must
replay the final commit, including the distinct supervised and transfer witnesses.

The planned native witnesses are authored, not presumed executed. A semantic
edit ends old-epoch conformance; this batch may finish its authorized edits but
cannot reuse the baseline evidence as proof of the new bytes. No push, PR,
merge, workflow edits, registry edits, production change or descendant delegation
belongs to this lane. Rollback is an owner-authorized revert of the complete
cohesive commit, not deletion of live ownership state.
