# Pending Semantic Review Evidence

Status: derived review contract; native qualification required before merge

Owner: `REQ-CI-PROOFKIT-006`, exact selective proof routing

## Problem And Meaning

A structurally valid proof route and a passing native test do not establish that
the changed assertion distinguishes the intended behavior from its counterexample.
Reviewers need the exact declaration delta and changed source/test context without
creating another binding registry or changing execution selection.

The existing route authority contains requirement, scenario, witness, kind, path,
command and environment tuples. Paths include production code, documentation,
configuration, tests and fixtures. It does not declare a separate directed
production-path-to-test-path relation. Joining paths through a shared requirement
therefore produces candidate associations, never proved causal edges.

## Derived Contract

The existing selective-plan check retains its structural verdict and emits a
pending review projection after admitting the actual planner input and result.

- Bind the exact base/head, committed/index/worktree diff identities, bounded
  untracked input identities, classification profile and consumed policy bytes.
  Compare the observed subject before and after admission.
- Preserve exact base/current declaration tuples for touched requirements and
  changed declarations. Bind the full authority by relation and projection
  digests without repeating every unrelated row.
- Include requirements affected by removed bindings and command/environment
  changes. Equal path sets do not make swapped declaration edges equivalent.
- Separate changed test-cohort paths from other changed paths using the existing
  module-ownership profile. A test-cohort path may be a helper or fixture; neither
  its classification nor its change proves a changed assertion.
- Mark explicit selection-only paths separately from observed Git changes.
  The projection neither adds nor removes executable commands.
- Keep semantic review `pending`, including when no associated test path changed.
  This is an honest review obligation, not an automatic failure or approval.

The reviewer must identify the asserted behavior, the discriminating negative
case and any unchanged witness that already covers the change. This projection
does not decide that question and imposes no reviewer count, approval field,
CODEOWNERS rule or GitHub policy change.

## Ownership

Agentic Proofkit supplies generic structural admission, immutable identity and
route/impact primitives. Those mechanisms already exist in the admitted release;
no missing reusable primitive has been established. Its generic compact binding
model and this repository's compact v2 source files are different contracts and
must not be equated by version number.

The repository owns extraction from its Git subject, requirement meaning,
classification policy, execution commands and semantic acceptance. The new view
is a derived product of those existing owners. Its artifact is not another route
authority, test selector, receipt registry or execution result.

## Publication And Failure

Write the complete report to `.ci-evidence/proofkit-plan-check.json` before the
bounded console summary. Publish atomically through an admitted repository
directory. Bind the summary to the artifact's exact byte digest. A 16 MiB limit
rejects oversized reports instead of truncating semantic rows. Structural,
subject-drift or publication failure returns failure; a pending semantic review
alone does not alter the existing structural success predicate.

GitHub retains the artifact from the actual selective-plan step, including its
failure path. Full manual runs that do not execute that step do not invent a
review artifact. Artifact retention is diagnostics and review support, not a
successful-test or merge-authority claim.

## Alternatives, Proof And Revision

The cheapest sufficient change is a projection inside the admitted planner
wrapper. A second coverage map would duplicate declarations; a Cartesian
source/test join would invent causality; a new generic Proofkit command would
add a public contract without a demonstrated gap. A shared library extension is
reconsidered only for a neutral missing predicate supported by real consumers.

Native negative witnesses cover same-path-set edge swaps, deleted or changed
bindings, command/environment changes, stale Git/policy observations, non-test
proof paths, selection-only paths, no changed test, report ordering, overflow
and inadmissible publication paths. Existing routing and retirement witnesses
must retain their results. Git observations remain bounded before/after reads,
not an atomic filesystem snapshot or provider-authenticated evidence.

Implementation order is projection and negative fixtures, exact route bindings,
artifact upload, generated metadata refresh, independent source review under
`AGENTS.md`, then exact-commit GitHub qualification. No local behavioral execution
substitutes for that gate. Retire the local projection if an admitted generic
capability preserves every declared relation and native boundary at lower cost.
