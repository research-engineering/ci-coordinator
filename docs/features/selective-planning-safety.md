# Selective Planning Safety

Status: design; qualification state is owned by ROADMAP

Date: 2026-09-25

Owner requirements: `REQ-CI-CORE-004`, `REQ-CI-CORE-005`

## 1. Decision

A reduced deterministic plan is admitted by a proof checker that is
implementation-independent from the planner orchestration. The checker
validates current context identity, complete catalog classification, exact
selected coverage, witness closure, and every omission predicate. It does not
call the planner, impact analyzer, omission-proof builder, or planner witness
closure.

Runner-capacity projection consumes the repository's full test inventory but
materializes only tests owned by the already selected witness set. It rejects
unknown witnesses, duplicate test identities, and missing selected coverage.

The validation catalog admits only an exact, transitively immutable value
graph, computes its content hash once after closing all references, and returns
that identity in constant time. Exact tuples, exact catalog member models,
exact nested sharding policy, and immutable primitive fields are preconditions
for caching the hash.

## 2. Confirmed Failure Modes

The external engineering review identified three related counterexamples:

1. The verifier replayed `plan(input, policy)` and accepted an omission when
   the same replay emitted it. A defect shared by the planner and replay could
   therefore satisfy both checks.
2. Capacity construction passed every repository test into a manifest whose
   constructor required the test witness set to equal the selected witness
   set. A proper full repository manifest made selective capacity projection
   unavailable.
3. `ValidationCatalog.catalog_hash` serialized and hashed the complete catalog
   on every read. Planner and verifier loops amplified this work.

These findings are valid because each has a current executable path and a
feasible counterexample. They form one capability objective:

```text
SelectiveSafety :=
  IndependentAdmission
  and SelectedExecutionReachable
  and VerificationCostBounded
```

Fixing only one conjunct leaves the selective path either unsound,
unreachable, or operationally unusable.

## 3. Admission Algebra

For current context `C`, policy `P`, catalog obligations `O`, selected
obligations `S`, and omitted obligations `M`:

```text
CoordinatesCurrent(C, P, plan) :=
  plan.configEpochId = C.policy.epochId
  and plan.repoEpochHash = Hash(C.repoEpoch)
  and plan.inputHash = RecomputeInputHash(C)
  and plan.diffHash = C.diff.diffHash
  and plan.compiledPolicyHash = P.compiledPolicyHash
  and plan.policyHash = P.policyHash
  and plan.dependencyGraphHash = C.graph.graphHash
  and plan.catalogHash = P.catalog.catalogHash

CompletePartition(O, S, M) :=
  Ids(S) intersect Ids(M) = empty
  and Ids(S) union Ids(M) = Ids(O)

SelectedExact(P, S) :=
  forall s in S:
    s.requiredWitnessIds = P.catalog[s.id].requiredWitnessIds
    and s.depth = P.catalog[s.id].defaultDepth

OmissionValid(C, P, m) :=
  CurrentProofCoordinates(C, P, m.proof)
  and m.requiredWitnessIds = P.catalog[m.id].requiredWitnessIds
  and P.catalog[m.id].omitAllowed
  and C has no FullCI-invalidating fact
  and no changed path is unknown
  and no global-risk path changed
  and ImpactedPaths(C) does not intersect ResponsibilityPaths(m)
  and ImpactedRisks(C) does not intersect ResponsibilityRisks(m)

SelectiveAdmitted(C, P, plan) :=
  not plan.fallback.triggered
  and PolicyContextConsistent(C, P)
  and CoordinatesCurrent(C, P, plan)
  and CompletePartition(O, S, M)
  and SelectedExact(P, S)
  and ExactWitnessClosure(P.catalog, S, plan.selectedWitnesses)
  and forall m in M: OmissionValid(C, P, m)
  and EvidenceClassifiesExactlyOnce(O, S, M)
```

The verifier does not require `S` to be the smallest safe set. Selecting an
additional obligation is conservative. It does require every omission to be
independently justified. Therefore:

```text
SelectiveAdmitted(C, P, plan)
  => forall o in O:
       Selected(o) or IndependentlyProvedNoImpact(o)
```

A triggered fallback is admitted only when it selects every catalog
obligation at full depth, closes witnesses exactly, omits nothing, and carries
one reason-bound fallback evidence record.

## 4. Independence Boundary

The checker shares canonical value contracts, hashing, ordering, and path
matching with the planner. It deliberately does not share planner
orchestration, impact traversal, omission construction, or witness closure.

This is the minimally sufficient boundary:

- sharing immutable syntax prevents two incompatible contract languages;
- independently deriving safety predicates catches planner orchestration
  defects;
- a complete second planner would duplicate selection policy, create drift,
  and reject harmless conservative supersets.

The design proves bounded implementation diversity, not mathematical
independence of every primitive.

## 5. Capacity Projection

Let `T` be the complete admitted repository test inventory and `W` the selected
witness identities:

```text
ProjectedTests(T, W) = {t in T | t.witnessId in W}

ManifestAdmitted(T, W) iff
  every t in T references a catalog witness
  and test identities in T are unique
  and WitnessIds(ProjectedTests(T, W)) = W
```

Tests for known unselected witnesses are intentionally excluded. Unknown
witnesses, duplicate identities, or a selected witness without a test remain
fail-closed.

## 6. Complexity

Catalog hashing costs `O(|catalog bytes|)` once at construction and `O(1)` per
later read. Verification computes graph closure once and reuses it for every
omission:

```text
O(V + E + sum(|responsibilityPaths(o)| * |impactedPaths|))
```

It does not perform one planner replay per omission and does not repeatedly
serialize the catalog. This removes the prior cubic amplification without
introducing persistence or mutable global caches.

For admitted catalog `K` and any later time `t`:

```text
IdentityMapping(K, t) = IdentityMapping(K, construction)
CatalogHash(K) = Hash(IdentityMapping(K, t))
```

The first equality follows from exact immutable containers, members, nested
policies, and primitive identity fields. Therefore the cached hash cannot
become stale without violating constructor admission.

## 7. Rejected Alternatives

| Alternative                                | Rejection                                                                             |
|--------------------------------------------|---------------------------------------------------------------------------------------|
| Replay the same planner once               | Detects stale supplied output but not a defect reproduced by the same implementation. |
| Replay the planner for every omission      | Adds cost without adding independent evidence.                                        |
| Implement a complete second planner        | Duplicates policy, increases drift, and rejects safe conservative supersets.          |
| Accept all tests in a selected manifest    | Makes valid selective execution unreachable.                                          |
| Cache through process-global mutable state | Adds lifecycle and invalidation authority that an immutable value can avoid.          |

## Candidate Input Authority Repair

The 2026-09-25 intake distinguishes structural graph validity from authority:
an internally valid graph supplied by the candidate cannot certify removal of
its own dependency edges. Repository context now requires unchanged exact
base/head graph bytes and independently rejects a graph path change. Missing
baseline, provider failure or unequal bytes produces a FullCI-invalidating
context. Artifact-owned invalidators remain additional restrictions.

```text
GraphEligible := CompleteDiff
  and GraphPathNotChanged
  and BaseGraphBytesPresent
  and HeadGraphBytes = BaseGraphBytes
  and ExistingGraphAdmission

GraphEligible !=> DependencyClosureComplete
```

This is a bounded repair of candidate-controlled replacement, not a claim that
an unchanged graph knows every dependency. Copying the candidate's graph into
a provenance wrapper does not fix the issue. Loading only a baseline graph
would hide new unknown paths unless separately revalidated. Recomputing every
language's dependency graph in the coordinator would add an unqualified compiler
platform. Comparing already bounded immutable artifacts is the smallest local
repair preserving unchanged-graph eligibility and independent FullCI.

The event-specific comparison repair separately distinguishes PR three-dot
semantics from before/after branch transitions. Provider ancestry and requested
identities must justify the interpretation; an empty file list by itself does
not prove equal trees. Exact provider conditions and adversarial fixtures are
part of the implementation plan, not inferred from successful HTTP status.

No signing format, database schema, external effect or enforcement activation
changes. A newly introduced/edited graph intentionally loses selective
eligibility for that transition. Independent native witnesses must demonstrate
both that fallback and the still-reachable unchanged-graph selective path.

The assertion that deterministic admission simply replays the planner is not
true at this source: that checker already derives classification, closure and
impact independently. The separate confirmed gap is post-admission fallback
construction: a faulty shared planner builder could return a partial but
internally consistent fallback. Verifier-owned construction and final catalog
closure now remove that common-mode path. The public omission validator also
requires current policy/input coordinates, just as the main verifier does.
Independent literal-candidate tests exercise stale and incomplete context,
global and transitive impact, identity substitutions and conservative supersets.

Using one verifier-owned closure helper for admission, fallback, advice and
final value checks avoids four disagreeing verifier contracts. Keeping that
helper separate from planner closure preserves the required implementation
diversity. Shared immutable types, canonical hashing and path matching remain
explicit assumptions, not evidence of independence from every possible fault.

## 8. Non-Claims

This design does not approve production omission, prove GitHub provider
behavior, make capacity snapshots reservations, or replace shadow-mode and
production-admission requirements.
