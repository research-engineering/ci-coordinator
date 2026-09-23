# Execution Orchestration Module Specification

Status: normative module specification

Date: 2026-07-30

## 1. Owned Invariant

Executable authority is never carried as plan-supplied code. A selected plan
contains only admitted nominal identities; a revision-bound target registry
maps each execution profile to one reviewed static job in one exact workflow;
binds the exact plan job, execution-kind-specific fallback identity, and
acyclic intra-execution `needs` graph; and the execution kind determines
whether the coordinator assigns witness shards or selects repository-native
jobs.

```text
ExecutableSelectedPlan(p) :=
  VerifiedCoverage(p)
  and TypedNonExecutablePayload(p)
  and RegistryAdmitsCatalog(p.targetRegistry, p.validationCatalog)
  and ExistsExactlyOne(CurrentWorkflowBinding(p))
  and ProviderInventoryAdmitsWorkflowSlice(p.targetRegistry)
  and (
    WitnessShards(p) and EveryShardSignalIsDerived(p)
    or
    NativeJobSet(p) and ExactDeclaredGateSignal(p)
  )
```

Failure to prove any conjunct selects FullCI. It cannot produce a reduced
execution.

Target identity is proved before kind-specific execution readiness:

```text
TrustedExecutionTarget :=
  verified plan
  + one exact registry workflow
  + selected profile closure
  + exact Git-object adapter snapshot
  + structurally admitted control plane
  + provider-proved static jobs and mandatory-job closure

TrustedExecutionProjection :=
  TrustedExecutionTarget
  + (
      native-job-set
      or complete witness-shard manifest and capacity projection
    )

FullCiReconciliation requires TrustedExecutionTarget
SelectedExecution requires TrustedExecutionProjection and ProductionAdmission
```

## 2. Public API

```text
ProviderSignal.derive(execution_profile_id, shard_id) -> ProviderSignal
ProviderSignal.declared_native(workflow_path, job_id, job_name) -> ProviderSignal
ProviderOccurrence.belongs_to(workflow_run_id, run_attempt) -> bool
parse_target_execution_registry(bytes) -> TargetExecutionRegistry | absent
TargetExecutionRegistry.admits(validation_catalog) -> bool
TargetExecutionRegistry.workflow(workflow_path) -> TargetWorkflowBinding | absent
ProviderWorkflowInventory.admits_static_job(workflow_path, job_id) -> bool
ProviderWorkflowInventory.admits_exact_job_topology(workflow_path, expected) -> bool
ProviderWorkflowInventory.admits_local_reusable_workflow_closure(...) -> bool
ProviderWorkflowInventory.admits_control_plane(...) -> bool
GitHubAdapterSnapshotLoader.load(repository, revision_sha) -> snapshot | absent
CapacityPlanningService.project(...) -> TrustedExecutionPlanningResult | absent
```

`SelectedExecution`, `SignedProfileExecution`,
`SignedNativeProfileExecution`, and `SignedExecutionShard` are owned by
`plan_issuance` because they are part of the signed wire contract. Trusted
kind-specific projection and shard construction are owned by
`runner_capacity`. Workflow inventory is owned by `repo_context` and loaded by
the GitHub adapter.

## 3. Authority Separation

```text
HotPolicyAuthority := obligation + witness + execution-profile identities
StaticTargetAuthority := workflow binding + static job + component-profile bindings
ProviderAuthority := exact commit/tree/blob adapter snapshot
ShardedRuntimeWork := signed shard and test identities
NativeRuntimeWork := signed profile and static job identities
```

These authorities are not interchangeable. In particular:

- policy cannot supply workflow YAML, shell, action references, runner labels,
  permissions, credentials, fixtures, or services;
- the target registry cannot prove that its workflow or job exists;
- provider YAML cannot prove semantic test coverage;
- provider workflow-list state cannot strengthen an immutable-revision proof;
- a signed shard cannot broaden its statically admitted execution profile;
- a native selection cannot supply a matrix, command, runner, permission,
  credential, service, environment, action, or reusable-workflow reference;
- two workflow slices cannot be combined into one selected execution.

## 4. Required Properties

1. The target registry is bounded, strict JSON with exact workflow and profile
   schemas and canonical identity order.
2. The target-registry profile set equals the configured validation-catalog
   profile set. Every profile has one exact target binding and exactly one
   workflow owner; neither authority source may add a profile absent from the
   other.
3. Every workflow binding names one top-level workflow invoked by the
   authenticated run, one fixed invocation-classifier job, one exact requester,
   one exact plan job, one non-empty exact acyclic execution-job graph, and one
   distinct aggregate gate job. The complete top-level workflow job set equals
   those roles. The classifier has no dependency, the requester depends exactly
   on the classifier, the plan depends exactly on the requester, and every
   execution job directly needs that plan job without a dependency outside the
   declared graph. Every role ID is unique under GitHub's case-insensitive
   expression comparison.
4. A witness-shard workflow names one distinct static fallback job whose exact
   dependency set is the plan job. A native workflow forbids a separate
   fallback job and executes the complete registered native graph in full mode.
5. The always-run gate's complete direct dependency set equals the plan job,
   every execution job, and the sharded fallback job when present.
6. Every selected execution uses exactly one workflow and one execution kind.
7. Every shard contains tests from exactly one execution profile; a native
   execution contains no shard matrix or test-manifest identity.
8. Every shard signal is derived from profile and shard identities. Every
   native gate signal is derived from the trusted workflow path, gate job id,
   and gate job name.
9. The target workflow path equals the top-level `workflow_ref` path of the
   authenticated run. A `job_workflow_ref` cannot substitute for this binding.
   This is a selected direct-run invariant: manual and same-repository reusable
   invocation skip the plan requester and retain native FullCI.
10. Provider inventory admits the aggregate gate only when one explicit static
   non-matrix top-level job binds both its exact job id and exact job name.
11. Reconciliation accepts an occurrence only from the signed workflow run and
   run attempt and rejects zero-or-many ambiguous matches conservatively.
12. Each workflow owns one stable required gate independent from selected shard
    or native-job count. Displayed gate names may repeat across workflows
    because signal identity includes exact workflow and job coordinates.
13. Every registry profile maps to a distinct static job in the authenticated
   workflow revision. Sharded profiles receive separate per-job matrices and
   their signed selected job set must already be closed over the registry-bound
   dependency graph. Native profile roots expand to that transitive dependency
   closure before target jobs evaluate their route.
14. Coordinator and OIDC responses are bounded while downloading, target files
   are bounded before allocation, and the complete plan-job output is rejected
   before publication when its UTF-16 size exceeds the safe provider budget.
15. A target test manifest may be empty only as a neutral transport value.
    Any selected `witness-shards` plan still requires exact test coverage for
    every selected witness; an all-native registry performs no manifest read.
16. Loss of a shard manifest cannot erase an independently proved target
    workflow and gate. It withholds selected execution while preserving exact
    FullCI reconciliation. A missing or stale runner snapshot retains exact
    coverage with conservative parallelism.
    On GitHub, a present shard manifest must be a bounded regular Git blob at
    the exact request head revision; symlink dereferencing is never capacity
    evidence.
17. Missing target authority produces neither selected execution nor a
    synthetic reconciliation signal.
18. Provider target projection runs only when production admission or durable
    reconciliation can consume it. With neither consumer configured, the
    service performs no target-inventory or capacity I/O.
19. The registry commits to every top-level workflow, the complete same-commit
    local reusable-workflow closure, and one generated plan-consumption,
    plan-validation, and gate-validation control bundle. Every member is a regular Git
    blob with the declared
    SHA-256 digest at the authenticated workflow SHA.
20. Local reusable-workflow calls use the documented same-commit GitHub.com
    `$/.github/workflows/{file}` or `./.github/workflows/{file}` form, both
    canonicalized to one repository-relative identity. The closure is acyclic,
    does not exceed ten connected workflow levels, and remains within the
    stricter product cap of 32 workflow files. External reusable workflows use
    full immutable Git object ids.
21. Every external action uses a full immutable Git object id. Docker actions,
    job containers, and services use immutable SHA-256 image digests.
22. One complete projection binds every applicable classifier, request, plan,
    and gate call, command, environment value, output, and dependency. These
    roles contain no job-level environment, defaults, container, services, or
    local action. Only the classifier declares its fixed `bash` step shell;
    other control steps declare no shell or working directory. Request, plan,
    gate, and execution-route conditions may depend only on `github` and
    `needs`.
23. Every selected projection contains each registry-declared mandatory job.
    Native execution may satisfy this through its registered dependency
    closure; witness-shard execution must select the job directly.
24. The exact adapter snapshot uses at most 39 cold Git reads and four
    concurrent blob reads for at most 33 files. It does not call the
    default-branch workflow-list endpoint. A failed blob read cancels and joins
    all request-owned sibling reads before the snapshot load returns.
25. The full-SHA coordinator-owned reusable workflow uses the exact plan URL as
    its OIDC audience, executes no target-controlled bytes, rejects redirects,
    bounds the request deadline and response, and emits only bounded canonical
    chunks or a fallback reason. The target-owned consumer has no OIDC or
    network authority and atomically reconstructs only a bounded JSON object.
    A failure after the reusable job starts preserves local FullCI. Provider
    failure before the external workflow graph resolves is a hard workflow
    failure and therefore requires an independently runnable native Full Check
    during adoption.

## 5. Failure Algebra

```text
InvalidRegistry                  => FullCI
RegistryCatalogMismatch          => FullCI
IncompleteProviderSnapshot       => FullCI
MissingExactRevisionWorkflow     => FullCI
NonRegularAdapterObject          => FullCI
AdapterDigestMismatch            => FullCI
AdapterClosureMismatch           => FullCI
InvalidControlPlane              => FullCI
InvalidInvocationClassifier      => FullCI
MutableActionOrContainer         => FullCI
MissingStaticJob                 => FullCI
UnregisteredWorkflowJob          => FullCI
PlanDependencyMismatch           => FullCI
StaticJobDependencyMismatch      => FullCI
JobExpressionIdentityCollision   => FullCI
InvalidExecutionDependencyGraph  => FullCI
MissingStaticGateName            => FullCI
WorkflowRefTargetMismatch        => FullCI
InvalidWorkflowSyntaxProjection  => FullCI
ForeignRunOccurrence             => ignore and continue bounded reconciliation
AmbiguousProviderOccurrence      => terminal reconciliation failure
UnsafeExecutablePayload          => reject signed execution
CrossWorkflowSelection           => FullCI
ExecutionKindMismatch            => FullCI
StaticJobSetMismatch             => FullCI
RequiredStaticJobMissing         => FullCI
MissingNativeGate                => FullCI
ShardManifestCoverageMissing     => FullCI
ShardManifestUnavailable         => FullCI through exact workflow gate
TargetAuthorityUnavailable       => FullCI without reconciliation registration
NoExecutionProofConsumer         => FullCI without provider projection
ProviderOutputBudgetExceeded     => FullCI
ResponseBodyLimitExceeded        => FullCI
TargetRequestTransportFailure    => local FullCI
RequesterGraphResolutionFailure  => hard provider failure before local fallback
WorkflowCancelled                => explicit gate failure
```

## 6. Implementation Mapping

```text
ci_coordinator/execution_orchestration/provider_signal.py
ci_coordinator/execution_orchestration/adapter_bundle.py
ci_coordinator/execution_orchestration/target_registry.py
ci_coordinator/execution_orchestration/target_registry_codec.py
ci_coordinator/plan_issuance/execution_contract.py
ci_coordinator/app/capacity_planning.py
ci_coordinator/runner_capacity/inputs.py
ci_coordinator/repo_context/workflow_inventory.py
ci_coordinator/repo_context/workflow_syntax.py
ci_coordinator/integrations/github/adapter_snapshot.py
ci_coordinator/integrations/github/capacity_context.py
ci_coordinator/integrations/github/reconciliation_observer.py
.github/workflows/trusted-plan-request.yml
ci_coordinator/target_artifacts/control_source/
ci_coordinator/target_artifacts/resources/ci-coordinator.cjs
scripts/target_control_bundle.py
```

There is no runtime command dispatcher, arbitrary shell matrix, or
plan-supplied reusable-workflow selector in this contract.

## 7. Acceptance Falsifiers

1. A plan-provided string is evaluated as shell or used as an action target.
2. A registry profile relabels one runner, permission, credential, fixture,
   service, or capacity identity.
3. A selected shard mixes tests from two execution profiles.
4. A native profile contains a shard, matrix, manifest identity, or executable
   field.
5. One selected execution contains profiles from two workflows or two kinds.
6. A workflow other than the exact top-level authenticated workflow admits
   selected execution.
7. A registry execution or gate job absent from exact-revision workflow YAML
   admits execution.
8. A duplicate or aliased YAML key changes the projected static job set.
9. Two provider jobs satisfy one expected signal.
10. A provider occurrence from another run or attempt satisfies reconciliation.
11. A response larger than its admitted limit is materialized before fallback.
12. A locally valid selected plan exceeds the provider job-output budget.
13. A multi-profile plan shares one dynamic-authority job or leaves a selected
    profile without a successful static job.
14. An all-native workflow is forced to invent a test or read runner capacity.
15. A missing shard manifest removes the already verified aggregate fallback
    gate.
16. A missing target registry causes a synthetic bootstrap signal to be
    registered.
17. A registry gate id is accepted with another job's provider name.
18. A target workflow differs from the top-level OIDC `workflow_ref` path.
19. A non-enforcing service without reconciliation performs provider target or
    capacity I/O whose result cannot affect issuance.
20. A selected native job is emitted without one of its transitive static
    execution dependencies.
21. A registry dependency differs from exact-revision workflow `needs`, or an
    undeclared prerequisite enters selected execution.
22. A registry-only dependency profile absent from the validation catalog gains
    selected-execution authority.
23. A sharded workflow has no exact static fallback job, gives it dependencies
    beyond the plan job, or omits it from the aggregate gate.
24. A native workflow declares or observes a synthetic separate FullCI result.
25. The aggregate gate is conditional or omits any registered terminal route.
26. The workflow contains an unregistered job or the plan has a prerequisite.
27. Two registered job roles alias under GitHub's case-insensitive expression
    comparison.
28. A symlink, submodule, special object, or digest-mismatched blob enters the
    adapter snapshot.
29. A local reusable-workflow call is missing, cyclic, too deep, or outside the
    registry-bound closure.
30. A control job changes a generated output, command, environment value,
    action input, condition, shell, working directory, local action, default,
    container, service, runner, permission, timeout, step order, or identifier
    without invalidating target authority.
31. An external action, Docker action, job container, service image, or
    external reusable workflow uses a mutable reference.
32. A selected projection omits a mandatory job.
33. A maximal adapter exceeds 39 cold Git reads, four concurrent blob reads,
    33 files, or 32 workflow files.
34. An adapter read fails while another request-owned adapter read remains
    active after the load returns.
35. An OIDC or coordinator request failure suppresses both selected execution
    and FullCI.
36. Workflow-level environment or defaults, or job-level or step-level
    `continue-on-error`, in any reachable adapter workflow preserves target
    authority.

## 8. Non-Claims

Static YAML projection proves only declared top-level triggers, static job ids,
their static `needs`, explicit static job names, immutable dependency
identities, the local reusable-workflow graph, protected-job failure semantics,
and the bounded normalized plan/gate shape. It proves the declared control-job
permissions and runner identity but not their provider availability. It does
not execute expressions, prove local action or target-entrypoint behavior,
close repository-variable availability, establish semantic test adequacy,
prove default-branch activation, or prove that provider state remains unchanged
after the immutable snapshot. Universal support means total
`in_place_job_set | reusable_workflow_set | witness_shards | full_only |
invalid` classification. It does not mean every arbitrary workflow is safely
optimizable.
