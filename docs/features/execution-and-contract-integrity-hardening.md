# Execution And Contract Integrity Hardening

Status: accepted for implementation

Frozen baseline: `e7eb73fea9e4ceb5a950f9028240fbdc4b93e064`

Owners: `identity_admission`, `plan_issuance`, `config_epochs`,
`control_plane_identity`, `persistence`, `runner_capacity`, repository
proof tooling, and production assurance

Implementation plan:
[Execution And Contract Integrity Hardening Implementation Plan](execution-and-contract-integrity-hardening-implementation-plan.md)

## 1. Decision

Repair ten reproducible implementation gaps and resolve two disputed assurance
claims without adding product authority:

1. bind every signed plan to both the source revision being analysed and the
   exact GitHub Actions execution revision;
2. make configuration-registration evidence a two-way exact relation with its
   owned audit event;
3. use one PostgreSQL relation-kind domain in offline and live principal
   attestation;
4. revoke browser sessions when the control-plane master key rotates;
5. give configuration mutation and query requests finite no-queue lanes and an
   exact non-cacheable HTTP contract;
6. prevent unproved runner observations from authorizing a capacity class;
7. prevent shared-role ACL replacement while another runtime session is live;
8. preserve one machine owner for aggregate coverage and the direct mutation
   job envelope while making their distinct meanings explicit;
9. replace source-text and wall-clock timing evidence with behavioural
   monotonic-clock falsifiers;
10. retain the owner-authorized pre-release migration baseline instead of
    inventing support for an unsupported database state; and
11. classify project-specific FastAPI Production conformance and every live
    production receipt as `NOT_DETERMINED` until their exact evidence exists;
    and
12. project the company deployment runtime as the singleton GIL-enabled
    CPython 3.13.15 contract across packaging, containers, development tooling,
    CI, runtime admission, and documentation.

The architecture audit attached to this change proves no semantic god-file.
`runtime/composition.py`, `operator_override_repository.py`, and the telemetry
branch's `ci_economics_repository.py` remain review candidates, not blockers.
Their decomposition belongs to later changes that can prove stable
responsibilities and preserved cleanup or transaction semantics.

### 1.1 Bounded publication-scope exception

The twelve deltas are not falsely represented as one atomic capability. In
particular, session-handle derivation and the Python runtime profile can be
deployed, proved, and reverted independently. The ordinary change-scope rule would
therefore split them. The product owner has instead required one accelerated
corrective batch before feature work resumes and has accepted its wider review
and revert unit. This is a bounded exception to the default PR-scope rule, not
evidence that the deltas are coupled and not precedent for later changes.

```text
Independent(D2, D12)
and DefaultChangeScopeRule => Split(D2, D12)

OwnerAcceptedBatch
and NoReleasedOrSupportedPredecessor
and OneFrozenBaseAndHead
and OneExactHeadProviderGate
=> PublicationException(CurrentMergeUnit)

PublicationException(CurrentMergeUnit)
!=> Atomic(D1, ..., D12)
PublicationException(CurrentMergeUnit)
!=> PublicationException(AnySuccessorMergeUnit)
```

The exception is invalid if the baseline changes before publication, any delta
requires a separately ordered deployment, or exact-head evidence cannot cover
the complete candidate. Generated projections and proof metadata remain bound
to their semantic owners inside the shared review unit.

## 2. Admission Rule

For a proposed repair `c` and counterexample `x`:

```text
Admitted(c, x) iff
  ReproducibleOnFrozenBaseline(x)
  and ExactSemanticOwner(c)
  and Excludes(c, x)
  and PreservesProtectedObservables(c)
  and NoAuthorityExpansion(c)
  and FailureAlgebraClosed(c)
  and Complexity(c) <= Complexity(minimal correct alternatives)
```

A disputed finding is rejected only when a higher-priority current premise
negates one of its necessary assumptions. Metrics, novelty, documentation age,
and a successful process exit are not semantic evidence.

## 3. Protected Observables

Unless Section 4 explicitly states otherwise:

1. `headSha` remains the source head used for diff and policy analysis.
2. Unknown, stale, incomplete, or inconsistent evidence selects FullCI or a
   typed failure, never selected execution.
3. Existing authorization roles and configuration lifecycle semantics stay
   unchanged.
4. Browser sessions remain opaque, short-lived, server-side records.
5. Audit and configuration registration remain in one transaction.
6. The runtime principal remains a restricted direct-login role.
7. Capacity evidence remains advisory and cannot reserve a runner.
8. The global coverage score remains a coarse regression floor, not evidence of
   semantic test adequacy.
9. Local proof cannot claim provider, deployment, FastAPI Production, capacity,
   or production readiness.

## 4. Explicit Behaviour Deltas

| ID  | Before                                                                                                                                          | After                                                                                                                                                                               | Reason                                                                                                 |
|-----|-------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------|
| D1  | A plan identifies only source `headSha`; a pull-request job may execute an unsigned synthetic merge revision.                                   | Plan request v2 carries `executionSha`; OIDC `sha`, signed payload, target validation, and every checkout agree on it.                                                              | Analysed source and executed tree are independent identities.                                          |
| D2  | Master-key rotation leaves old session-handle digests addressable until expiry.                                                                 | Session lookup digests are domain-separated HMACs derived from the current master key.                                                                                              | Rotation must revoke the authority protected by the key.                                               |
| D3  | Configuration reads have no class bulkhead and configuration writes share only body-retention limits.                                           | Mutations and reads use separate finite no-queue lanes with typed overload and timeout outcomes.                                                                                    | Retained bytes and expensive capability concurrency are independent budgets.                           |
| D4  | Configuration cache policy and OpenAPI response declarations are incomplete.                                                                    | Every response is `no-store`; all reachable statuses and export headers are declared.                                                                                               | An epoch-dependent control-plane response cannot remain cache-valid after transition.                  |
| D5  | Free self-hosted runners are projected into a default capacity class without proving workload eligibility.                                      | Runtime capacity is withheld until an owner-defined class has an exact observed label relation.                                                                                     | Availability does not imply eligibility.                                                               |
| D6  | A shared runtime role can receive successor grants while predecessor sessions remain live.                                                      | ACL application obtains the exclusive fence and rejects any other live session for the target role before its first privilege mutation.                                             | One role cannot expose two unequal exact grant sets concurrently.                                      |
| D7  | Documentation calls the Coverage.py threshold a branch or line floor and separately describes aggregate coverage as informational.              | `backend/pyproject.toml` retains the aggregate combined score floor of `84`; risk-owned line and branch floors plus mutation witnesses remain the semantic adequacy controls.       | These predicates are complementary, not duplicate authorities.                                         |
| D8  | Documentation says the direct mutation job is 250 minutes although the machine plan and workflow use 258; timing tests inspect source spelling. | The quality plan owns a 258-minute envelope and 30-minute reserve; the workflow is an exact tested projection, while elapsed evidence is tested through injected monotonic samples. | One owner plus checked projection prevents drift; behaviour, not spelling, proves timing.              |
| D9  | Container health polling derives its deadline from adjustable civil time.                                                                       | Start, progress, and deadline use one monotonic clock domain and reject a regressed sample.                                                                                         | Civil-clock adjustment cannot lengthen or truncate a process-local bound.                              |
| D10 | A historical review assumes every merged pre-release migration is a supported deployed schema.                                                  | Before the first release, only fresh installation of the current graph is supported; an older development database must be reset. The first release freezes its migration baseline. | The rejected premise contradicts product axiom PA-6 and the owner-admitted migration squash.           |
| D11 | A historical review treats FAPI solely as Financial-grade API and concludes that FastAPI Production mapping is inapplicable.                    | FastAPI Production applicability is distinct and remains `NOT_DETERMINED` until an exact project context and conformance record are admitted.                                       | Name collision cannot decide applicability.                                                            |
| D12 | Runtime authority admits CPython 3.13.15 and 3.14.7 although the company deployment stack requires only 3.13.15.                                | Runtime authority is the singleton GIL-enabled CPython 3.13.15 set; every executable and dependency surface is its exact projection.                                                | Supporting an unrequired runtime adds matrix, lock, and compatibility cost without product capability. |

D2 intentionally invalidates every browser session created with the former
unkeyed digest representation when this change is first deployed, even if the
master-key bytes do not change. Users must authenticate again. Accepting the
legacy digest as a fallback would preserve exactly the rotation bypass being
removed.

## 5. Formal Invariants

### 5.1 Execution identity

Let `S` be the source head, `E` the GitHub execution revision, `T.sha` the
verified OIDC claim, and `C_j` the revision checked out by execution job `j`.

```text
SelectedOrFallbackExecution(p) =>
  Signed(p.request.sourceHead = S)
  and Signed(p.request.executionSha = E)
  and T.sha = E
  and for every j in ExecutedJobs(p): C_j = E
```

Therefore a valid signature for `S` cannot authorize an unidentified checkout.

### 5.2 Registration evidence

For registrations `R` and owner events `A`:

```text
ValidRegistrationEvidence iff
  for every r in R: exists exactly one a in A: ExactPair(r, a)
  and for every a in A: exists exactly one r in R: ExactPair(r, a)
```

`ExactPair` compares event and operation identity, repository scope, epoch,
source format and hashes, schema version, subject, and canonical payload.

Migration admission evaluates the complete relation while it holds the
exclusive compatibility fence. Any future explicit deep-integrity caller must
hold an equivalent admitted fence. Ordinary runtime operations use the
following inductive contract instead of rescanning an unbounded ledger:

```text
ValidRegistrationEvidence(0)
ValidRegistrationEvidence(n)
and AtomicExactPairAppend(n + 1)
and ImmutableHistory(0..n)
=> ValidRegistrationEvidence(n + 1)
```

An exact replay performs a point lookup and verifies the retained event's
derived hashes before comparing its input identity. Thus per-operation proof
cost is independent of historical registration cardinality. Arbitrary SQL by
the migration principal is outside this runtime proof and requires a fresh
deep-integrity qualification before release. The migration-time scan applies
streaming only to its exact `SELECT`; it must not mutate connection-wide
execution options because later DDL and DML share that transaction.

### 5.3 Principal relation domain

```text
LiveRelationKinds = OfflineRelationKinds = {r, p, v, m, f}
```

The equality is owned by one constant, so a grant on any admitted PostgreSQL
relation kind cannot disappear from one proof plane.

### 5.4 Session rotation

```text
SessionDigest(K, h) = HMAC-SHA256(Derive(K, session-handle-info), h)
K_old != K_new => SessionDigest(K_old, h) != SessionDigest(K_new, h)
```

The implication is computational subject to the HMAC collision bound. Old rows
may remain until expiry cleanup but cannot authenticate after rotation.

### 5.5 Shared-role cutover

Let `K` be the database instant after the exclusive fence is acquired and
before the first ACL mutation:

```text
InstallSharedRoleAcl(R, A_new, K) =>
  OtherLiveSessions(R, K) = empty
  and ExactAclAfterCommit(R) = A_new
```

This is necessary but not sufficient for a rollout: the deployment owner must
also prevent a drained predecessor from restarting through the cutover. If
zero-downtime mixed-version rollout becomes mandatory, generation-specific
roles are required.

### 5.6 Quality and time ownership

```text
AggregateCoverageGate :=
  CoveragePyCombinedScore >= 84

SemanticTestAdequacy :=
  RiskOwnedLineAndBranchFloors
  and ChangedCriticalFloors
  and RequirementFalsifiers
  and NamedMutationWitnesses

DirectMutationJobAdmitted :=
  Sum(SuiteOuterTimeouts) + 30 minutes <= 258 minutes
  and WorkflowTimeout = QualityPlanTimeout

ProcessDeadlineAdmitted :=
  Start, Progress, Deadline belong to one monotonic clock domain
```

No implication from `AggregateCoverageGate` to `SemanticTestAdequacy` is
claimed.

### 5.7 Migration and conformance epochs

```text
not FirstReleasePublished
and ProductAxiom(PA-6)
=> SupportedDatabaseOrigins = {fresh current migration graph}

FirstReleasePublished
=> every migration in that release is immutable

FastAPIProductionConformant
=> ExactProjectContext
   and ExactProfileIdentity
   and CompleteConformanceRecord
   and RequiredExternalReceipts
```

The antecedents of the last implication are incomplete, so the current result
is `NOT_DETERMINED`, never `CONFORMANT`.

### 5.8 Runtime identity

```text
RequiredBackendBehaviours subset Behaviours(CPython 3.13.15)
CompanyDeploymentRuntime = CPython 3.13.15
AddedProductCapability(CPython 3.14.7) = empty
Cost({3.13.15, 3.14.7}) > Cost({3.13.15})
=> SupportedRuntime = {GIL-enabled CPython 3.13.15}
```

Package installation and runtime startup both reject every other patch and
build variant. The repeated guard is intentional: package metadata controls
resolution, while startup admission protects copied environments and direct
entrypoint invocation.

This decision supersedes only the runtime-version clauses in
`docs/decisions/local-development-environment.md` and
`docs/decisions/python-proof-tooling.md`. Those predecessor bytes remain
historical evidence; their unrelated topology and tooling decisions remain in
force.

## 6. Alternatives Rejected

| Alternative                                                            | Rejection proof                                                                                                                                                                                                                                                                          |
|------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Treat `headSha` as both source and execution identity.                 | False for pull requests because GitHub may execute a synthetic merge commit.                                                                                                                                                                                                             |
| Rescan every registration/event pair before each config transaction.   | The unbounded ledger makes operation `k` cost `Omega(k)` and sequential growth `Theta(N^2)`; a privileged writer can still race after the scan. Atomic append-only construction plus exact replay verification preserves the admitted invariant at constant historical cardinality cost. |
| Put the master-key fingerprint in the global authority profile digest. | It revokes unrelated authority and couples secret rotation to a broader contract.                                                                                                                                                                                                        |
| Infer runner class from availability alone.                            | Required labels and workload eligibility are absent.                                                                                                                                                                                                                                     |
| Allow an ACL superset during rolling deployment.                       | It violates exact least privilege and still does not prove predecessor safety.                                                                                                                                                                                                           |
| Add a forward migration for an unsupported pre-release database.       | It contradicts PA-6, restores intentionally retired history, and creates two fresh-install paths without an adopted compatibility consumer.                                                                                                                                              |
| Delete the aggregate coverage floor.                                   | It removes a cheap whole-package regression signal without reducing duplication: the floor and risk-owned gates prove different predicates.                                                                                                                                              |
| Remove timeout from the machine plan and derive it in workflow YAML.   | It gives the projection authority and makes repository tooling unable to validate the complete envelope independently.                                                                                                                                                                   |
| Declare FastAPI Production non-applicable from the FAPI acronym.       | It equivocates between unrelated specifications.                                                                                                                                                                                                                                         |
| Retain CPython 3.14.7 as a compatibility runtime.                      | It enlarges the lock artifact and adds a second witness epoch while providing no owner-required behaviour on the 3.13.15 deployment stack.                                                                                                                                               |
| Split large files solely by metrics.                                   | Metrics do not prove forbidden semantic co-ownership.                                                                                                                                                                                                                                    |

## 7. Falsifiers And Revision Triggers

The design is falsified if a plan executes a checkout other than signed
`executionSha`; a registration or owner audit event lacks an exact counterpart;
live and offline privilege domains differ; an old session authenticates after
key rotation; config overload reaches capability I/O; unproved capacity narrows
a plan; ACL mutation starts while another runtime session is live; a
process-local deadline depends on civil time; workflow timeout diverges from
the machine plan; a backend executable admits a runtime other than GIL-enabled
CPython 3.13.15; or a conformance result is asserted without complete exact
evidence.

Revisit this decision when exact runner eligibility evidence exists,
zero-downtime ACL cutover becomes a hard requirement, the first product release
is published, the company deployment runtime changes, or a complete FastAPI
Production context and conformance record is admitted.

## 8. Non-Claims

This repair does not provide runner reservations, hosted-runner quota
prediction, distributed HTTP fairness, zero-downtime shared-role ACL expansion,
production capacity, distributed OAuth abuse control, all-replica break-glass
rotation, AnyIO parent-side serialization evidence, edge-policy receipts,
provider correctness, or production readiness.
