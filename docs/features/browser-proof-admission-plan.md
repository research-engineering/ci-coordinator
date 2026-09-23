# Browser Proof Admission Implementation

Status: active implementation plan

Design: [blocking browser proof admission](browser-proof-admission.md).
Independent review follows the [repository policy](../../AGENTS.md).

1. Enable the two existing library guards without changing interactive mode,
   browser projects, production assets or existing test selection arguments.
2. Replace the workflow's second build with existing exact bundle verification
   followed by the same Playwright suite. Preserve ordered prerequisites.
3. Add native controls invoking the real pinned CLIs. The valid unfocused
   corpus executes both bodies; focused blocking execution rejects with the
   exact focused-item diagnostic and no body marker; removing only the guard
   in the scratch config admits exactly the focused body. Biome rejects the
   focused file and accepts its unfocused counterpart, without a rule-selection
   override. Copies changing only severity to warning or off pass; the warning
   keeps the focused-test diagnostic and off removes it. Do not use stdin or
   automatic fixes as the rejection oracle.
4. Bind a workflow-order witness to the actual build, mutation, manifest and
   browser commands. Existing bundle tests independently reject asset drift.
   Refresh only the workflow fingerprint in both entrypoint-disposition
   mirrors after enumerating every declared workflow file and preserving exact
   membership, disposition and caller bindings. The native exhaustive-profile
   witness validates the complete projection, not merely the changed file.
5. Clarify UI006 and refresh Proofkit routes without dropping prior bindings.
   Run local static/type/contract checks, one frozen independent review and
   exact-head native Full Check; squash only after qualification.
   Use the repository-owned lint wrapper with its backend Ruff configuration
   for scripts as well as backend source.

The native CLI controls resolve packages from the owned locked worktree. They
use bounded output, timeout, no shell, an explicit non-secret environment and
guaranteed scratch cleanup. A loader error, empty collected population or
unrelated lint error must not satisfy a negative control. No local test,
browser, container or server execution is admitted by this plan.

Closeout records the actual browser job time and removed build step separately
from correctness. This does not close complete browser fault accounting,
owner-required inventory, all ADR-0031 candidates or deployment qualification.
