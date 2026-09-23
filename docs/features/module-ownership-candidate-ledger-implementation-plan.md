# Module Ownership Candidate Ledger Implementation Plan

Status: implemented and locally verified

Date: 2026-07-28

## 1. Objective

Make the repository's decomposition-review selection contract executable
without turning heuristics into architecture verdicts.

```text
Exact deterministic signal
  -> complete candidate ledger
  -> semantic review
  -> optional policy-relative finding
```

The implementation closes two current defects:

1. threshold fields do not encode whether their boundary is inclusive; and
2. the ownership command admits candidate-queue declarations but emits only
   exclusive frontend path ownership.

## 2. Preconditions

- The repository is pre-release and has no published consumer of the current
  profile shape, so the initial v1 profile may be corrected in place.
- Existing frontend exclusive ownership and Python import-boundary behavior
  remain unchanged.
- The external GodFile Guard archive is review methodology, not a runtime or CI
  dependency.
- Candidate metrics may select review only; they cannot emit `warn` or `fail`.

## 3. Implementation Slices

### Slice A: exact profile semantics

- replace ambiguous minimum fields with metric rows containing an exact
  `operator` and `threshold`;
- add ordered first-match file-kind rules and one explicit default kind;
- bind source analysis to a versioned grammar id;
- bind parser-dependent signals to the exact admitted CPython runtime ids;
- retain an uncapped queue and separate production/test cohorts;
- reject unknown fields, operators, kinds, duplicate ids, invalid thresholds,
  and unsafe path patterns.

### Slice B: deterministic inventory

- self-discover tracked and non-ignored untracked repository paths through
  bounded Git output, while rejecting ambient Git repository-selection
  redirects and symlink traversal;
- bind each regular file to its content digest and exact file kind;
- enforce depth, Git process output/time, per-file, and aggregate content
  budgets;
- measure physical lines, recognized public declarations, and recognized
  first-party import contexts using a closed grammar;
- select every threshold match and every applicable analysis failure;
- emit sorted candidate rows, per-cohort counts, zero truncation, profile
  identity, parser-runtime identity, and a digest over every path disposition;
- keep caller-supplied fixture evaluation explicitly incomplete so it cannot
  forge an authoritative worktree receipt.

### Slice C: proof integration

- compose the inventory with existing exclusive ownership output;
- keep the default witness receipt compact while exposing full candidate rows
  through an explicit CLI flag;
- add boundary, malformed-source, symlink, cohort-separation, completeness, and
  deterministic-round-trip falsifiers;
- bind the design, plan, profile, collector, report, and tests to
  `REQ-CI-CORE-018`;
- run ownership, import-boundary, Proofkit, documentation, Python, frontend,
  and portable gates.

## 4. Acceptance Predicate

```text
Accept iff
  operators are explicit
  and every observed path has exactly one file-kind disposition
  and the source grammar has an exact versioned identity
  and the parser runtime has an exact profile-admitted identity
  and only a self-discovered Git worktree can claim completeness
  and every applicable signal is measured or marked unknown
  and every threshold match or unknown is selected
  and production and test candidates are separate
  and candidate truncation = 0
  and candidate evidence is content-bound
  and metric-only verdict ceiling = review-required
  and existing ownership and import boundaries still pass
  and Proofkit reports no unknown changed-path edge
```

## 5. Non-Claims

- The ledger does not prove semantic concentration or internal cohesion.
- It does not propose or prove a safe decomposition.
- Completeness is relative to the admitted deterministic signal grammar, exact
  parser runtime, and self-discovered Git worktree; it does not claim complete
  coupling, complexity, churn, or history analysis.
- It does not authenticate external review kits or grant merge authority.
