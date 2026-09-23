# Bootstrap Workflow Contract

Status: normative adoption fixture contract

Date: 2026-09-05

## 1. Purpose

`Dynamic CI Bootstrap` is the single stable GitHub required check that selects
either a signed, statically authorized witness execution or the complete
repository-owned FullCI path.

The adoption fixture consists of:

```text
fixtures/target-repository/.github/workflows/ci-coordinator-bootstrap.yml
fixtures/target-repository/.ci-coordinator/ci-coordinator.cjs
fixtures/target-repository/.ci-coordinator/execution-registry.v1.json
fixtures/target-repository/.ci-coordinator/plan-trust-root.v1.json
fixtures/target-repository/.ci-coordinator/run
```

The last path is a required target-owned executable and is intentionally not
implemented by this repository fixture.

## 2. Formal Contract

Let:

- `E` be an Ed25519-valid, unexpired envelope bound to the current repository,
  event, ref, run, attempt, and OIDC workflow identity;
- `R@workflowSha` be the canonical target execution registry loaded from the
  authenticated workflow revision;
- `C` be the complete configured validation catalog admitted by the coordinator
  before issuance;
- `S` be a selected execution whose `targetRegistryHash = H(R)`;
- `StaticJob(p)` be the reviewed workflow job named by the registry binding for
  execution profile `p`;
- `PlanJob(R)` be the one registry-bound routing job directly required by every
  registered execution job;
- `PlanRequestJob(R)` be the one registry-bound immutable external reusable
  workflow call that is the sole holder of OIDC minting authority;
- `FullCI(R)` be the target-owned complete validation route: the one
  registry-bound fallback job for witness shards, or every registered
  execution job for native execution.

```text
SelectedAdmitted(E, R) :=
  ValidEnvelope(E)
  and E.execution.mode = "selected"
  and E.verifiedPlanId = E.planId = E.execution.verifiedPlanId
  and E.productionAdmissionReceiptId matches ^production_admission_[0-9a-f]{32}$
  and E.fallbackReason = null
  and E.execution.targetRegistryHash = H(R)
  and ExactGitObjectAdapterSnapshot(R@workflowSha)
  and ExactGeneratedControlPlane(R@workflowSha)
  and ExactCompleteNeeds(R@workflowSha)
  and needs(PlanJob(R)) = {PlanRequestJob(R)}
  and ExactTrustedPlanRequester(
        R.planRequestWorkflowRef,
        E.authenticatedRun.jobWorkflowRef,
        E.authenticatedRun.jobWorkflowSha)
  and OidcAuthority(PlanRequestJob(R))
  and not OidcAuthority(PlanJob(R))
  and forall job in R.executionJobs: PlanJob(R) in job.needs
  and forall profile in E.execution.profiles:
        ExactComponentBinding(profile, R)
        and StaticJob(profile.id) exists
  and exactly one:
        E.execution.executionKind = "witness-shards"
        and ExactShardCoverage(E.execution)
        and forall shard: DerivedProviderSignal(shard)
      or
        E.execution.executionKind = "native-job-set"
        and NonEmptyCanonicalStaticJobSelection(E.execution, R)
        and SelectedJobs(E.execution)
              = TransitiveDependencyClosure(E.execution.profiles, R)
        and NoShardMatrix(E.execution)
  and RequiredJobs(R) subset SelectedJobs(E.execution)
  and BoundedDownloadAndStableRead(E)
  and Utf16Bytes(AllPlanJobOutputs(E)) <= SafeProviderOutputBudget

FallbackAdmitted(E) :=
  ValidEnvelope(E)
  and E.execution.mode = "full-ci"
  and E.verifiedPlanId = null
  and E.productionAdmissionReceiptId = null
  and E.verifierVersion = null
  and E.fallbackReason = E.execution.reason != null

CoordinatorSelectedIssuanceAdmitted(E, R, C) :=
  SelectedAdmitted(E, R)
  and ids(R.profiles) = ids(C.executionProfiles)
```

Execution law:

```text
SelectedAdmitted(E, R) and kind = witness-shards
  => run only StaticJob(profile) with signed shard IDs
SelectedAdmitted(E, R) and kind = native-job-set
  => run exactly TransitiveDependencyClosure(selected profile jobs, R)
FallbackAdmitted(E) and kind = witness-shards
  => run exactly R.fallbackJobId, whose needs = {PlanJob(R)}
FallbackAdmitted(E) and kind = native-job-set
  => R.fallbackJobId = null and run every registered native execution job
Unavailable(Coordinator or OIDC or key or registry) => FullCI
Invalid(E or R or selected closure) => FullCI
StaticJobSetMismatch => FullCI
NonRegularOrMismatchedAdapterBlob => FullCI
InvalidGeneratedControlPlane => FullCI
MissingRequiredJob => FullCI
ProviderOutputBudgetExceeded => FullCI
Missing(.ci-coordinator/run) => failure
```

No plan field can supply a command, executable path, argument vector, action,
workflow reference, runner label, permission, credential, fixture, or service.

The generated gate validator admits exactly one terminal route:

```text
SelectedGreen :=
  PlanSucceeded
  and PlanValid
  and not Fallback
  and SelectedJobs is non-empty
  and forall static job:
        result = success iff job is selected
        result = skipped iff job is not selected
  and exactly one:
        executionKind = "witness-shards"
        and FullCI.result = skipped
      or
        executionKind = "native-job-set"
        and NoSeparateFullCIResult

FallbackGreen :=
  not (PlanSucceeded and PlanValid and not Fallback)
  and SelectedJobs is empty
  and exactly one:
        executionKind = "witness-shards"
        and forall static job: result = skipped
        and FullCI.result = success
      or
        executionKind = "native-job-set"
        and forall static job: result = success
        and NoSeparateFullCIResult

GateGreen := exactly_one(SelectedGreen, FallbackGreen)
```

## 3. Revision Separation

The two repository revisions have different owners:

```text
workflowSha -> workflow, control bundle, plan trust root, and target execution registry authority
headSha     -> code diff, test manifest, and dependency graph subject
```

The credential-free invocation classifier first compares caller and
defining-job workflow refs exactly. The immutable platform request job performs
OIDC and the plan HTTP exchange without checking out target bytes only when
that comparison admits a direct invocation. The dependent unprivileged plan job checks
out `job.workflow_sha`, consumes bounded plan chunks, and validates the
registry and trust root. The coordinator
loads the registry, every top-level workflow, the complete local
reusable-workflow closure, and the generated control bundle through the
exact Git commit/tree/blob chain at `workflow_sha`; it loads the test manifest
from `head_sha`. Therefore a pull request cannot obtain new adapter authority
merely by changing hot policy or target test data.

Every adapter member must be a regular Git blob whose recomputed SHA-256 equals
the registry binding. Registry self-description alone is not proof. The
workflow-list endpoint is not used on this path because its mutable
default-branch view cannot strengthen an immutable-revision proof.

## 4. Static Jobs And Signals

The fixture contains one example profile job:

```text
profileId: python-linux
workflowPath: .github/workflows/ci-coordinator-bootstrap.yml
jobId: selected-python-linux
```

Its runner, permissions, credentials, fixtures, and services are static YAML.
Only the signed shard document enters its profile-specific matrix. Its visible
job name is the derived `providerSignal.jobName`, allowing reconciliation to
observe the exact same run and attempt.

For more than one profile, the target workflow adds one static selected job per
registry binding. The validator emits canonical maps keyed by those static job
ids plus the exact selected-job set. Every static job selects only its own
matrix entry, and the aggregator admits exactly the selected jobs. A target
cannot combine profiles with different static authority in one dynamic job.

The internal FullCI job has the stable diagnostic name:

```text
ci/full-ci/ed96dc58121d215d5431
```

That diagnostic name is not reconciliation authority. `Dynamic CI Bootstrap`
is the aggregate provider signal for FullCI and native-job-set execution, and
remains the sole branch-protection gate. Selected witness-shard jobs provide
additional reconciliation evidence but are not independently configured
required checks. A native adapter uses the same gate validator but runs every
registered static execution job during fallback; replacing those jobs with the
fixture's monolithic FullCI entrypoint is not semantics-preserving by default.

## 5. Required Properties

The fixture must:

1. run on `pull_request`, `push`, `merge_group`, and manual dispatch without path filters;
   request a selected plan only for direct dynamic events after exact non-empty
   equality between caller and defining-job workflow refs, while manual or
   same-repository reusable invocation reaches native FullCI;
2. avoid `pull_request_target`, repository secrets, and write permissions;
3. grant `id-token: write` only to an exact full-SHA platform reusable workflow
   job that executes no target-controlled bytes;
4. pin third-party actions to immutable commit SHAs;
5. bound OIDC and coordinator response bytes while downloading, bound stable
   local-file reads before allocation, and bound plan validation, selected
   execution, and FullCI time;
6. use the immutable platform requester as the sole OIDC and plan HTTP
   implementation, bind the OIDC audience to the exact HTTPS plan URL, and
   convert every post-resolution unavailable, non-successful, malformed, or
   oversized response to local FullCI;
7. serialize request JSON structurally rather than by shell interpolation;
8. validate exact envelope and payload shapes before selected execution;
9. validate a bounded canonical repository-local Ed25519 trust root,
   `targetRegistryHash`, static profile components, shard closure, and every
   `providerSignal`;
10. invoke only `.ci-coordinator/run witness` or `.ci-coordinator/run full-ci`;
11. execute FullCI for every unavailable, malformed, stale, mismatched, or unsupported state;
12. end in one always-run aggregator named `Dynamic CI Bootstrap`;
13. call the generated registry-bound gate validator and accept only when
    exactly one execution path succeeded and the other was skipped;
14. reject selected execution before writing outputs when the complete UTF-16
    plan-job output exceeds the conservative provider transport budget;
15. require an exact registry-to-static-job set and support every selected
    execution profile through its own statically authorized matrix job;
16. require every registry-declared mandatory job in selected execution;
17. accept selected execution only from coordinator issuance that proved exact
    equality between registry and configured validation-catalog profile sets.
    The target independently verifies the signed envelope and exact registry
    hash; it does not claim to receive or re-evaluate the complete catalog;
18. match one generated normalized classifier/request/plan/gate safety
   projection; every workflow
   in the reachable local closure declares no workflow ambient
   environment/defaults and no job-level or step-level `continue-on-error`.
19. bind `planRequestJobId` and `planRequestWorkflowRef` in the target registry,
    require exact `job_workflow_ref` and `job_workflow_sha` equality, and carry
    plan bytes only through bounded canonical chunks that target validation
    treats as data.
20. require the fixed classifier to have no permissions, checkout, network
    command, or dependency; require the requester to depend exactly on it; and
    reject empty, case-different, or unequal caller/defining workflow refs.
21. bind plan and gate checkout and validation to `job.workflow_ref` and
    `job.workflow_sha`; reusable caller identity cannot replace the workflow
    file that defines those jobs.

## 6. Control Runtime Decision

The trust-edge control sources remain CommonJS because the hosted runner already
provides Node.js, whose standard library supplies Ed25519 verification and the
same JSON number serialization used by the signing contract. Replacing it with
Python would require either installing a cryptographic dependency before trust
is established or maintaining a second canonical-number implementation.

Bounded I/O and canonicalization, envelope and run binding, execution
projection, plan transport, and gate validation remain separate source modules.
An exact esbuild projection packages them behind one closed command dispatcher
without making the generated file an architectural owner. The target therefore
reviews and executes one artifact while source ownership remains decomposed.

This is a bounded target integration exception. Backend code, repository proof
tooling, generators, mutation runners, container checks, and internal scripts
remain Python.

## 7. Adoption

A target repository generates and checks its artifacts with:

```text
ci-coordinator-target-artifacts render --source SOURCE --output-directory .ci-coordinator
ci-coordinator-target-artifacts check --source SOURCE --output-directory .ci-coordinator
```

These commands own three repository-specific JSON artifacts and one versioned
CommonJS control bundle. The bundle originates from a visible wheel resource
and is rendered as a byte-exact `.ci-coordinator` mirror; target repositories
do not maintain an independent control implementation.

It renders the reviewed plan-signing trust root separately because key rotation
has a different owner and lifecycle from execution-registry generation:

```text
ci-coordinator-target-artifacts render-trust-root --key-id KEY_ID --public-key PLAN_PUBLIC_KEY_PEM --output .ci-coordinator/plan-trust-root.v1.json
ci-coordinator-target-artifacts check-trust-root --key-id KEY_ID --public-key PLAN_PUBLIC_KEY_PEM --output .ci-coordinator/plan-trust-root.v1.json
```

The canonical trust root stores only the key id, algorithm, and DER-encoded
public key. It is reviewed and version-controlled at the authenticated workflow
revision. Rotation is an explicit repository change; ambient variables cannot
replace the verifier's trust root.

It must implement a version-controlled executable `.ci-coordinator/run` with a
closed mode and witness dispatch table. The `witness` mode reads the bounded
`CI_COORDINATOR_SHARD_JSON` document as data and never evaluates it as shell.
The `full-ci` mode executes the complete repository-owned validation path.

Required repository variables are:

```text
CI_COORDINATOR_INSTALLATION_ID
CI_COORDINATOR_PLAN_URL
```

`CI_COORDINATOR_PLAN_URL` is also the exact OIDC audience. The service requires
both the configured top-level `workflow_ref` identity and the registry-bound
called `job_workflow_ref` identity. Target registry selection, aggregate-gate
authority, and reconciliation remain bound to the selected direct top-level
workflow path. Repository and run claims, or either workflow namespace alone,
do not grant execution authority. Same-repository reusable invocation does not
request selected execution; its plan and gate jobs use defining-job identity
and totalize through native FullCI.

If GitHub cannot resolve or authorize the reusable workflow, the caller never
starts and cannot execute local fallback code. During adoption the repository
therefore retains an independently runnable native Full Check. This is a
provider hard failure, not a successful FullCI result.

The plan-signing trust root is independent from the production-admission key.
The former lets a target repository verify service envelopes; the latter lets
the service authenticate an external production-evidence assertion. Reusing a
key across those authority domains is not implied or recommended.
