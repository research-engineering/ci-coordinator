# Revision-Bound Adapter Admission

Status: trusted plan-request boundary implementation in progress

Date: 2026-07-30

Owner requirement: `REQ-CI-RUNTIME-027`

## 1. Decision

Selected execution may use a repository-scoped workflow-path identity across
dynamic GitHub refs only when two independent authorities are proved:

1. the authenticated immutable caller revision contains an exact,
   production-admitted, content-addressed target adapter; and
2. OIDC was minted only inside the registry-bound, immutable platform plan
   requester, whose source is not controlled by the caller revision.

The target execution registry is the single transitive commitment:

```text
targetRegistryHash
  -> adapter file paths and SHA-256 digests
  -> top-level workflow and local reusable-workflow closure
  -> immutable platform plan-request workflow identity
  -> generated plan consumption, plan validation, and gate validation programs
  -> mandatory static execution jobs
```

Exact workflow-ref allowlists remain supported. Workflow-path admission is an
additional bounded identity mode, not wildcard ref matching. Caller identity
and called-workflow identity are conjunctive namespaces: matching either one
alone cannot authorize selected execution.

The service reads the adapter through the Git commit, tree, and blob APIs. It
does not list repository workflows or depend on the coordinator source
repository during target CI.

The optional witness-shard test manifest uses the same Git Data reader at the
exact request head revision. It is not part of adapter identity, but it must be
a regular blob because its path participates in diff-policy invalidation.

## 2. Problem

For a pull-request run, GitHub identifies the workflow with a dynamic ref such
as:

```text
owner/repository/.github/workflows/full-check.yml@refs/pull/812/merge
```

An exact-ref allowlist therefore cannot admit an unbounded sequence of pull
requests. A wildcard ref allowlist would admit changing adapter code without
proving which routing and validation bytes executed.

The current registry proves job topology, but job topology alone does not prove
route conditions, reusable-workflow targets, or validator behavior:

```text
SameJobTopology(W1, W2) -/-> SameRoutingSemantics(W1, W2)
```

Selected omission consequently requires a second identity dimension: exact
adapter bytes at the authenticated workflow revision.

## 3. Formal Model

Let:

- `R` be the immutable repository id and canonical owner/name;
- `P` be the top-level workflow path;
- `S` be the immutable Git object id in the verified OIDC `workflow_sha`;
- `F` be the canonical adapter-file set;
- `B(f, S)` be provider bytes for file `f` at revision `S`;
- `M(f, S)` be the Git tree mode for file `f` at revision `S`;
- `D(f)` be the registry-declared lowercase SHA-256 digest for `f`;
- `T` be the admitted target execution registry;
- `W(T, S)` be the reflexive-transitive set containing every registry
  top-level workflow and every same-revision local reusable workflow reachable
  from those roots;
- `H(T)` be its canonical registry hash; and
- `A` be a valid production-admission grant.
- `Q` be the exact full-SHA reusable-workflow reference bound by the target
  registry;
- `J` and `JS` be the verified OIDC `job_workflow_ref` and
  `job_workflow_sha`; and
- `RequestBytes` be the bytes executed by the privileged plan requester.

```text
PathIdentityAdmitted(R, P, S) :=
  VerifiedActionsOidc
  and ExactRepositoryIdentity(R)
  and ExactRequestRefRunAttemptAndEvent
  and WorkflowPathClaim = R.ownerName + "/" + P
  and ImmutableGitRevision(S)

PlanRequesterAdmitted(Q, J, JS) :=
  ExactExternalReusableWorkflowRef(Q)
  and J = Q
  and JS = RevisionSuffix(Q)
  and ImmutableGitRevision(JS)
  and RequestBytes = GitBlob(Q)
  and NoTargetCheckoutBeforePlanRequest
  and OidcAuthorityExistsOnlyInRequestBytes

AdapterBytesMatch(F, S) :=
  forall f in F:
    ProviderReadAtExactRevision(f, S)
    and M(f, S) in {"100644", "100755"}
    and SHA256(B(f, S)) = D(f)

WorkflowClosure(T, F, S) :=
  W(T, S)
    = ReflexiveTransitiveLocalWorkflowClosure(RegistryWorkflowPaths(T), S)
  and W(T, S) = WorkflowFiles(F)

ControlFileClosure(F) :=
  ControlFiles(F) = FixedTargetControlFileSet

ControlPlaneAdmitted(T, W, S) :=
  ExactNormalizedRequestPlanAndGateJobProjection(T, S)
  and NoWorkflowEnvironmentOrDefaults(W)
  and NoJobOrStepContinueOnError(W)
  and ImmutableExternalActionsAndContainerImages(T, S)
  and RouteConditionsUseOnlyGithubAndNeeds(T, S)

ProviderBudgetAdmitted(T) :=
  WorkflowFileCount(T) <= 32
  and AdapterFileCount(T) <= 38
  and ColdGitReadCount(T) <= 44
  and ConcurrentBlobReads(T) <= 4

RequiredJobsAdmitted(T, SelectedJobs) :=
  forall j in T.currentWorkflow.requiredJobIds:
    j in (
      NativeDependencyClosure(SelectedJobs)
      if T.currentWorkflow.executionKind = "native-job-set"
      else SelectedJobs
    )

CapacityManifestAdmitted(Head, ManifestPath) :=
  GitCommitResolvedExactly(Head)
  and RegularBlobAt(Head, ManifestPath)
  and BoundedManifestBytes

RevisionBoundAdapter(T, F, S) :=
  CanonicalBoundedFileSet(F)
  and AdapterBytesMatch(F, S)
  and WorkflowClosure(T, F, S)
  and ControlFileClosure(F)
  and ControlPlaneAdmitted(T, W(T, S), S)
  and ProviderBudgetAdmitted(T)

SelectedExecutionAdmitted(R, P, S, T, A, SelectedJobs) :=
  PathIdentityAdmitted(R, P, S)
  and PlanRequesterAdmitted(
        T.currentWorkflow.planRequestWorkflowRef,
        J,
        JS)
  and RegistryLoadedAt(T, S)
  and RevisionBoundAdapter(T, T.adapterFiles, S)
  and P in RegistryWorkflowPaths(T)
  and RequiredJobsAdmitted(T, SelectedJobs)
  and A.binds(R, H(T), WorkflowPathIdentity(R, P))
  and ExistingPolicyPlanReconciliationPredicates
```

Exact-ref admission substitutes `ExactWorkflowRefAllowed` for the path
identity clause but does not remove either revision-bound adapter proof or
trusted plan-request proof.

## 4. Safety Argument

Assume SHA-256 collision resistance and canonical registry hashing.

For any admitted adapter file `f`, change its bytes from `b1` to `b2`, where
`b1 != b2`.

1. If the registry is unchanged, `D(f) = SHA256(b1)`.
2. Provider verification observes `SHA256(b2)`.
3. Except for a cryptographic collision,
   `SHA256(b2) != D(f)`.
4. Therefore `RevisionBoundAdapter` is false and selected execution is
   unavailable.

If an actor also changes `D(f)`, canonical registry bytes change:

```text
D1 != D2 -> T1 != T2 -> H(T1) != H(T2)
```

The existing production grant binds `H(T1)`, so it cannot authorize `H(T2)`.
Thus every admitted route or validator change requires a new owner-reviewed
production-admission subject.

Dynamic Git refs are therefore safe to abstract away only after exact
repository, workflow path, immutable revision, adapter bytes, and production
authority are all bound:

```text
PathIdentity alone -/-> SelectedExecution
PathIdentity and RevisionBoundAdapter and ProductionAdmission
  -> SelectedExecutionMayProceed
```

The immutable platform requester converts absent configuration, unsupported
events, unavailable OIDC, transport failure, non-success HTTP, oversized
responses, and malformed provider data to a bounded reason with no plan bytes.
The unprivileged target consumer converts that outcome to a local FullCI
document. The signed plan validator remains an independent boundary: neither a
successful HTTP response nor a structurally valid unsigned document grants
selected execution.

The plan URL is caller-supplied configuration, but it is also the exact OIDC
audience. Let `C` be the configured coordinator URL and `U` the URL supplied by
the caller:

```text
U = C  -> token is sent only to C by immutable requester bytes
U != C -> coordinator rejects because aud = U != C
```

The target-controlled caller never receives the token. URL substitution can
therefore cause only fallback or disclosure of claims already available to the
caller; it cannot mint a coordinator-accepted token for another endpoint.

## 5. Adapter Boundary

The adapter bundle contains exactly:

1. every target-registry top-level workflow;
2. the complete same-revision local reusable-workflow closure reachable through
   static job-level `uses: $/.github/workflows/...` or
   `uses: ./.github/workflows/...`; and
3. these six fixed generated control files:

```text
.ci-coordinator/consume-plan.cjs
.ci-coordinator/validate-plan-core.cjs
.ci-coordinator/validate-plan-envelope.cjs
.ci-coordinator/validate-plan-execution.cjs
.ci-coordinator/validate-plan.cjs
.ci-coordinator/validate-gate.cjs
```

An external reusable-workflow call is admitted only as a static
`owner/repository/.github/workflows/file@revision` target whose revision is a
full immutable Git object id. Its exact identity is already committed by the
containing workflow digest; mutable branches and tags are rejected. GitHub.com
supports at most ten connected workflow levels and fifty unique called
workflows. The product applies the stricter bound of 32 workflow files per
adapter.

The one registry-declared plan-request call is additionally projected into the
registry as `planRequestJobId` plus `planRequestWorkflowRef`. The request job
has no target checkout, no target step, no secret input, and only `id-token:
write`; the dependent plan job has `contents: read`, no OIDC permission, and
runs on every non-cancelled request-job result. Exact topology proves
`needs(planJobId) = {planRequestJobId}`.

For same-repository calls on GitHub.com, both currently documented forms
`$/.github/workflows/{file}` and `./.github/workflows/{file}` resolve at the
caller commit. Admission canonicalizes both to the same repository-relative
path. The dollar-root form is not a GitHub Enterprise Server compatibility
claim; the current provider transport is explicitly GitHub.com-only.

The top-level adapter workflows are also admitted structurally:

- external actions use full immutable Git object ids;
- same-repository actions use a canonical static `$/path` or `./path`;
- Docker actions, job containers, and service containers use SHA-256 digests;
- the plan and gate jobs match the normalized generated safety projection,
  including runner, timeout, permissions, outputs, ordered steps, action
  coordinates, action inputs, step identifiers, commands, conditions, and
  complete environment values;
- cosmetic job and step names are excluded from that projection, while complete
  static `needs` and the gate name are proved independently;
- workflow-level environment and defaults are absent in every workflow in the
  reachable closure;
- no job or step in that closure declares `continue-on-error`; and
- execution-route conditions reference only `github` and `needs`.

It intentionally excludes application source, test implementation, third-party
action source, local action source, repository variables, policy epochs, the
execution registry itself, the target-owned execution entrypoint, and the plan
trust root.

The exclusions are necessary:

- application and test source are execution inputs, not routing authority;
- action and container identities are committed by workflow bytes, while their
  external source and behavior remain separate supply-chain facts;
- target-owned execution code and local actions are diff-policy inputs and
  repository-governance responsibilities, not plan-supplied authority;
- `$` local actions resolve from the running workflow repository and commit,
  while `./` actions resolve from the checked-out workspace; admitting either
  static path does not claim behavioral equivalence or prove the action source;
- policy and catalog hashes are already first-class plan and production
  subjects;
- including the registry in its own digest set would create a cyclic identity;
- the trust root has an independent key-rotation lifecycle and validates the
  signed plan rather than defining adapter behavior.

## 6. Provider Acquisition And Budget

The exact snapshot algorithm is:

```text
workflowSha
  -> commit
  -> root tree
  -> .ci-coordinator tree
  -> registry regular blob
  -> .github/workflows tree
  -> every registry-bound regular blob
```

The current GitHub.com Git Data profile admits exactly 40 lowercase hexadecimal
characters at this provider boundary. The broader internal 40-to-64-character
Git object representation does not grant a provider read; support for another
object format requires a versioned profile and matching commit, tree, blob,
OIDC, and target-consumer admission.

It never uses the Contents API for adapter authority because that API may
dereference symlinks. It never uses the workflow-list API on the plan path
because that endpoint has no revision parameter and would add non-atomic
default-branch state to an immutable-revision proof.

For `witness-shards`, the optional test manifest is read independently at the
exact request `head_sha` through the same Git commit/tree/blob reader. This
adds at most four cold Git reads for its two-segment path. A non-regular,
missing, excessive, or unavailable manifest withholds selected shards and
retains the already proved aggregate FullCI fallback. Native job-set workflows
perform no manifest read.

For the maximal admitted adapter:

```text
6 fixed tree/registry reads + 38 adapter blobs = 44 Git reads
```

This is a per-load budget, not an arrival-rate or provider-wide budget. One
shared GitHub transport factory admits at most 64 concurrent exchanges,
including credential refresh, and the existing absolute exchange deadline
bounds admission wait. Independently composed factories and replicas multiply
that local contribution. Because GitHub limits are shared with activity
outside this runtime and secondary point costs may be undisclosed, neither fact
proves provider-wide primary or secondary rate safety. A provider error or
exceeded local bound returns no trusted target; it does not retry without a
separate bounded transport policy. Adapter blob reads use structured
concurrency: one failed read cancels and joins every request-owned sibling
before the load returns, so a fail-closed decision cannot leave provider work
running outside its request lifetime.

## 7. Failure Algebra

| Observation                                                                                                                       | Result                                                                                              |
|-----------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------|
| Exact configured workflow ref and valid adapter                                                                                   | Continue admission                                                                                  |
| Configured workflow path, valid workflow SHA, valid adapter                                                                       | Continue admission                                                                                  |
| Caller identity matches but the registry-bound called-workflow identity or SHA does not                                           | FullCI                                                                                              |
| Workflow path matches but workflow SHA is absent or malformed                                                                     | Reject identity                                                                                     |
| Ref or path is outside configured identity sets                                                                                   | Reject identity                                                                                     |
| Registry is absent, malformed, or loaded from another revision                                                                    | FullCI                                                                                              |
| Adapter file is absent, excessive, non-regular, or has a digest mismatch                                                          | FullCI                                                                                              |
| Local reusable-workflow edge is dynamic, foreign, missing, cyclic, or outside the declared closure                                | FullCI                                                                                              |
| External reusable-workflow edge is dynamic or uses a mutable ref                                                                  | FullCI                                                                                              |
| Control projection, route context, protected-job failure semantics, action ref, or image identity is outside the admitted grammar | FullCI                                                                                              |
| A mandatory static job is absent from selected execution                                                                          | FullCI                                                                                              |
| Registry hash differs from production admission                                                                                   | FullCI                                                                                              |
| Provider read is unavailable or incomplete                                                                                        | FullCI                                                                                              |
| One concurrent adapter read fails                                                                                                 | Cancel and join sibling reads, then FullCI                                                          |
| Shard manifest is symlinked, special, missing, or unavailable at the exact head                                                   | Target authority retained; selected shards withheld; FullCI                                         |
| Platform requester OIDC or plan HTTP request is unavailable, non-successful, malformed, or oversized                              | Local FullCI after the call starts                                                                  |
| Platform reusable workflow cannot be resolved or authorized by GitHub                                                             | Fail closed before local fallback; native validation remains independently runnable during adoption |

No adapter or transport failure is converted into selected success.
Coordinator-side authentication remains an HTTP boundary; the platform
requester and target consumer convert every post-resolution non-success outcome
into local FullCI. A cancelled workflow is not relabelled as FullCI success:
the always-run gate rejects cancellation. A provider graph-resolution failure
occurs before target fallback code can run and is therefore reported as a hard
provider failure, never as successful FullCI.

## 8. Alternatives

| Alternative                                               | Verdict                       | Reason                                                                                                                                                                                                 |
|-----------------------------------------------------------|-------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Register every pull-request ref                           | Rejected                      | Unbounded operational state and unavoidable registration races                                                                                                                                         |
| Admit wildcard refs                                       | Rejected                      | Ref pattern does not bind executed adapter bytes                                                                                                                                                       |
| Run the complete CI system in a central reusable workflow | Rejected                      | Adds runtime source-repository availability to execution and fallback authority and weakens target independence                                                                                        |
| Use one immutable central workflow only for OIDC and HTTP | Adopted with bounded residual | Removes OIDC from target-controlled bytes; a pre-execution provider resolution failure remains fail-closed and requires independent native validation during adoption                                  |
| Add a new adapter hash to every plan and database row     | Rejected for now              | `targetRegistryHash` already transitively binds the adapter; another identity would duplicate authority                                                                                                |
| Bind only top-level job topology                          | Rejected                      | Does not bind route conditions, local reusable calls, or validators                                                                                                                                    |
| Bind the adapter through the target registry              | Adopted                       | Reuses existing signed and durable authority while closing the stale-adapter counterexample                                                                                                            |
| List every active default-branch workflow for each plan   | Rejected                      | The list is not revision-addressed, adds rate cost, and is unnecessary when the authenticated run proves the invoked workflow                                                                          |
| Load coordinator source in target CI                      | Rejected                      | Adds an unrelated repository and package-install availability dependency to the fallback path                                                                                                          |
| Treat a local emulator as the GitHub oracle               | Rejected                      | No executor in the bounded 30 July 2026 review covered all required OIDC, policy, cancellation, runner, and provider semantics; an unreviewed executor remains unknown rather than proved insufficient |

## 9. Falsifiers

Implementation is rejected if any of these counterexamples reaches selected
execution:

1. a configured path with an absent or non-immutable workflow SHA;
2. the same path in another repository;
3. a changed top-level workflow with an unchanged registry;
4. a changed generated validator with an unchanged registry;
5. an undeclared or missing local reusable workflow;
6. a local reusable-workflow cycle;
7. an extra unreachable workflow added to the adapter set;
8. a registry and adapter changed together while production admission retains
   the old registry hash;
9. provider bytes loaded from `head_sha` or a mutable ref instead of the OIDC
   workflow SHA;
10. an external reusable workflow referenced by a mutable branch or tag;
11. an unavailable adapter read interpreted as an empty valid bundle;
12. a symlink or special Git object accepted as an adapter file;
13. a changed plan or gate output, command, complete environment value, action
    input, condition, shell, working directory, local action, default,
    container, service, runner, permission, timeout, step order, or identifier;
14. a mutable external action, Docker action, job container, or service image;
15. an execution route controlled by `vars`, `secrets`, `env`, `inputs`,
    `matrix`, or `strategy`;
16. a local reusable-workflow chain exceeding the admitted depth or file-count
    bound;
17. a selected execution that omits a registry-declared mandatory job;
18. a maximal adapter exceeding 44 Git reads or four concurrent blob reads;
19. a failed adapter read returns while a request-owned sibling read remains
    active;
20. a plan transport failure that prevents FullCI from being selected locally;
21. any target workflow that checks out or installs coordinator source at
    runtime;
22. workflow-level environment or defaults, or job-level or step-level
    `continue-on-error`, in any reachable adapter workflow.
23. a shard manifest obtained through Contents API dereferencing, a mutable
    ref, a symlink, a submodule, or any non-regular Git object.
24. a `$` local-action path containing `@`, or any local-action path containing
    an empty, current-directory, parent-directory, or backslash segment.
25. target-controlled workflow, action, script, checkout, or command bytes can
    observe `ACTIONS_ID_TOKEN_REQUEST_TOKEN`.
26. caller workflow identity matches while the registry-bound
    `job_workflow_ref` or `job_workflow_sha` does not.
27. the plan job has OIDC permission, does not depend exactly on the request
    job, or can be skipped after a non-cancelled request failure.
28. caller-selected plan URL `U != C` produces a token accepted by coordinator
    URL `C`.
29. missing, duplicated, non-canonical, excessive, or invalid base64url plan
    chunks reach signed-plan validation as selected authority.

## 10. Local Proof Ladder

Provider attempts are admitted only after these cheaper proof classes close:

```text
static workflow and security lint
  -> extracted immutable-requester and transport falsifiers
  -> exact-Git adapter and control-plane falsifiers
  -> real planner, signature, target consumer, validator, and gate contract laboratory
  -> one exact-head GitHub provider canary
```

The first three classes have bounded repository witnesses in this change. The
contract laboratory runs the real deterministic planner, in-memory issuance
with an ephemeral test key, a path-bound lab-only production subject, generated
plan consumer, plan validator, and gate for selected and fallback routes in both
execution kinds. A separate workflow test extracts and executes the exact
privileged script bytes from the reusable workflow with bounded fake provider
responses. Only job results are synthetic; they prove transport and gate
algebra, not lane execution. The harness is test-only and cannot obtain deployed
credentials, durable production authority, or provider evidence.

A target-specific `application-pilot-ci-local-lab/v1` scenario profile remains a subsequent
integration slice. It will bind exact coordinator and target source epochs and
record whether each result is synthetic or executed. Local workflow executors
may be optional substrates, never semantic oracles.

## 11. Platform Sources

- [Workflow syntax for GitHub Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)
- [Reusable workflow limits](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations)
- [OIDC with reusable workflows](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-with-reusable-workflows)
- [OpenID Connect claims](https://docs.github.com/en/actions/reference/security/oidc)
- [Git Trees REST API](https://docs.github.com/en/rest/git/trees)
- [Git Blobs REST API](https://docs.github.com/en/rest/git/blobs)
- [REST API rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)

## 12. Non-Claims

This design does not prove arbitrary shell-command semantics, test adequacy,
local action or target-entrypoint behavior, repository-variable availability,
third-party action or external reusable-workflow correctness, provider
default-branch activation, branch-protection wiring, provider availability,
SHA-256 collision impossibility, production admission evidence, deployment
readiness, or that a selected plan is globally minimal.
