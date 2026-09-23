# Testing And Proofkit Specification

Status: cross-cutting specification
Last verified: 2026-09-08 (dependency and execution-placement references)

## 1. Decision

Every executable product claim MUST have exactly one owning requirement and at
least one risk-appropriate native witness. Proofkit admits requirement,
binding, and witness-plan structure; the witness command proves the behavior.

```text
AdmittedBehavior(c) :=
  ExistsUniqueRequirement(c)
  and ExistsNativeWitness(c)
  and BindingAdmitted(c)
  and WitnessPassedAtReviewedRevision(c)
```

No term can be removed without admitting an unowned, untested, unrouted, or
stale claim. Proofkit is therefore necessary for governance but insufficient
for runtime correctness.

## 2. Test Taxonomy

| Test class  | Required use                                                   |
|-------------|----------------------------------------------------------------|
| Unit        | Pure predicates, parsers, value objects, and state transitions |
| Property    | Laws over generated inputs and orderings                       |
| Contract    | OpenAPI, signed envelopes, profiles, and provider payloads     |
| Integration | PostgreSQL units of work and adapter composition               |
| Workflow    | Repository-owned CI and bootstrap failover behavior            |
| Shadow      | Candidate omission against the deterministic baseline          |
| Mutation    | Strength of named high-risk witness families                   |
| Container   | Default image startup and bounded health behavior              |

Tests MUST assert specified behavior, not implementation shape. A golden vector
is admissible only when its owner is a current requirement or machine-readable
contract.

## 3. Required Laws

```text
permutation(diff_files) does not alter canonical input hash
permutation(graph_nodes) does not alter canonical graph hash
semantic input change alters semantic hash
unknown or truncated or stale input maps to FullCI
agent advice cannot reduce deterministic coverage
payload mutation invalidates its signature
audit event mutation invalidates replay before filtering
runner assignment selects every requested test exactly once
unsupported interpreter implementation or version fails before runtime startup
```

A law needs both positive examples and a falsifier whenever its negation can be
constructed locally.

## 4. Proofkit Boundary

The [current dependency decision](../../decisions/proofkit-0-14-consumer-admission.md)
owns the exact Proofkit pin. Its active
inputs are:

```text
docs/specs/*/requirements.v1.json
proofkit/requirement-bindings.json
proofkit/routes/*.v2.json
proofkit/repo-profile.json
proofkit/witness-plan-input.json
```

The stable binding path indexes exact owner-route source bytes; its normalized
in-memory projection is submitted to Proofkit and is never tracked. Proofkit
owns final structural admission and selective command routing. It MUST fail
closed when a governed path is unbound, a binding references an absent path or
command, a requirement has no witness, or a proof-like change cannot be routed.
It MUST NOT claim witness freshness, provider execution, credential authority,
safe CI omission, merge admission, rollout approval, or production readiness.

The Python witness router exposes stable repository entrypoints:

```text
python3 -m scripts.python_witness lock-check
python3 -m scripts.python_witness install-check
backend/.venv/bin/python -m scripts.python_witness package-check
backend/.venv/bin/python -m scripts.python_witness lint
backend/.venv/bin/python -m scripts.python_witness typecheck
backend/.venv/bin/python -m scripts.python_witness test
backend/.venv/bin/python -m scripts.python_witness coverage
backend/.venv/bin/python -m scripts.python_witness persistence-test
backend/.venv/bin/python -m scripts.python_witness import-boundary
```

The coverage command executes the complete unit and PostgreSQL integration
suite because its source denominator is the complete backend package. A
non-persistence numerator over a whole-package denominator would measure a
different proposition and is therefore rejected. Coverage.py's combined
aggregate score must remain at or above 84 percent. This coarse regression
floor is distinct from the risk-owned line and branch floors enforced by the
coverage policy, and neither is a claim of semantic adequacy. Requirement
falsifiers, mutation tests, and external admission remain independently
necessary.

`backend/pyproject.toml`, `backend/requirements-dev.lock`, and `backend/uv.lock`
own dependency truth. Installation proof MUST synchronize a clean environment
from locked dependencies; a globally installed package is not evidence.

The import-boundary witness statically rejects undeclared dynamic loading or
source execution across bounded contexts. It is an architecture witness, not a
runtime sandbox.

Repository proof commands use the bounded lifecycle defined by
[`proof-command-execution.md`](proof-command-execution.md). A command receives
only its declared environment allowlist, has a positive finite timeout and a
shared stdout-plus-stderr limit, and runs in an owned process group. Proofkit
network and cache declarations remain route classifications; local subprocess
execution does not claim OS-level network or filesystem isolation.

## 5. Freshness And Scope

Let `B` be an ancestor base and `H` the exact reviewed clean commit.

```text
FreshBranchEvidence(B, H) :=
  B != H
  and Ancestor(B, H)
  and HEAD == H
  and WorktreeClean
  and NonEmptyDiff(B, H)
  and AllSelectedCommandsPassedAt(H)
```

Worktree checks prove the current filesystem only. A branch-head receipt proves
the committed range only when every predicate above remains true before and
after execution. Mutation evidence is bounded to the declared mutant manifest;
it does not imply mutation adequacy outside that set.

## 6. Required Gates And Execution Placement

The commands below identify witness entrypoints, not permission to execute
behavioral checks locally. Local work uses admitted static checks only.
Behavioral, migration, coverage, container, browser and mutation witnesses run
in GitHub under the current verification-execution policy. A listed command
does not override that placement or prove an unexecuted witness.

```text
backend/.venv/bin/python -m scripts.proofkit_dependency
backend/.venv/bin/python -m scripts.proofkit_admission verify
backend/.venv/bin/python -m scripts.proofkit_plan_check
backend/.venv/bin/python -m scripts.proofkit_admission text-policy
backend/.venv/bin/python -m scripts.python_witness package-check
backend/.venv/bin/python -m scripts.quality_plan
backend/.venv/bin/python -m scripts.workflow_lint
backend/.venv/bin/python -m scripts.container_runtime_smoke
git diff --check
```

Detached mutation suites and branch-head validation run after the exact patch
is committed. Provider checks add remote evidence; their absence leaves the
repository in `local proof only` mode and MUST NOT be represented as provider
or deployment readiness.

Before any governed mutation witness executes, one bounded preflight MUST:

- admit every configured suite as a non-empty finite inventory with positive
  integer time bounds;
- admit each mutant with non-empty argv, unique non-empty requirement-owner
  ids, and a non-no-op patch; and
- then prove that each named original byte sequence occurs exactly once in its
  regular, non-symlinked repository target.

Every declared requirement-owner id MUST bind its manifest path to that
suite's exact mutation command. A witness inherits only `PATH`, receives only
the closed manifest-owned additions `PYTHONPATH`, `PYTHONDONTWRITEBYTECODE=1`,
`PROOFKIT_BASE_REF`, and `PROOFKIT_HEAD_REF`, and cannot re-enable Python
bytecode writes.
Each detached suite MUST repeat the same check against its exact `HEAD` image
before linking dependencies or invoking its first witness. This fail-fast
admission preserves mutation outcomes while preventing a stale late-suite
patch from consuming the execution budget of earlier valid mutants. It does
not execute a witness or prove mutation adequacy.
Each manifest outer timeout MUST fit within its owning witness-command timeout.
A provider job that runs governed suites sequentially MUST execute every
registered suite exactly once and prove that the sum of suite outer timeouts
plus an explicit non-suite reserve fits within the job timeout. The current
machine plan owns a 30-minute reserve and a 258-minute provider job envelope;
the workflow timeout is its tested projection. Changing either value requires
synchronized plan, workflow, requirement, and oracle updates.

All detached-suite, branch-head, cleanup, and Git subprocesses MUST delegate to
the repository's finite captured-command executor with a positive finite
deadline. Managed interactive provider sessions remain governed separately.
Signal state is a cancellation input to the finite executor, not an independent
process-group implementation. A stop request, output overflow, timeout,
parent-side lifecycle error, or executable residual descendant suppresses the
command's success receipt; the first terminal cause remains authoritative and
cleanup remains idempotent and cannot turn an execution failure into a pass.
Every first-party module and repository data input used by mutation admission,
including package initializers, is part of the exact-`HEAD` mutation authority
closure.

The final branch-head sequence is:

```text
TargetedNativeWitnesses
  -> WorktreeQuality
  -> CommitReviewedPatch
  -> ExactBaseHeadMutationAndFreshnessGate
```

Detached mutation runners test `HEAD`, not dirty worktree bytes. Therefore a
mutation result produced before commit cannot prove the current worktree. A
branch-head receipt is valid only if the range, clean head, selected manifests,
and worktree identity remain unchanged before and after execution.

Dependency admission has three non-substitutable layers:

```text
Exact manifests and locks
and Current local vulnerability audit
and Provider diff review when available
```

The current [advisory-only policy](../../how-to/toolchain-updates.md#retain-security-alerts-without-automated-pull-requests)
retains vulnerability alerts and pauses version-update PRs for all configured
ecosystems: `uv`, npm, GitHub Actions, Docker, and Go modules. Alert enablement,
security-update settings and auto-triage rules are separate provider facts;
the configuration does not prove vulnerability coverage for every ecosystem.
Repository-owned configuration grants no merge authority. Any manually
requested or subsequently enabled update proposal is discovery evidence only;
the same compatibility, Proofkit-routing,
native-test, and immutable-action gates apply to it as to a human-authored
dependency change. Provider auto-merge settings are external evidence. The
repository-owned release workflow defines exact-source OCI publication,
provenance, and SBOM attestation, while its successful provider execution
remains external evidence. Local workflow admission cannot claim that an image
was published or attested.

If version proposals are re-enabled, the retained weekly schedule and seven-day
cooldown request a provider discovery cadence, not a proposal deadline.
While PR limits are zero they are dormant. In particular, provider queueing, an
exhausted open-pull-request limit, or provider unavailability can defer a new
proposal without a repository event:

```text
WeeklyScheduleConfigured -/-> ProposalCreatedWithinBound
UpdateProposalCreated -> DiscoveryEvidence
UpdateProposalCreated -/-> MergeAuthority
```

Compatibility and safety evidence may reject an update. A future currentness
SLA requires an independently observed scheduled check with a bounded failure
signal; Dependabot configuration alone cannot establish one.

Repository-owned and distributed GitHub workflows pass two non-substitutable
static analyzers: digest-pinned `actionlint` proves GitHub Actions syntax and
expression shape, while locked `zizmor` proves security-oriented workflow and
Dependabot invariants. `zizmor` runs offline in the local gate, so repository
bytes are covered but online provider facts remain unverified. Provider
dependency review and repository rulesets are separate evidence classes.

## 7. Acceptance Questions

1. Which requirement owns each changed behavior?
2. Can every owning binding route to an existing native command?
3. Does each command falsify the claim it purports to prove?
4. Is the evidence bound to the reviewed revision and environment?
5. Are every broader claim and every unavailable provider fact explicit
   non-claims?

Acceptance is valid only when all five answers are evidenced rather than
inferred from a green aggregate.
