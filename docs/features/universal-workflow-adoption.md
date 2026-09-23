# Universal Workflow Adoption

Status: accepted for implementation

Date: 2026-07-29

Owners: `ci-coordinator.workflow-discovery`,
`ci-coordinator.execution-orchestration`, target-repository validation owners

## 1. Decision Summary

CI Coordinator supports arbitrary GitHub Actions repositories through total
classification, not universal semantic inference.

```text
EveryWorkflow
  -> in_place_job_set
  | reusable_workflow_set
  | witness_shards
  | full_only
  | invalid
```

Only target-owner-admitted workflows may enter selected execution. Every other
workflow remains usable through its native full-validation path or is reported
invalid.

Selected execution has two explicit forms:

```text
`witness-shards`:
  coordinator partitions target-declared work items across static matrix jobs

`native-job-set`:
  coordinator selects target-declared static jobs while the target workflow
  retains commands, runners, permissions, secrets, services, dependencies,
  and one native aggregate gate
```

These forms share planning, verification, identity, signing, freshness,
production admission, fallback, and audit semantics. They do not share
provider-signal or runtime-execution semantics and therefore must not be
represented as one implicit shard model.

## 2. Scope

This design introduces:

- a workflow-scoped target execution registry;
- explicit execution kinds;
- a declared native aggregate provider signal;
- exact current-workflow validation in the target-side verifier;
- total workflow adoption assessments;
- conservative target-artifact and proposal output for unsupported workflows.

It does not:

- execute arbitrary plan-supplied commands;
- infer test adequacy from workflow YAML;
- mutate a repository or provider configuration;
- authorize omission without accepted target semantics and production
  admission;
- claim that every workflow can be optimized.

## 3. Formal Boundary

Let:

- `S(w)` mean exact bounded syntax evidence exists for workflow `w`;
- `T(w)` mean target-owner semantic projection exists for `w`;
- `R(w)` mean a revision-bound execution registry admits `w`;
- `P(w)` mean provider inventory proves `w` and every registered static job at
  the authenticated workflow revision;
- `G(w)` mean the exact repository-owned aggregate gate for `w` is known;
- `F(w)` mean the execution-kind-specific FullCI route is structurally proved;
- `C(w)` mean all execution-kind-specific capacity inputs are available;
- `A(w)` mean the production admission receipt authorizes the exact subject;
- `K(w)` mean the signed plan binds the current run, attempt, ref, workflow ref,
  workflow SHA, registry hash, and freshness interval.

```text
TargetAuthority(w) := S(w) and T(w) and R(w) and P(w) and G(w) and F(w)

ExecutionReady(w) :=
  TargetAuthority(w)
  and (kind(w) = native-job-set or C(w))

Selectable(w) := ExecutionReady(w) and A(w) and K(w)
not Selectable(w) => NativeFull(w)
```

Target authority and execution readiness are distinct proofs. In particular,
an unavailable shard manifest or runner snapshot cannot erase an already
verified workflow gate:

```text
TargetAuthority(w) and not C(w)
  => NativeFull(w) observed through G(w)
```

When `TargetAuthority(w)` is absent, the coordinator records no reconciliation
subject. It must not invent a provider signal.

Target projection has exactly two consumers:

```text
NeedsTargetProjection :=
  production admission configured
  or durable reconciliation configured

not NeedsTargetProjection => no provider target or capacity I/O
```

`P(w)` and `G(w)` require all of:

```text
OIDC.workflow_ref path = registry.workflowPath
provider workflow path = registry.workflowPath
provider static gate job id = registry.gateJobId
provider static non-matrix job name = registry.gateSignalName
provider top-level job ids
  = {planJobId, gateJobId, optional fallbackJobId, execution job ids}
needs(planJobId) = {}
provider gate direct needs
  = {planJobId, optional fallbackJobId, execution job ids}

kind(w) = witness-shards
  => registry.fallbackJobId identifies one static job
  and needs(fallbackJobId) = {planJobId}

kind(w) = native-job-set
  => registry.fallbackJobId = null
  and NativeFull(w) executes every registered execution job
```

`job_workflow_ref` identifies a called reusable workflow and cannot substitute
for the top-level run workflow identity.

Discovery alone proves only `S(w)`.

```text
S(w) does not imply T(w)
S(w) does not imply R(w)
S(w) does not imply Selectable(w)
```

The universal product claim is:

```text
forall w in discovered workflows:
  exactly_one(assessment(w))
```

The product does not claim:

```text
forall w: Selectable(w)
```

## 4. Impossibility Boundary

GitHub Actions jobs may invoke arbitrary programs and remote systems. If a
total coordinator algorithm could decide whether any arbitrary job was
semantically unnecessary, it could decide whether an arbitrary program changes
a protected outcome, which includes termination-dependent behavior. Therefore
unrestricted semantic necessity is not a computable total function.

The safe construction replaces inference with an owner-declared finite
language:

```text
changed paths
  -> capabilities
  -> obligations
  -> witnesses
  -> execution profiles
  -> static workflow jobs
```

Every missing edge is an unknown and routes to full validation.

## 5. Registry Model

The target execution registry owns two entity sets.

```text
WorkflowBinding := {
  workflowPath,
  executionKind,
  planJobId,
  fallbackJobId,
  gateJobId,
  gateSignalName,
  executionJobs: [{jobId, needs}]
}

ProfileBinding := {
  profileId,
  workflowPath,
  jobId,
  executionKind,
  runnerProfileId,
  permissionProfileId,
  credentialProfileId,
  fixtureProfileId,
  serviceProfileIds,
  capacityClassId
}
```

Registry laws:

```text
forall profile:
  exists exactly one workflow:
    profile.workflowPath = workflow.workflowPath

profile.executionKind = workflow.executionKind

ids(workflow.executionJobs)
  = canonical set of profile.jobId for that workflow

ids(registry.profiles)
  = ids(validationCatalog.executionProfiles)

forall job in workflow.executionJobs:
  planJobId in job.needs
  and job.needs subset (ids(workflow.executionJobs) union {planJobId})
  and job.jobId not in job.needs

acyclic(workflow.executionJobs)

executionKind = witness-shards
  iff fallbackJobId is one canonical static job id

executionKind = native-job-set
  iff fallbackJobId = null

pairwiseDistinct(
  planRequestJobId,
  planJobId,
  fallbackJobId when non-null,
  gateJobId,
  ids(workflow.executionJobs)
)

unique(workflowPath)
unique(profileId)
unique(workflowPath, jobId)
unique(workflowPath, gateSignalName)
unique(asciiLower(planRequestJobId, planJobId, gateJobId, fallbackJobId, execution job ids))

needs(planJobId) = {planRequestJobId}
```

The registry contains identities and the complete top-level job dependency
relation needed to prove an executable selected set. The exact-revision
workflow job set contains only the plan request, plan, execution, kind-specific
fallback, and aggregate gate roles. The plan depends exactly on the plan
request, every selectable job directly depends on that plan, and no unclassified job or undeclared
prerequisite can execute on a selected route. Job-role IDs must also remain
unique under GitHub's case-insensitive expression comparison so one selected ID
cannot activate another job. Exact profile-set equality prevents a
registry-only dependency job from acquiring execution authority outside
configured policy. The registry does not contain commands, action references,
environment values, or credentials.

For `witness-shards`, `fallbackJobId` closes availability proof over one
repository-owned aggregate FullCI job whose exact dependency is `planJobId`.
For `native-job-set`, a separate fallback job is forbidden because the full
route is the complete registered native job graph. This distinction prevents a
synthetic monolithic command from silently replacing repository-owned
semantics.

## 6. Execution Forms

### 6.1 Witness shards

The existing form remains valid:

- one static target matrix job per execution profile;
- a signed selected profile set already closed over every registered
  execution-job dependency;
- selected tests partitioned into content-addressed shards;
- one derived provider signal per shard;
- target-owned `.ci-coordinator/run witness`;
- one registry-bound target-owned `.ci-coordinator/run full-ci` fallback job.

The signed execution carries matrices and bounded parallelism.

### 6.2 Native job set

The native form is admitted when:

- a target semantic projection maps obligations to static profile/job IDs;
- every selected profile maps to one current-workflow static job;
- the selected root set is non-empty;
- the target validator expands it to the registry-bound transitive dependency
  closure before exposing job-selection outputs;
- exact-revision provider inventory proves that every registered
  complete `needs` relation, including the plan job, still matches the
  workflow;
- the complete top-level workflow job set equals the registered plan request,
  plan, execution, kind-specific fallback, and gate roles;
- the plan depends exactly on the registered plan request job;
- every role ID is unique under GitHub's case-insensitive expression
  comparison;
- the workflow owns one static aggregate gate;
- the aggregate gate truth table rejects selected skips, unexpected
  executions, failures, and cancellations;
- full mode selects every registered execution job.

The signed execution carries:

- selected obligation and witness identities;
- selected native profile and job identities;
- exact registry hash;
- exact workflow path;
- one declared aggregate provider signal.

It carries no shard matrix because no coordinator-owned shard execution occurs.

## 7. Provider Signal Algebra

Two signal constructors are admitted:

```text
DerivedShardSignal(profileId, shardId)
DeclaredNativeSignal(workflowPath, gateJobId, gateSignalName)
```

Both are content-addressed. A declared native signal is valid only when its
exact coordinates came from the trusted target registry and provider inventory
proved the exact workflow and gate job at the authenticated workflow revision.
The provider proof includes one explicit static non-matrix
`jobs.<gateJobId>.name` equal to `gateSignalName`; a job id alone is
insufficient.
The same declared gate identity observes FullCI fallback for either execution
kind. Derived shard signals are used only when a selected witness-shard
execution is authorized.

The signal name is observed in one exact workflow run and run attempt.
Repository/run identity remains part of the reconciliation subject, so a
same-named check in another repository or run cannot satisfy it.

## 8. Target Validator

The target validator receives:

```text
CI_WORKFLOW_PATH
CI_STATIC_JOB_IDS_JSON
CI_TARGET_REGISTRY_PATH
CI_WORKFLOW_REF
CI_WORKFLOW_SHA
```

It must:

1. verify the signed envelope and exact run identity;
2. derive the current workflow path from the trusted workflow-ref binding and
   compare it with the bounded local path input;
3. select exactly one workflow registry slice;
4. require an exact bijection between declared static execution jobs and that
   slice;
5. reject selected profiles from another workflow;
6. output only canonical selected job IDs and shard matrices appropriate to the
   registry execution kind;
7. map all invalid states to full fallback or explicit failure.

No global bootstrap path constant is permitted.

The generated gate validator receives only plan-mode facts, the canonical
selected-job set, one exact result per registry-bound static job, the
kind-specific FullCI result, and the current workflow path/ref plus registry.
It admits exactly one selected or fallback terminal route. Sharded fallback
requires every selected job to skip and the one registry-bound FullCI job to
succeed. Native fallback requires every registered static job to succeed and
forbids any separate FullCI result. The validator does not interpret commands,
job semantics, workflow expressions, credentials, or runner policy.

## 9. Adoption Assessment

The read-only assessment is a derived navigation artifact.

```text
AdoptionAssessment := {
  workflowPath,
  state,
  recommendedAdapter,
  blockers,
  requiredOwnerInputs,
  inventoryDigest,
  nonClaims
}
```

Decision order:

1. parse failure => `invalid`;
2. incomplete or open graph => `full_only`;
3. no accepted target semantic contract => `full_only`;
4. an accepted existing witness-shard target => `witness_shards`;
5. static in-place graph and accepted owner policy => `in_place_job_set`;
6. accepted reusable-only target contract when in-place is unavailable =>
   `reusable_workflow_set`;
7. otherwise => `full_only`.

The assessment cannot activate policy, mutate provider state, or authorize
omission.

`in_place_job_set` and `reusable_workflow_set` describe the target-owned
execution-job shape, not additional runtime execution kinds. Both compile to
`native-job-set`; the distinction guides adoption without duplicating runtime
authority semantics. Once coordinator infrastructure is present, adapter
classification considers only the exact registry-bound execution jobs and
excludes the plan and aggregate gate.

`witness_shards` reports an already-adapted sharded target without recommending
a native adapter. Like every adoption state, it is navigation evidence only;
the execution registry and production admission remain the runtime authorities.

The discovery adapter reads the workflow sources and target registry from one
exact commit tree. The discovery domain consumes only a bounded read-only
projection of registry hash, workflow path, execution kind, plan job, optional
fallback job, execution job dependency graph, gate job, and gate signal name.
It does not import or retain the execution-authority registry. The API exposes
only projection status and registry hash. This is the minimum boundary strength
that prevents a navigation read model from becoming an execution owner while
preserving exact comparison and identity semantics.

## 10. Failure Algebra

```text
UnknownWorkflowSyntax             => full_only
MissingTargetSemanticProjection   => full_only
RegistryWorkflowAbsent            => FullCI
RegistryKindMismatch              => FullCI
StaticJobSetMismatch              => FullCI
StaticJobDependencyMismatch       => FullCI
MissingOrInvalidShardedFallback   => FullCI
ForeignWorkflowSelectedProfile    => FullCI
MissingNativeGate                 => FullCI
AmbiguousNativeGateSignal         => terminal reconciliation failure
MissingTargetAuthority            => FullCI without reconciliation registration
MissingShardCapacityEvidence      => FullCI through the exact workflow gate
RemoteRequesterResolutionFailure  => dynamic workflow hard failure; independent adoption FullCI remains eligible
StaleWorkflowRevision             => FullCI
PlanFromPriorRunAttempt           => FullCI
UnsupportedExecutionKind          => FullCI
ExecutablePlanField               => reject
```

If the workflow cannot enter native full execution after plan infrastructure
fails, the native gate must fail. Failure to optimize is never converted into a
successful omission.

## 11. Protected Behavior

For `native-job-set`, protected equivalence means:

- every selected job executes its original target-owned definition;
- full mode executes every original registered execution job;
- original runner, permission, secret, service, environment, action, command,
  timeout, dependency, and artifact semantics remain target-owned;
- the same native aggregate gate remains the required provider signal;
- selected failure remains failure;
- omitted work is explicit and auditable.

All-observable equivalence is not claimed because adding plan routing changes
the workflow DAG, job inventory, and timing.

## 12. Counterexamples

| ID     | Counterexample                                                                                        | Exclusion                                                                       |
|--------|-------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------|
| UWA-01 | Bootstrap path is accepted for a different workflow                                                   | current workflow slice binding                                                  |
| UWA-02 | Native job is encoded as a fake one-shard test                                                        | explicit execution kind                                                         |
| UWA-03 | Reusable child checks do not match derived shard names                                                | native aggregate signal                                                         |
| UWA-04 | Same gate name in another run satisfies reconciliation                                                | exact reconciliation subject                                                    |
| UWA-05 | Unknown shell behavior is treated as safe omission                                                    | target semantics or full-only                                                   |
| UWA-06 | Registry selects a job from another workflow                                                          | per-workflow profile closure                                                    |
| UWA-07 | Empty selected set produces a green no-op                                                             | selected set must be non-empty                                                  |
| UWA-08 | Omitted job runs due to route drift                                                                   | target aggregate gate rejects extra execution                                   |
| UWA-09 | Coordinator outage skips everything                                                                   | native full fallback or gate failure                                            |
| UWA-10 | New workflow syntax silently broadens support                                                         | closed parser/profile version                                                   |
| UWA-11 | Registry gate name points at another job                                                              | exact static gate id/name association                                           |
| UWA-12 | Workflow A receives a target projection for workflow B                                                | OIDC workflow-ref path equality                                                 |
| UWA-13 | Disabled execution without reconciliation still reads provider target evidence                        | consumer-gated projection                                                       |
| UWA-14 | Copied gate logic drifts between target workflows                                                     | generated registry-bound gate validator                                         |
| UWA-15 | Two workflows named `CI` alias or reject each other                                                   | signal identity and reconciliation subject include exact workflow coordinates   |
| UWA-16 | Native fallback skips original jobs and substitutes a monolithic command                              | kind-specific gate truth table requires every native job to succeed             |
| UWA-17 | A selected job is skipped because one of its `needs` jobs was omitted                                 | registry-bound transitive closure plus exact-revision dependency comparison     |
| UWA-18 | A new unregistered prerequisite executes before selected work                                         | complete exact-revision `needs` equality and one registry-bound plan job        |
| UWA-19 | A registry-only dependency profile gains execution authority outside configured policy                | exact equality between registry and validation-catalog profile sets             |
| UWA-20 | Sharded plan failure has no registered FullCI job                                                     | exact non-null fallback identity, provider job proof, and gate dependency       |
| UWA-21 | Native workflow runs an unproved synthetic FullCI command                                             | native registry forbids a separate fallback job and gate input                  |
| UWA-22 | Discovery compares workflow bytes from one commit with registry bytes from another                    | one exact Git commit-tree read and registry hash projection                     |
| UWA-23 | A plan crash leaves `selected_jobs` empty and job-condition JSON parsing fails before native fallback | every membership expression parses `selected_jobs \|\| '[]'`                    |
| UWA-24 | An unregistered job or prerequisite executes outside selected authority                               | exact equality of the complete top-level job and `needs` graph                  |
| UWA-25 | Selecting `Lint` also activates registered job `lint`                                                 | ASCII-casefold uniqueness across every workflow job role                        |
| UWA-26 | The remote requester cannot resolve, so an in-workflow fallback never starts                          | a separately triggered local Native Full Check remains required during adoption |

## 13. Acceptance

- no generic validator contains a repository fixture workflow path;
- both execution kinds round-trip through source, schema, Python codec, signed
  payload, CommonJS validator, persistence, and reconciliation;
- provider inventory proves every workflow, execution job, intra-execution
  dependency, kind-specific fallback route, and gate job;
- target authority survives missing shard capacity evidence while selected
  execution remains unavailable;
- FullCI reconciliation uses the exact workflow aggregate gate and never a
  synthetic bootstrap signal;
- malformed kind combinations fail closed;
- a workflow from a second fixture path uses the same implementation;
- every discovery source has exactly one adoption assessment;
- unsupported workflows are reported `full_only` or `invalid`, never guessed;
- existing witness-shard bootstrap behavior remains green;
- a native-job-set fixture proves dependency closure, selected, full, stale,
  outage, cancellation, retry, missing gate, extra execution, and failure
  paths;
- the same generated gate validator proves both execution kinds without
  repository-specific behavior;
- no plan field is executable.
- selected-job conditions remain total when the plan job publishes no outputs.
- each adoption fixture retains a separately triggered local Native Full Check
  with no remote reusable-workflow dependency.

## 14. Non-Claims

This design does not prove target semantic adequacy, safe production omission,
provider deployment, required-check configuration, credential availability,
hosted-runner CPU use, or globally minimal CI.
