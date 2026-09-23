# Validation And Execution Identity Module Specification

Status: normative module specification

Date: 2026-07-30

## 1. Decision

Correctness, executable work, execution environment, execution form, and
provider observation are distinct concepts:

```text
ValidationObligation -> one or more ExecutableWitness values
ExecutableWitness -> exactly one ExecutionProfile
ExecutionProfile -> exactly one workflow-scoped static target job

witness-shards:
  ExecutionProfile + selected tests -> content-addressed Shards
  ExecutionProfile + Shard -> derived ProviderSignal

native-job-set:
  ExecutionProfile -> selected static job identity
  WorkflowBinding -> declared aggregate ProviderSignal

ProviderSignal + provider run coordinates -> ProviderOccurrence
```

The repository policy configures obligations, witnesses, and profiles. A target
registry configures workflows, static jobs, execution kinds, and component
profile bindings, including the minimum acyclic intra-execution dependency
graph and exact plan job required for runnable native selection. A target test
manifest configures tests only for
`witness-shards`. Provider signals are constructed from trusted nominal
coordinates and cannot be supplied as free-form input.

## 2. Formal Model

```text
Obligation := {
  obligationId,
  responsibility,
  requiredWitnessIds,
  defaultDepth,
  fullDepth,
  omitAllowed
}

Witness := {
  witnessId,
  executionProfileId,
  supportedDepths
}

ExecutionProfile := {
  profileId,
  runnerProfileId,
  permissionProfileId,
  credentialProfileId,
  fixtureProfileId,
  serviceProfileIds,
  capacityClassId,
  shardingPolicy
}

ShardingPolicy := {
  maxShards,
  maxParallel,
  maxItemsPerShard,
  setupSecondsPerShard,
  cpuWeight,
  wallWeight,
  operatorWeight
}

ValidationCatalog := {
  canonical obligations,
  canonical witnesses,
  canonical executionProfiles
}

Test := {testId, witnessId, expectedSeconds}

WorkflowBinding := {
  workflowPath,
  executionKind,
  executionJobs: [{jobId, needs}],
  planJobId,
  fallbackJobId,
  gateJobId,
  gateSignalName,
  requiredJobIds
}

ShardId := "ci_shard_" + short(H("ci-shard/v1", verifiedPlanId, profileId, canonicalWorkItems))
DerivedShardSignal := H("ci-provider-signal/v1", profileId, shardId)
DeclaredNativeSignal := H(
  "declared-native-provider-signal/v1",
  workflowPath,
  gateJobId,
  gateSignalName
)
```

`H` is canonical SHA-256 identity construction. Identifiers are nominal and
cannot be substituted merely because their string bytes happen to match.

## 3. Laws

For selected obligations `O`, selected witnesses `W`, profiles `P`, work items
`T`, and shards `S`:

```text
forall o in O: requiredWitnesses(o) subset W
forall w in W: exists exactly one p in P: profile(w) = p.id
forall t in T: witness(t) in W
forall s in S: forall t in s.items: profile(witness(t)) = s.profileId
disjointUnion(items(s) for s in S) = T
forall s in S: signal(s) = Signal(s.profileId, s.shardId)
forall occurrence: occurrence.runId = subject.runId
                   and occurrence.runAttempt = subject.runAttempt

forall selected execution:
  exists exactly one workflow binding
  exists exactly one execution kind

NativeJobSet => no shards and no testManifestId
WitnessShards => exactly one non-null fallbackJobId
NativeJobSet => fallbackJobId = null
WitnessShards => exact non-empty selected-witness test coverage

TargetAuthority :=
  exact registry
  and exact Git commit/tree/blob adapter snapshot
  and complete registry-bound workflow closure
  and structurally admitted classifier, request, plan, and gate control plane
  and exact current-workflow plan job
  and exact complete current-workflow static job set
  and exact complete current-workflow direct dependencies
  and case-distinct job identities under GitHub expression comparison
  and one workflow-owned aggregate gate
  and mandatory-job closure

SelectedExecutionReady :=
  TargetAuthority
  and (
    NativeJobSet
    or exact shard manifest and capacity projection
  )

FullCiReconciliation => TargetAuthority
SelectedExecution => SelectedExecutionReady and ProductionAdmission
not TargetAuthority => no reconciliation signal is invented
```

Depth for a witness is the maximum requested depth among selected obligations
that require it. Admission rejects a policy when a required witness does not
support both configured obligation depths.

### 3.1 Repository-policy admission

`config_control` admits the first public `ci-repository-policy/v1` directly in
this algebra. `dynamicCi` contains `obligations`, `witnesses`, and
`executionProfiles`; it has no check aggregate, workflow identity, credential
allowlist, fixture allowlist, or compatibility alias.

Source admission is valid if and only if:

```text
SourceCatalogAdmitted(C, R) :=
  EveryNominalIdentifierValid(C, R)
  and EverySetValuedFieldUnique(C, R)
  and EveryResponsibilityPathValid(C)
  and Unique(C.obligations, obligationId)
  and Unique(C.witnesses, witnessId)
  and Unique(C.executionProfiles, profileId)
  and every obligation.requiredWitnessId resolves
  and every witness.executionProfileId resolves
  and every witness and profile is reachable from an obligation
  and every obligation.defaultDepth <= obligation.fullDepth
  and every obligation depth is supported by every required witness
  and every obligation risk class is a member of R
  and every member of R is used by an obligation
  and every omittable obligation has a non-empty responsibility surface
  and count(profile.serviceProfileIds) <= 16 for every profile
  and maxParallel <= maxShards for every profile
  and at least one sharding objective weight is positive

SourceCatalogAdmitted(C, R)
  => CompiledCatalog(C) = CanonicalizeByECMAScriptUTF16(C)
```

Responsibility paths and risk classes may each be empty. An omittable
obligation may not leave both empty; a mandatory obligation may do so to state
an unconditional validation requirement.

The compiler maps every admitted source catalog to exactly one
`CompiledCatalog`, ordering entity identifiers and set-valued fields
canonically. It computes
`configuredValidationCatalogHash` over exactly the closed `ValidationCatalog`,
and includes that digest plus every non-catalog planning fact in `policyHash`.
The catalog digest is a configured-policy identity only. It is not evidence of
provider inventory completeness.

Every verified planning-evidence payload embeds the complete configured
`ValidationCatalog`. Config producer feasibility therefore measures the exact
canonical catalog as a necessary byte lower bound: catalog overflow proves
that the enclosing audit payload must overflow. Passing this lower bound does
not prove that the complete payload fits, so runtime admission remains
mandatory.

`project_dynamic_ci_planning` exposes the shared immutable
`ValidationCatalog` from `ci_coordinator.validation_contract`. It recomputes
the catalog digest and rejects any mismatch before returning planner facts.

Any missing reference, incompatible profile, incomplete manifest, duplicate
signal, or provider coordinate mismatch forces FullCI or a terminal
reconciliation failure. It never authorizes omission.

## 4. Static Execution Boundary

The signed plan may contain only nominal obligation, witness, profile, shard,
signal, and work-item identities. It must not contain:

```text
shell command
executable path
argv
arbitrary environment name or value
dynamic action or reusable-workflow reference
GitHub expression
```

Each target repository owns a fixed executable at `.ci-coordinator/run` with a
closed `witnessId -> handler` map. The generated bootstrap invokes only:

```text
.ci-coordinator/run full-ci
.ci-coordinator/run witness
```

The witness invocation reads one bounded validated shard document from a fixed
environment variable or file. It never evaluates that document as shell.

Each target workflow contains one static job per execution profile and one
distinct aggregate gate. Runner labels, permissions, environment, services,
credentials, actions, reusable-workflow references, and commands remain static
target properties. For `witness-shards`, matrix values can select work only
within the owning profile. Its target-side admission requires canonical shard
identities, no more than the signed `maxShards`, and no more than the signed
`maxItemsPerShard` in each shard. For `native-job-set`, the signed payload can
select only static profile/job identities and carries no matrix. The artifact
renderer owns registry, optional shard manifest, dependency graph, and verifier
bytes; it does not claim to generate arbitrary GitHub workflow authority from
nominal profile identifiers.

The policy stores nominal component profile identities rather than raw runner,
permission, secret, fixture, or service values. An exact-revision target
registry binds those component identities to one generated static job. Policy
activation or selected execution is inadmissible when that binding is missing,
ambiguous, stale, or different from the provider-observed workflow:

```text
HotPolicyProfile(profileId)
and ExactStaticJobBinding(profileId, componentProfileIds)
and BindingMatchesProviderRevision
=> ProfileExecutable
```

The complete registry profile set must equal the complete configured validation
catalog profile set. Subset admission is insufficient: it would let a
registry-only dependency profile acquire execution authority without
configuration-policy ownership.

This separation permits policy hot reload without permitting hot reload of
execution authority. Changing runner labels, permissions, named credentials,
services, fixtures, generated control programs, action identities, container
identities, or reusable-workflow identities requires a reviewed target-workflow
or registry change and a fresh exact adapter snapshot.

The target validator receives the complete static selected-job set from the
reviewed current workflow and requires an exact bijection with that registry
slice. A selected execution may use any non-empty root subset of those
profiles, but may not cross workflow or execution-kind boundaries. It emits
matrices and parallelism only for `witness-shards`; for `native-job-set` it
emits the canonical transitive dependency closure of that root set. The
generated registry-bound gate validator
compares that set with every static job result, so missing, extra, failed,
cancelled, selected skips, and unexpected executions cannot be admitted.
Every registry-declared `requiredJobId` must also be present. A native required
job may enter through the registered dependency closure; a sharded required job
must be selected directly.

Provider transport is part of validity, not an after-the-fact delivery detail:

```text
TransportableSelectedExecution(E) :=
  BoundedDownload(E)
  and BoundedStableRead(E)
  and Utf16Bytes(AllPlanJobOutputs(E)) <= SAFE_JOB_OUTPUT_BUDGET

SelectedAdmitted(E) => TransportableSelectedExecution(E)
not TransportableSelectedExecution(E) => FullCI
```

The safe budget is strictly below GitHub's documented per-job output limit and
is measured using the provider's UTF-16 approximation. The validator applies
the bound before writing to `GITHUB_OUTPUT`. The coordinator should avoid
issuing an oversized selected execution, but the target validator remains the
final independent fail-closed guard.

Each workflow binding owns one stable required gate. For `native-job-set`, that
gate is the declared provider signal. For `witness-shards`, shard occurrences
remain derived signals while the gate remains the repository-facing aggregate
check.

## 5. Repository Inventory

Configured requirements and provider-discovered workflows are separate facts:

```text
ConfiguredValidationCatalog != ProviderWorkflowInventory
```

The provider snapshot is complete for target-execution admission only when this
algorithm succeeds:

1. resolve the authenticated `workflow_sha` through the Git commit API and
   require an exact commit identity;
2. traverse the non-recursive root, `.ci-coordinator`, `.github`, and
   `.github/workflows` trees with exact tree identities and bounded entry
   counts;
3. load the target registry as a regular `100644` or `100755` blob and admit its
   strict bounded canonical contract;
4. load every registry-bound adapter member as a regular blob at the same
   commit, recompute its Git object identity and SHA-256 digest, and reject any
   missing, symlinked, submodule, special, excessive, or mismatched member;
5. admit no more than 32 workflow files and 33 total adapter files, use no more
   than 39 cold Git reads, and perform no more than four blob reads
   concurrently;
6. admit bounded UTF-8 YAML 1.2 without aliases, custom tags, duplicate or
   non-string mapping keys, excessive depth, node count, scalar size, or file
   size;
7. require the exact acyclic same-commit local reusable-workflow closure for
   the documented GitHub.com `$/.github/workflows/{file}` and
   `./.github/workflows/{file}` syntax, canonicalized to one path identity, at
   most ten connected levels and within the stricter 32-file product bound;
8. require external reusable workflows and actions to use full immutable Git
   object ids and Docker actions, job containers, and service images to use
   immutable SHA-256 digests;
9. project every top-level static job id, complete static `needs`, explicit
   static non-matrix job name, generated control job's normalized
   safety-relevant shape, protected-job failure semantics, and route-condition
   context;
10. require every target-registry `(workflowPath, jobId)` pair and complete
    direct dependency set in that exact projection;
11. require the fixed credential-free invocation classifier, plan requester,
    plan consumer, and gate to match one normalized generated control-plane
    tuple; require exact non-empty caller/defining workflow-ref equality and the
    exact declared dynamic trigger subset before requester activation; bind plan
    and gate execution authority to `job.workflow_ref` and `job.workflow_sha`;
    reject workflow-level environment/defaults and any job-level or step-level
    `continue-on-error` throughout the reachable workflow closure; and require
    every execution-route condition to use only `github` and `needs`;
12. for witness shards, require the fallback job to exist with exactly the plan
    dependency; for native jobs, require no separate fallback identity;
13. require every aggregate gate
    `(workflowPath, gateJobId, gateSignalName)` to match one exact static job and
    cover every terminal route; and
14. for selected direct execution, require the target workflow path to equal
    the top-level authenticated `workflow_ref` path; manual and
    same-repository reusable invocation remain native FullCI routes.

```text
ExactGitAdapterSnapshot
and ExactWorkflowClosure
and ExactControlPlane
and ExactDirectInvocationEvidence
and RegistryJobsEqualStaticJobs
and RegistryDependenciesEqualCompleteStaticNeeds
and ExecutionKindFallbackContract
and ExactStaticGateIdName
and AlwaysRunGateCoversEveryTerminalRoute
and TargetPathEqualsWorkflowRefPath
=> SelectedExecutionAuthorityObserved
```

The workflow-list API is intentionally absent from this path: it has no
revision selector, is unnecessary for an already authenticated invocation, and
would add default-branch consistency and rate-limit obligations to an
immutable-revision proof. Default-branch existence remains required for
`workflow_dispatch` and provider configuration, but it is not inferred by this
snapshot.

Any transport, identity, object-mode, content, syntax, bound, closure, control,
or mapping failure produces absent target authority and therefore FullCI.

The provider-independent consumer laboratory applies steps 6 through 13 to
the exact sealed workflow bytes before it executes any scenario. It replaces
default-branch activation with its explicitly non-provider execution-authority
coordinate, but reuses the same workflow parser, reusable-closure, static-gate,
exact-topology, and generated-control-plane predicates. Therefore:

```text
HashBound(WorkflowBytes)
and RegistryClaims(WorkflowSemantics)
does not imply LabTargetAuthority

LabTargetAuthority
=> WorkflowBytesSatisfy(RegistryClaims, ExactLabEpoch)
```

A coherently rehashed workflow whose actual requester ref, gate, job topology,
or generated control-plane projection differs from the registry is rejected.
The laboratory still makes no default-branch, provider, or target-job execution
claim.

Workflow discovery may propose obligations, witnesses, profiles, and target
runner mappings. It cannot activate policy or assert runtime behavior from
static YAML alone.

Static extraction does not execute GitHub expressions or reusable workflows,
prove local action or target-entrypoint behavior, infer repository-variable
availability, prove permissions, runner availability, or semantic job
behavior. Trigger presence is a structural capability fact only. Provider
mutation after the immutable revision snapshot is outside this proof and
requires a new snapshot.

## 6. Target Artifacts

The dependency graph remains `dependency-graph/v1`. Its source revision comes
from exact provider retrieval, not self-declared file content.

The shard test manifest is `dynamic-ci-test-manifest/v1`:

```json
{
  "schemaVersion": "dynamic-ci-test-manifest/v1",
  "generator": {"id": "target-tests", "version": "1"},
  "tests": [
    {"testId": "backend/test-a", "witnessId": "python-test", "expectedSeconds": 10}
  ]
}
```

An empty `tests` array is the canonical neutral artifact for an all-native
registry. It does not authorize an empty sharded plan. `witness-shards`
selection still fails closed unless the manifest covers every selected witness
exactly through one or more tests.

`ci-coordinator-target-artifacts` exposes explicit `render` and `check`
subcommands. Both admit the supported Python runtime and one bounded source,
then render three data artifacts and load one bounded, versioned control bundle
from the installed package. Each data artifact passes its normative packaged
schema, exact owner decoder, and consumer byte bound. The control bundle is a
non-empty, NUL-free, newline-terminated package resource capped at 1 MiB.
`render` stages all changed outputs before publication and, after any caught
replacement failure, restores every prior path before reporting failure.
Each rename is atomic; a process or host crash during the multi-file publication
window is not claimed to be bundle-atomic and must be resolved by `check` plus a
new render or by publishing the generated set in one repository commit. `check`
performs no write and succeeds only when all four existing files are byte-exact
canonical outputs.

The decomposed `target_artifacts/control_source` modules are the control source
authority. Exact-version esbuild produces the visible package resource, and the
hidden target-repository file is its deterministic distribution mirror. This
keeps trust-edge ownership eligible for repository static analysis while
preserving the target repository's `.ci-coordinator` contract.

The compiler proves deterministic shape, canonicalization, consumer admission,
and drift only. Semantic completeness of tests and graph edges requires
repository-owner evidence and conservative unknown handling.

## 7. Ownership

| Fact                                             | Owner                                                              |
|--------------------------------------------------|--------------------------------------------------------------------|
| obligation, witness, and profile admission       | `config_control`                                                   |
| configured validation catalog                    | `config_control` typed planning projection                         |
| exact adapter Git-object snapshot                | `integrations/github.adapter_snapshot` and `repo_context` contract |
| obligation selection and omission proof          | `planning_core`                                                    |
| witness closure and monotonicity                 | `verification_core`                                                |
| optional shard manifest and profile partitioning | `runner_capacity`                                                  |
| typed signed execution payload                   | `plan_issuance`                                                    |
| static job and fixed target-entrypoint contract  | `execution_orchestration`                                          |
| same-run signal observation                      | `reconciliation`                                                   |

## 8. Acceptance Falsifiers

1. Two tests with different profiles enter one shard.
2. A selected obligation lacks one required witness.
3. A signed payload contains an executable command or path.
4. A target variable supplies a FullCI or selected shell command.
5. A configured workflow subset is labelled provider-complete.
6. An observation from another run or attempt satisfies a signal.
7. Two provider jobs satisfy one expected signal.
8. Manifest generation over identical inputs changes bytes.
9. A schema-valid source renders an artifact rejected by its runtime consumer.
10. `check` creates or modifies a file or directory.
11. A native job set fabricates a test, shard, matrix, or runner-capacity read.
12. One selected execution crosses a workflow or execution-kind boundary.
13. The gate admits a selected skip, an extra execution, a failed or cancelled
    job, concurrent FullCI, stale selected jobs during fallback, or failed
    fallback.
14. A native selected root omits a transitive registered dependency.
15. Provider workflow `needs` drifts from the registry, or gains an undeclared
    prerequisite, without invalidating target authority.
16. A registry profile absent from the validation catalog gains execution
    authority as a dependency of another profile.
17. A symlink or other non-regular Git object enters the adapter snapshot.
18. A changed local reusable workflow, generated plan consumer, plan validator,
    gate validator, immutable external requester ref, action identity, or
    container identity preserves the prior target-registry hash.
19. A control job changes its output, command, environment value, action input,
    condition, shell, working directory, local action, defaults, container,
    services, runner, permissions, timeout, step order, or identifier without
    forcing FullCI.
20. A selected plan omits a registry-declared mandatory job.
21. A maximal adapter exceeds its file, read, concurrency, reusable-depth, or
    closure bound.
22. A request failure after the external reusable job starts, or a target-side
    plan-transport failure, prevents the static FullCI route.
23. Workflow-level environment or defaults, or job-level or step-level
    `continue-on-error`, in any reachable adapter workflow preserves selected
    authority.
24. The consumer laboratory passes a workflow after every byte digest is
    coherently updated while its actual requester ref, gate, job topology, or
    generated control-plane semantics differ from the sealed registry claim.

Any successful falsifier rejects the implementation.

## 9. Compatibility Decision

No version of this backend, policy, bootstrap, or database has been deployed.
There is no external consumer or retained production state. Therefore the first
public `v1` contracts are defined directly by this specification; dual-read,
aliases, migration adapters, and historical field names would add complexity
without preserving any real compatibility obligation.
