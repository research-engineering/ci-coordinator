# Module Ownership And Decomposition

Status: governing cross-cutting architecture contract

Date: 2026-07-22

## 1. Decision Summary

This contract separates three predicates that must not be conflated:

```text
ReviewCandidate(F) != SemanticConcentration(F) != BlockableViolation(F)
```

Quantitative signals select files for review. Repository-owned semantic rules
determine whether responsibilities may share a file. A merge blocker is
admissible only after the complete proof below closes.

The exact machine policy is
[`module-ownership-profile.v1.json`](../../specs/ci-coordinator-core/module-ownership-profile.v1.json).
External analysis kits may collect evidence and challenge a decision, but they
are neither repository policy nor merge authority.

## 2. Context And Observables

The policy protects these observables:

1. one product capability can change without silently changing another;
2. domain decisions remain independent of HTTP, SQL, provider, clock, and
   environment mechanisms;
3. generated, declarative, migration, composition-root, and test files are not
   misclassified by production-source size heuristics;
4. a decomposition preserves public types, runtime validation, failure
   algebra, resource bounds, import direction, and test oracles; and
5. uncertain evidence cannot become a false architecture failure or a false
   pass.

Hard constraints are correctness, security, performance, reliability,
provider compatibility, database semantics, and the public build contract.
Reducing line count is never allowed to weaken one of them.

## 3. Formal Model

For evidence set `E`, file `F`, context `C`, governing profile `P`,
responsibility set `R`, and decomposition `D`:

```text
ReviewCandidate(F, C) :=
  InScope(F, C)
  and (
    DeterministicSignalThresholdReached(F)
    or DeterministicSignalUnknown(F)
    or ChangedAcrossIndependentOwners(F)
    or ReviewerSelected(F)
  )

SemanticConcentration(E, F, R, C) :=
  |R| >= 2
  and forall r in R: PrimaryAuthority(E, F, r, C)
  and PairwiseIndependent(E, R, C)

PolicyExcess(P, E, F, R, C) :=
  SemanticConcentration(E, F, R, C)
  and ApplicableForbiddenCoownership(P, F, R, C)

ClosedEvidence(E, P, F, R, C) :=
  ResponsibilitiesProven(E, F, R, C)
  and ResponsibilitySetIsStableAcrossProofPhases(E, R, C)
  and GoverningProfileAndBaseHeadEpochMatch(E, P, C)
  and EvidenceIsTypedCurrentAndConclusive(E, F, C)

Safe(D, F, C) :=
  PreservesObservableBehavior(D, F, C)
  and PreservesHardConstraints(D, F, C)
  and PreservesDependencyDirection(D, F, C)
  and HasRegressionWitnesses(D, F, C)

Block(P, E, F, R, D, C) :=
  PolicyExcess(P, E, F, R, C)
  and ClosedEvidence(E, P, F, R, C)
  and not ApplicableRequiredColocationDefeats(P, D, R, C)
  and MovesEveryAuthorityToAtLeastTwoTargets(E, D, R, C)
  and Safe(D, F, C)
  and StrictlyPreferred(E, D, F, C)
  and NewOrWorsened(E, F, C)
  and not ActiveWaiver(P, F, C)
```

The repository gate owns only the deterministic projection:

```text
CandidateInventory(P, W) :=
  ClassifyEveryObservedPath(P, W)
  and EvaluateEveryApplicableDeterministicSignal(P, W)
  and SelectEveryThresholdMatchOrUnknown(P, W)
  and SeparateProductionAndTestCohorts(P, W)
  and CandidateCap(P) = none

CandidateInventoryComplete(P, W) :=
  InventoryScope = git-worktree
  and SignalRuntimeId in P.AdmittedSignalRuntimeIds
  and ObservedPathCount = DispositionCount
  and CandidateTruncatedCount = 0
  and InventoryDigestMatches(P, W)
```

`CandidateInventoryComplete` is relative to the exact profile, worktree
snapshot discovered by Git, path universe, file-kind rules, deterministic
signal grammar, and exact parser runtime. A caller-supplied fixture snapshot is
always labelled incomplete and cannot issue an authoritative completeness
receipt. Completeness does not claim that coupling, complexity, churn, semantic
responsibility, or every possible reviewer signal was mechanically decided.
The authoritative Git path universe uses the exact resolved repository
toplevel and clears ambient `GIT_*` repository selectors. Completeness is
relative to Git-observed tracked and non-ignored untracked paths; it does not
claim a physical inventory of ignored local caches, environments, or
developer-private files.

`StrictlyPreferred` means the decomposition is no worse on every declared hard
constraint and strictly better on at least one admitted maintenance objective,
using recomputable admitted cost relations. If any term is unknown or
conflicting, `Block` is not proved and the verdict is `ABSTAIN`, not `FAIL`.

### Why one invariant family is not an equivalence

`Valid(F) <-> OneInvariantFamily(F)` is false in both directions.

- It is not necessary: a composition root may wire several contexts without
  owning their policy; a generated projection may represent several contracts;
  an atomic migration may change several database objects.
- It is not sufficient: a broad label such as "workflow orchestration" can
  conceal HTTP, provider, persistence, and policy authorities in one file.

The valid implications are narrower:

```text
Block(P, E, F, R, D, C) -> PolicyExcess(P, E, F, R, C)
LargeOrComplex(F) -> DecompositionReviewRequired(F)
LargeOrComplex(F) -/-> PolicyExcess(P, E, F, R, C)
```

## 4. File-Kind Applicability

| Kind                                          | Review rule                                                                                       | Maximum metric-only verdict |
|-----------------------------------------------|---------------------------------------------------------------------------------------------------|-----------------------------|
| Production authority                          | Full semantic ownership review                                                                    | `REVIEW_REQUIRED`           |
| Composition root, router, registry            | Co-location is admissible only for wiring without domain policy or durable state                  | `REVIEW_REQUIRED`           |
| Generated projection                          | Excluded only when source, generator, and reproducibility are proved                              | `ABSTAIN`                   |
| Test                                          | Separate maintainability and oracle review; never counted in the production percentile population | `REVIEW_REQUIRED`           |
| Published migration                           | Review atomicity and forward evolution; do not split merely for size                              | `REVIEW_REQUIRED`           |
| Declarative schema, fixture, or specification | Size is not semantic concentration                                                                | `REVIEW_REQUIRED`           |
| Enforcement, security, release, or CI script  | Treat as production-like                                                                          | `REVIEW_REQUIRED`           |
| Vendor, build, or cache output                | Exclude only with proved provenance                                                               | `ABSTAIN`                   |
| Documentation                                 | Govern through the documentation authority graph                                                  | `ABSTAIN`                   |
| Other observed path                           | Classify and record; no source metric applies by default                                          | `ABSTAIN`                   |

The profile defines explicit comparison operators for its line, recognized
public-declaration, and first-party import-context signals. The current
boundaries are `lines > 400`, `exports > 12`, and `contexts >= 3`. These are
triage signals, not universal quality limits. A source-analysis failure selects
the file for review instead of silently removing it from the queue.

The closed source grammar is versioned as
`ci-coordinator.module-ownership-signals.v2` and is intentionally narrow:

- Python parsing is pinned to the CPython 3.13 grammar and the exact admitted
  CPython 3.13.15 parser runtime. Recognized public declarations are
  a single static unique-string module-scope `__all__`, or, when module-scope
  `__all__` is absent, top-level public function, class, and simple assignment
  declarations; imports are not counted unless named by `__all__`;
- ECMAScript public declarations are line-leading `export` lexical markers
  after comments have been masked and line terminators normalized. The marker
  may span a line break after the `export` keyword; and
- CommonJS `.cjs` resources use the pinned Tree-sitter JavaScript grammar.
  Only static plain-string `require` calls and static `module.exports` or
  `exports.name` assignments are admitted; computed, spread, dynamic, or
  mutating export forms produce `unknown`; and
- first-party contexts are recognized Python package imports and same-line
  static relative ECMAScript import, re-export, or CommonJS require specifiers
  resolved to the repository's admitted package roots.

An invalid or recursively unparseable Python module, non-UTF-8 source,
non-static or multiply bound module-scope Python `__all__`, an unadmitted
Python parser runtime, or an ambiguous ECMAScript slash, template literal,
continued string, or unterminated lexical construct fails closed. Source-level
analysis failure produces `unknown` and therefore selects review; an
unadmitted parser runtime rejects the inventory because its completeness epoch
cannot be named. Physical lines use LF, CRLF, and CR as equivalent line
terminators. Completeness is relative to this versioned lexical grammar and
runtime, not to a full TypeScript semantic model or every construct accepted by
a language implementation. A semantic parser can replace the lexical grammar
only under a new grammar id and equivalent boundary falsifiers.

File kinds are assigned by one ordered first-match profile. Test, migration,
generated, composition-root, documentation, vendor/build/cache,
production-like script, declarative, and production-authority rules are applied
in that order before the explicit `other` default. The order is part of the
machine contract; in particular, declarative resources nested under production
source roots remain declarative. Every observed path receives exactly one
disposition, including paths outside the source cohorts.

Production and test units use separate candidate cohorts. The default queue is
unbounded; an explicit bounded review budget must report queue completeness and
truncation, and every cap-deferred unit remains `ABSTAIN`.

## 5. Repository-Owned Boundaries

The backend dependency and trust boundaries remain owned by
[`02-context-map.md`](../02-context-map.md) and enforced by the Python import
boundary gate. Domain planning may not co-own HTTP admission, SQL persistence,
GitHub transport, environment access, or wall-clock reads.

The pure `target_authority_relation` capability is independently classified by
the machine profile. It may own finite row, baseline-transition, total-domain,
and exact-comparison policy, but may not co-own GitHub transport, HTTP
admission, runtime settings, process environment, SQL persistence, or clock
acquisition. Its import rule admits only its own package, the exact canonical
JSON, strict JSON, hashing, and ordering primitives from `kernel`, and the
minimal repository-scope value from `config_control.contracts`; both broad
facades are forbidden because they eagerly load unrelated mechanisms.

The pure `workflow_authority` capability owns Git-object reconstruction,
stable workflow-manifest identity, and exact source-binding evidence. The
GitHub adapter may depend inward on this package; provider transport,
persistence, HTTP, runtime, environment, filesystem, process, and clock
mechanisms are forbidden inside it.

The pure `target_authority_producers` capability owns owner registration and
independently enumerated observation projections over named capability values.
It may import those public owner contracts, but it cannot co-own their provider,
SQL, HTTP, runtime, environment, process, or clock mechanisms. Observation
key-space derivation cannot import or accept baseline, transition,
expected-relation, or registration keys.

`target_authority_evidence` has two mechanically distinct responsibilities.
Its model, codec, and replay files aggregate exact public owner codecs and
re-run the transition, producer, source-binding, and relation predicates; they
cannot own filesystem or runtime mechanisms. Its file and CLI adapters may own
only bounded no-follow input and atomic offline publication. The machine
profile forbids either responsibility from co-owning provider, HTTP,
persistence, runtime-setting, environment, or clock authority and forbids the
two responsibilities from sharing one implementation file.

The frontend API boundary is capability-oriented:

```text
frontend/src/api/
  configActivation/    attested repository configuration activation
  controlPlaneIdentity/ Keycloak administrator session and logout
  development/         local proxy admission
  governanceBaseline/  approved expected-governance baseline
  governanceComparison/ exact baseline-to-observation comparison
  governanceObservation/ effective default-branch governance observation
  providerInventory/   installation and repository inventory
  repositoryAttestation/ proposal-bound GitHub reviewer step-up
  shared/              bounded transport mechanics only
  workbench/           scoped workbench snapshots and their request bounds
  workflowDiscovery/   exact-revision discovery and proposal proof
  generated.ts         generated OpenAPI projection
```

Every handwritten TypeScript file under this root must match exactly one
profile-owned capability. A new catch-all `client.ts`, `schema.ts`, `utils.ts`,
or equivalent unowned landing zone fails the deterministic ownership gate.
Capability-neutral shared modules may not become primary authorities for a
product capability; an adapter may consume a capability-owned contract without
acquiring ownership of that contract.

The gate rejects symlinked ownership-root components, special files, unowned
`.ts`, `.tsx`, `.mts`, and `.cts` files, and traversal beyond profile-owned
entry, depth, file-count, per-file-byte, aggregate-content-byte, per-path-byte,
aggregate-path-byte, Git-output-byte, and Git-process-time budgets. Leaf reads
use non-blocking, no-follow admission and compare the path and descriptor
identity before and after reading. This proves a bounded static repository
projection, not an atomic snapshot isolated from a process that mutates the
worktree concurrently.

Enforcement ownership is deliberately narrow:

- `module_ownership_profile.py` admits the exact machine profile;
- `module_ownership_candidates.py` classifies the bounded repository snapshot,
  evaluates deterministic signals, and derives the complete candidate ledger;
- `module_ownership_source_signals.py` routes bounded source bytes and owns the
  language-neutral physical-line projection;
- `module_ownership_python_signals.py` owns the CPython-pinned AST grammar and
  module-scope export analysis;
- `module_ownership_ecmascript_signals.py` owns the conservative ECMAScript
  lexical grammar and pinned CommonJS AST grammar;
- `module_ownership_policy.py` composes exclusive path ownership and candidate
  evidence into one report; and
- `repository_paths.py` owns the segment-aware repository glob and real-path
  primitives shared with selective routing.

The report enumerates which forbidden-coownership rules were declared and
which deterministic projections were executed. A declared rule count is never
presented as proof that semantic co-ownership was mechanically evaluated.

Within workflow discovery, wire-shape admission, policy proof, and manifest
identity are separate reasons to change. They may collaborate but may not be
collapsed into one authority file merely because they share one response DTO.

## 6. Evidence And Verdicts

Candidate evidence identifies the exact file bytes, profile bytes, file kind,
cohort, deterministic measurements, selection reasons, and complete inventory
digest. Semantic evidence must additionally identify one responsibility set
reused across every proof phase, applicable forbidden and required-colocation
rules, base/head epoch, complete authority move, proposed decomposition,
preserved constraints, recomputable costs, and smallest regression falsifier.
Metrics, names, imports, churn, and AST facts are candidate evidence only; they
cannot prove semantic responsibility by themselves.

The default CLI report is a compact receipt containing counts plus inventory
and candidate-evidence digests. `--full-candidate-ledger` reveals the
per-candidate rows when a reviewer needs them. Both modes derive from the same
in-memory ledger; progressive disclosure changes serialization volume, not
selection semantics.

Verdict ceiling:

```text
heuristic only                 -> REVIEW_REQUIRED
unknown or conflicting proof   -> ABSTAIN
closed pre-existing excess     -> WARN
closed new-or-worsened excess  -> FAIL, only when policy permits
```

Waivers are exceptional, owned, reasoned, scoped to exact rules and files, and
time-bounded. Expired, ambiguous, or broadened waivers are inactive.

## 7. Alternatives

| Alternative                                             | Rejection reason                                                                                            |
|---------------------------------------------------------|-------------------------------------------------------------------------------------------------------------|
| Block on a line-count threshold                         | Produces false positives for cohesive parsers, schemas, generated files, migrations, and composition roots. |
| Trust an external reviewer profile as repository policy | Transfers architecture authority outside repository governance and makes results non-reproducible.          |
| Require one class or file per use case                  | Adds unearned abstractions and does not prove semantic independence.                                        |
| Keep only prose guidance                                | A mechanical ownership rule without enforcement is a wish.                                                  |
| Build a fully automatic semantic classifier             | Current evidence cannot make product ownership mechanically decidable without false assurance.              |

The selected hybrid is minimally sufficient: deterministic gates enforce
mechanically decidable ownership and profile integrity; evidence-based review
decides semantic concentration.

## 8. Implementation Plan

1. Keep hard boundary validity separate from review selection.
2. Admit explicit signal operators and ordered file-kind classification.
3. Emit a content-bound, uncapped candidate ledger with separate production
   and test cohorts and a completeness receipt.
4. Keep semantic ownership, safe decomposition, and blocking authority outside
   the deterministic collector.
5. Bind the profile, collector, report, and falsifiers to Proofkit and the
   portable quality plan.
6. Re-run frontend, Python, documentation, Proofkit, and branch-head gates; a
   later change must revise this decision if a weaker rule provides equal
   protection with lower maintenance cost.

## 9. Non-Claims And Revision Conditions

This contract does not prove that every reviewed file is cohesive, that every
large file should be split, that deterministic signal coverage exhausts all
useful reviewer signals, that an external kit is authentic, or that semantic
ownership can be fully automated.

It also does not claim a full TypeScript AST or type-semantic analysis,
non-UTF-8 Git-path support, Git configuration identity, or atomicity across the
multi-file worktree scan.

Revise the policy when measured false-positive cost exceeds review value, a
new file kind lacks correct applicability, a forbidden combination becomes
valid under an explicit context, or a cheaper enforceable boundary preserves
all observables and hard constraints.
