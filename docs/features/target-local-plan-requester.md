# Target-Local Plan Requester

## Decision And Scope

Owner: execution orchestration for requester admission; target artifacts for
distribution; GitHub integration for exact provider evidence.

Machine requirement: `REQ-CI-RUNTIME-040`. The existing four-file artifact
requirement remains unchanged; this adds one optional requester operation.

Add an optional same-commit requester mode. A target calls
`$/.github/workflows/trusted-plan-request.yml` and commits the exact requester
distributed with the coordinator. Existing external `owner/repo/path@SHA`
bindings remain supported. No existing repository is changed automatically.

This succeeds the requester-distribution and identity subset of
[thin target consumer control](thin-target-consumer-control.md). Other execution,
signature, fallback, production-admission and resource contracts are unchanged.
The [implementation plan](target-local-plan-requester-implementation-plan.md)
owns execution order, not a second behavioral specification.

### Observable Change

Local adoption adds one workflow file to the target, outside its four-file
`.ci-coordinator` artifact directory. It consumes one existing adapter-workflow
slot. The 32-workflow, 33-file and 39-cold-read ceilings do not increase.
Operators explicitly render/check the requester, update the source binding and
render/check the target artifacts. Nothing is written above an output directory
as a hidden side effect. A coordinator/requester version mismatch declines
selective execution; it does not accept arbitrary target-supplied transport code.

The requester retains its isolated OIDC job, pinned action, no checkout, no
target code, no secrets inheritance, two-minute job timeout, bounded HTTP
requests and current output contract. This is not a central workflow dispatcher.

### Explicit Adoption

1. Run `ci-coordinator-target-artifacts render-requester --output .github/workflows/trusted-plan-request.yml` in the target.
2. Set the calling job and source `planRequestWorkflowRef` to the canonical
   local reference. Include the new workflow path and exact SHA-256 in
   `adapterWorkflowFiles`; refresh the changed caller's digest as well.
3. Use the existing `render --source ... --output-directory .ci-coordinator`
   operation, then `check` and `check-requester --output .github/workflows/trusted-plan-request.yml`.
4. Review the complete consumer diff and explicitly admit its caller/callee
   OIDC policy. Commit the workflow and artifact changes together. Old external
   references continue to work; adoption is not an automatic migration.

## Why Local Reuse

Let `G` mean the target's own workflow graph resolves, `R` mean an eligible
runner executes it, `C` mean the run is not cancelled, and `Q` mean the requester
reaches a bounded terminal result. Under the existing target fallback contract:

```text
G AND R AND C AND Q AND NOT AdmittedSelectivePlan
    => TargetFullCI
```

An external reusable workflow adds an independent graph-resolution dependency
`X`: access to and availability of the external repository and pinned workflow.
`NOT X` can prevent graph construction, before a fallback job exists. A local
same-commit reference removes this separate repository edge. It does not prove
`G`, `R` or `C`; malformed own YAML, unavailable GitHub/runners and cancellation
remain outside the fallback guarantee. Action-download failure within the
requester job remains a request failure, not authority to skip FullCI.

GitHub documents that local reusable workflows are loaded from the caller's
commit. The `$` form is specific to GitHub.com; no GHES compatibility is claimed.
[Workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax).

Alternatives:

| Choice                                 | Preserved benefit                                     | Defeating cost or counterexample                                    |
|----------------------------------------|-------------------------------------------------------|---------------------------------------------------------------------|
| Keep only external reuse               | No copied requester                                   | Independent access failure can defeat fallback before execution     |
| Inline into an execution job           | No reusable-file lookup                               | Couples OIDC authority to target execution and duplicates transport |
| Generic transport plugin or dispatcher | More customization                                    | Adds authority and lifecycle mechanisms unnecessary for this defect |
| One canonical local reusable file      | Isolated authority without external repository lookup | One explicit file and version-coordinated admission                 |

The last option is preferred within this bounded cost/safety model, not proved
globally optimal. Revisit when provider identity semantics change, packaging
cost exceeds its bound, target customization is required, or local graph
resolution introduces another independent failure edge.

## Admission Relation

For local mode define authenticated caller repository `r`, configured caller
workflow path `p`, caller ref `f`, immutable caller workflow SHA `s`, canonical
requester path `l`, and coordinator-owned requester bytes `b`.

```text
LocalRequesterIdentity =
    ConfiguredRef = "$/.github/workflows/trusted-plan-request.yml"
    AND AuthenticatedCaller.workflow_ref = r + "/" + p + "@" + f
    AND f is a Git refs/... value
    AND AuthenticatedCaller.workflow_sha = s
    AND AuthenticatedCallee.job_workflow_ref = r + "/" + l + "@" + f
    AND AuthenticatedCallee.job_workflow_sha = s

LocalRequesterContent =
    ExactAdapterRevision = s
    AND l belongs to the complete adapter workflow closure
    AND ProviderRegularBlob(l, s) = b
    AND RegistryDigest(l) = SHA256(b)

LocalMode AND SelectiveExecution
    => LocalRequesterIdentity AND LocalRequesterContent AND ExistingPlanAdmission
```

The caller and callee coordinates come from verified GitHub OIDC claims, not
the request body. GitHub distinguishes caller workflow claims from the called
workflow's `job_workflow_ref`.
[OIDC with reusable workflows](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-with-reusable-workflows).

A same-SHA claim alone does not bind the repository, workflow or ref. A
self-consistent target registry and modified requester do not establish `b`.
Each conjunction member therefore has an independent negative witness, with
all earlier prerequisites valid. Existing issuer, audience, temporal claims,
exact configured identity policy, signature verification and runtime authority
remain necessary; this change grants no implicit installation or OIDC policy.

External mode retains its exact configured full ref and pinned callee SHA
checks. Local mode does not authorize arbitrary local paths, requester `@tag`
overrides, expressions,
secrets or permissions. Missing or mismatching content/identity yields no
selected execution projection through the existing application boundary.

## Ownership And Distribution

The executable source remains `.github/workflows/trusted-plan-request.yml`.
Its packaged YAML is a byte-exact projection. Native proof compares repository
source, installed package resource and explicitly rendered target file, then
executes the real requester entrypoint. A new manual hash constant, second
transport program or runtime dependency is unnecessary.

`target_artifacts.requester` owns bounded resource reading. The GitHub snapshot
adapter may consume this single public artifact capability to compare the
already loaded regular blob. Admit only that import, not the entire target
artifacts package. Pure `repo_context` and `execution_orchestration` cannot load
package resources or depend on providers, runtime or target artifact rendering.
This explicit dependency costs one bounded resource read per local snapshot;
it introduces neither a provider request nor a new mutable cache.

The registry constructor and target-side validator both require the local path
in adapter closure. The renderer rejects a source digest not matching the
packaged requester; it does not silently overwrite user input. The provider
then verifies actual bytes at the trusted caller workflow revision. Thus a
target cannot repair a malicious requester merely by recomputing its registry.

## Proof And Boundaries

Native GitHub checks cover positive local and unchanged external modes; isolated
repository/path/ref/SHA substitutions; missing and modified requester blobs
with recomputed target digests; rendering and package parity; application-level
identity rejection; consumer-lab identity construction; and target-side schema
and byte-bundle parity. The existing requester entrypoint witness remains the
real same-commit GitHub reusable job. Pure fixtures are not live OIDC evidence.

The candidate changes an admission predicate. Its green tests alone are not
bootstrap authority: independent frozen review compares the delta with the
base-owned fallback, identity and permission contracts before squash merge.

This does not complete pre-CI planning, reuse eligibility, adaptive sharding,
durable provider effects, production cutover or the live pilot. The known
non-working webhook remains administrator-deferred. No local behavioral tests,
deployment or live consumer changes are part of this batch.
