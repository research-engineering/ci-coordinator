# Automatic Mutation Discovery

## Decision

Add bounded automated fault discovery beside the existing curated mutation
witnesses. This is developer tooling, not runtime functionality or a new
production dependency. Requirement: `REQ-CI-PROOFKIT-010`.

The curated manifest can only test explicitly anticipated faults. An automatic
generator explores additional syntactic mutations. Neither mechanism proves
that the requirements or their combined fault universe are complete.

```text
PassingBaseline(P, T)
and ViolatesRequirement(m(P), R)
and PassingMutant(m(P), T)
=> T does not detect this violation of R
```

A survivor is a review candidate, not automatically a product defect. An
equivalent mutant requires an owner-grounded explanation rather than a test
that merely matches the current implementation.

## Scope And Ownership

| Owner                       | Initial scope                   | Native mechanism                                               |
|-----------------------------|---------------------------------|----------------------------------------------------------------|
| Python configuration        | `kernel/admission.py`           | mutmut 3.8.0 with pytest                                       |
| Frontend configuration      | `api/shared/operationId.ts`     | StrykerJS 10.0.0 with Vitest                                   |
| Execution and observation   | `scripts/automatic_mutation.py` | Existing bounded process executor; Pydantic report projections |
| Mandatory domain falsifiers | Existing eight mutation suites  | Unchanged curated manifests and runner                         |

Versions are development dependencies in the project manifests and lock files.
The frontend resolves its plugin through the installed package's public entry
point instead of depending on a hoisted node_modules layout.
The pipeline uses a disposable GitHub checkout per profile, at most two mutation
workers, finite command deadlines and captured-output bounds. The subprocess
environment excludes provider credentials. This is not an OS network sandbox.
Mutation outputs are ignored, never committed, and retained as CI artifacts for
seven days. Existing output prevents a new observation instead of being deleted
or silently reused. No incremental result cache is restored.

The Python native report is an exact nine-counter projection. Unknown, empty,
inconsistent, interrupted, suspicious or crashed results are rejected. The
Stryker version-1.0 projection requires the configured file, unique mutant IDs,
known statuses and no pending or runtime-error results. Both profiles require
at least one normally resolved mutant. Missing reports and failed commands
cannot become observations.

These projections are intentionally not a second complete upstream schema.
Additional Stryker source, location and presentation metadata remains in its
native artifact. It does not establish a repository-owned proof receipt.

## Result Meaning

The result is `observed`, never a claim that all tests are adequate. Survived,
uncovered, ignored, compile-error and timed-out mutants remain separate native
categories. Commands use the `discovery.mutation-*` namespace, keeping the
curated `mutation.*` registry complete without conflating their evidence.
In particular, mutmut may classify pytest internal errors as killed;
its aggregate is labelled `upstreamCounts`, not certified behavioral kills.
The instrumented Python target, span index and per-file metadata are preserved
for inspecting mutations without repeating their execution. The existing curated
runner continues to own stricter baseline and test-outcome evidence.

There is no new mutation-score merge threshold. A successful discovery job
means a non-empty bounded diagnostic campaign completed and its required report
projection was admitted. Existing Full Check and mutation acceptance are not
weakened. TypeScript type checking remains separate: the Stryker TypeScript-7
checker is experimental and is not enabled.

## Implementation And Acceptance

1. Pin the two engines and the Vitest adapter; regenerate both ecosystem locks
   and the Python requirements export without upgrading unrelated dependencies.
2. Configure one small, risk-relevant scope in each language and retain the
   normal test runners. Add direct Unicode/byte-boundary operation-ID witnesses.
3. Reuse bounded process execution; reject stale or inadmissible observations.
4. Register native report falsifiers and workflow policy tests with Proofkit.
   Refresh the mirrored non-runtime workflow inventory and keep parameterized
   fixture serialization deterministic across collection and execution processes.
5. Run static gates locally and behavioral tests and both real engines in
   GitHub on the proposed revision. A declared command is not execution evidence.
6. Review survivors against requirements before expanding scope or proposing a
   score threshold; compare useful findings, wall time, CPU cost and repeatability.

## Usage And Revision Triggers

Install through the existing locked development setup. In an authorized
disposable GitHub checkout, invoke one profile from the repository root:

```bash
backend/.venv/bin/python -m scripts.automatic_mutation python
backend/.venv/bin/python -m scripts.automatic_mutation frontend
```

Ordinary developer check commands do not silently execute these campaigns.
The explicit complete branch-head plan includes both discovery commands;
their accepted outcome remains observation, not mutation-score qualification.
Its outer envelope includes the additional 760,000 ms of declared child budgets
and preserves the existing orchestration reserve; no child deadline is relaxed.
Behavioral execution follows [the testing contract](../architecture/cross-cutting/testing-and-proofkit.md).

Reconsider the initial scopes after measuring useful findings per CPU minute.
Revalidate report projections and process behavior on engine upgrades. Before
enabling incremental reuse, admit the complete dependency/configuration/input
invalidation contract. Before replacing any curated mutation, prove that its
owner-specific fault and native oracle survive the replacement.

Keeping only curated tests is cheaper but cannot discover unlisted syntactic
faults. Replacing them wholesale loses explicit SQL, privilege and transaction
fault obligations. A new distributed mutation scheduler has no demonstrated
benefit for these bounded scopes, so native engines and the existing process
owner are the minimum sufficient initial integration.

## Connected Qualification Prerequisite

The required connected-stack run exposed a provisioner connection failure while
PostgreSQL was already labelled healthy. The existing socket healthcheck admits
the image's temporary initialization server before TCP is available; the separate
provisioner requires TCP. This is a pre-existing readiness race, not mutation
execution, and the available job log does not prove it was the unique cause.

The local correction checks TCP without changing the image, principals, secrets,
volumes or deadlines. A pinned-image witness distinguishes a ready socket-only
server from a ready TCP server using the actual Compose probe. Both connected
stack profiles must pass on the repaired revision; blind retries or a longer
startup budget would not repair the missing readiness predicate.

The pinned Testcontainers log strategy accepts `times` but its inspected matcher
returns on the first occurrence. The native witness therefore supplies one
message with `predicate_streams_and=True`: initialization through
[`pg_ctl`](https://www.postgresql.org/docs/18/app-pg-ctl.html) writes it to stdout,
whereas the directly executed final server writes it to stderr. Requiring both
streams distinguishes final startup in this fresh-cluster fixture without
increasing its deadline or treating one historical log match as live readiness.

## Initial Survivor Triage

The initial native campaign produced 31 Python survivors and two frontend
survivors. These observations guide tests; their counts are not acceptance limits.

| Candidate group                                                          | Owner-bound disposition                                                                                                                                                                                                               |
|--------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Python limit/type, zero/maximum weight and complete-capacity restoration | Ten candidates expose missing boundary or lifecycle assertions. Add public-behavior witnesses without changing the primitives.                                                                                                        |
| Python exception-message changes                                         | Sixteen candidates alter text without a proved required distinction in this scope. Do not assert arbitrary strings solely to improve the score.                                                                                       |
| Python private active-counter/guard changes                              | Five candidates have no demonstrated distinction through supported lease histories. This is not universal equivalence or permission to remove defensive state. Reopen if additional observers or callers become part of the contract. |
| Frontend loop includes `value.length`                                    | The extra `charCodeAt` produces NaN and neither surrogate branch matches for a supported primitive string; retain as behavior-equivalent in that input scope.                                                                         |
| Frontend omits the low-surrogate upper bound                             | Reject a high surrogate followed by U+E000 or U+FFFF. The existing implementation is correct; these witnesses prevent a non-equivalent mutation from surviving.                                                                       |
| Frontend decrementing loop mutations                                     | Keep their observed Timeout category distinct from a certified behavioral kill.                                                                                                                                                       |

Native artifacts retain exact mutant identities and replacements. Expansion
requires a new scope admission; this triage does not claim that all future
mutations, exception observers, private callers or performance effects are covered.
