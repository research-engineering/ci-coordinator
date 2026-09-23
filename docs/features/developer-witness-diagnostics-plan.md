# Developer Witness Diagnostics Implementation Plan

Owner: developer environment. [Design](developer-witness-diagnostics.md).

1. Preserve the existing parent supervisor; implement one HMR work deadline and
   one cleanup deadline in the existing witness owner. Keep first failed phase,
   strict completion markers and monotonic phase durations.
2. Identify debug config/start/port/inspect/restore failures using closed fields.
   Preserve provider lease, ownership checks and existing command budgets. Retain
   primary and restoration errors without leaking provider output or masking cancellation.
3. Add native-only controls at the actual Node helper and Python debug/CI boundary;
   independently falsify each changed predicate. Keep real rendered checks intact.
   Track actual timer creation/release through native timer wrappers, assert no
   pending handles after phase exit, and reject a missing-clear mutation by that
   assertion rather than a process timeout. Keep these assertions outside actions.
4. Extend DEV requirements and route the new design/plan and tests through existing
   Proofkit bindings. Check lint, types, import boundaries and documentation locally.
5. Use the independent batch-review policy in AGENTS.md. Publish one qualified
   candidate and run native Full Check. Keep previous failed runs in the evidence
   ledger; a success is not a root-cause finding.
6. Squash only after exact-head qualification. Require postmerge qualification
   before the next release; then deploy and measure archive stages. If another
   failure occurs, use its exact operation and budget evidence for a bounded repair.

Acceptance excludes an unmeasured Vite fix, arbitrary retry, total-budget increase,
local behavior execution, unrelated decomposition, or changed application policy.
