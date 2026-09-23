# Coverage Cost Attribution Implementation Plan

Status: active B3 execution plan

The [design](coverage-cost-attribution.md) owns rationale and protected
observations. This plan owns the work order; it does not authorize a speculative
fixture, cache, sharding or product change.

## Work And Acceptance

1. Change only coverage reporting options in `scripts/python_witness.py`.
   Update the full invocation oracle in
   `scripts/tests/test_repository_tooling.py`; retain all selection, policy,
   environment and deadline assertions.
2. Register both new documents under the existing Proofkit requirement and
   link them from the roadmap. Keep prior designs and plans unchanged.
3. Run bounded static lint/type, command-contract, Proofkit and documentation
   checks. Publish through the native GitHub workflow; do not execute local
   behavioral tests or a local database. Native Full Check must prove the
   current source's complete result and coverage policy.
4. Preserve the exact run/job/source and complete duration output. Sum by
   phase and file, report unassigned overhead, output size and precision limits.
   Separate elapsed time, CPU use, runner variation and instrumentation.
5. Apply the design's physical-line equivalence after owner inspection:
   replace the byte-wise loop in `module_ownership_source_signals.py` with
   built-in counts; add bounded exhaustive and explicit binary cases in
   `test_module_ownership_source_signals.py`. Keep all existing scans and their
   fixtures. Compare native before/after durations without weakening the
   original proof or treating an incomplete baseline as a successful run.
   Apply the measured isolated-copy fixture change only to the current-evidence
   cohort and add three nested-alias falsifiers. Name the existing byte-overflow
   cases explicitly; preserve their exact payloads. Compare setup cost, report
   row count, log volume and test outcomes at the next native source epoch.
6. Obtain the independent batch review required by `AGENTS.md` on the frozen
   final candidate. Squash only after exact required checks are green. Keep
   post-merge, release, deployment and production evidence separate.

Diagnostics alone close attribution prerequisites, not B3 performance work.
If a measurement is incomplete or hits an existing resource bound, retain that
failure explicitly; do not manufacture a successful baseline or speedup.
