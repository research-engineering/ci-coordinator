# Revision-Bound Adapter Admission Implementation Plan

Status: trusted plan-request boundary implementation in progress

Date: 2026-07-30

Design authority:
[Revision-Bound Adapter Admission](revision-bound-adapter-admission.md)

Owner requirement: `REQ-CI-RUNTIME-027`

## 1. Objective

Make pull-request workflows eligible for bounded selected execution without
wildcard trust or per-pull-request registration while ensuring target-controlled
bytes never receive OIDC minting authority. Use one immutable platform reusable
workflow for the privileged request and preserve target-owned validation,
routing, stable gate, and FullCI execution.

## 2. Implementation Order

1. Add canonical repository/workflow-path identities to OIDC settings and
   production-admission subjects while preserving exact-ref identities.
2. Require an immutable caller workflow SHA whenever path identity is the
   matching authority and require an independent exact called-workflow identity
   whenever that namespace is configured.
3. Add a bounded adapter-file model and make it part of canonical target
   execution registry identity.
4. Extend target artifact source and rendering so registry output commits to
   the complete local workflow closure and fixed generated validators.
5. Extend workflow syntax projection with exact static local reusable-workflow
   targets in the documented `$/.github/workflows/{file}` and
   `./.github/workflows/{file}` GitHub.com forms, canonicalize both to one
   repository-relative path, and reject external reusable-workflow targets
   unless pinned to a full immutable Git revision.
6. Verify the registry and every adapter digest through the exact Git
   commit/tree/blob chain, require regular blob modes, and prove the local
   reusable-workflow closure before capacity projection.
7. Add `planRequestJobId` and exact `planRequestWorkflowRef` to the registry.
   Require the request job to call one full-SHA external workflow with only
   OIDC permission, and require the unprivileged plan job to depend on it and
   execute on every non-cancelled result.
8. Admit the generated request, plan, and gate control plane through one
   normalized safety projection over runner, timeout, permissions, outputs,
   ordered steps, action coordinates and inputs, commands, conditions, and
   complete environment values; reject workflow ambient environment/defaults
   and job-level or step-level `continue-on-error` throughout the complete
   reachable workflow closure.
9. Install a bounded target response consumer. Carry
   the signed response through canonical base64url chunks that stay below both
   GitHub's aggregate UTF-16 output budget and the runner's per-environment-value
   bound; malformed, absent, excessive, or failed request output becomes local
   FullCI.
10. Add registry-owned mandatory execution jobs and reject any selected
   projection that omits them.
11. Bound one cold maximal adapter load to 44 Git reads and four concurrent blob
   reads without a default-branch workflow-list request. Keep this per-load
   budget distinct from the shared transport-factory HTTP concurrency cap and
   from unproved provider-wide rate safety. Use structured concurrency so one
   failed blob read cancels and joins every request-owned sibling before the
   load returns.
12. Read an optional witness-shard manifest as a bounded regular Git blob at
    the exact request head; never use Contents API dereferencing as capacity
    evidence.
13. Add counterexample tests for identity broadening, stale or symlinked bytes,
    incomplete closure, cycles, excessive sets, wrong revisions, forged
    control outputs or inputs, ambient authority, weakened failure semantics,
    mutable dependencies, omitted mandatory jobs, transport failure, and old
    production admission.
14. Update machine requirements, Proofkit bindings, deployment configuration,
    architecture routing, and target-adoption documentation.
15. Publish the reusable workflow, configure the minimum private-repository
    access policy, and freeze its exact commit SHA. A mutable ref is never an
    accepted target input.
16. Freeze one central branch head and run its exact selective, native, full
    local, and fresh-context review gates.
17. Regenerate the pilot target target artifacts only from the accepted coordinator
    revision, run the complete provider-independent target preflight, and
    perform one consolidated provider validation.

## 3. Responsibility Map

| Responsibility                                         | Owner                                    |
|--------------------------------------------------------|------------------------------------------|
| OIDC exact-ref and path identity admission             | `identity_admission`                     |
| Runtime environment syntax                             | `runtime_settings`                       |
| Production grant scope                                 | `production_admission`                   |
| Adapter file and registry identity                     | `execution_orchestration`                |
| Workflow call syntax projection                        | `repo_context`                           |
| Exact Git-object provider snapshot                     | `integrations.github`                    |
| Generated target artifacts                             | `target_artifacts`                       |
| Privileged reusable requester                          | repository-owned GitHub Actions workflow |
| Caller-workflow SHA propagation into adapter admission | `app`                                    |

No owner may read environment variables, provider state, persistence, or
wall-clock time outside its existing boundary.

## 4. Acceptance

```text
Accept iff
  every design falsifier is rejected
  and exact-ref behavior remains valid
  and path behavior requires immutable workflow SHA
  and caller and called-workflow identities are both required when configured
  and target-controlled bytes never receive OIDC minting authority
  and the registry binds the exact request job and full-SHA requester workflow
  and adapter closure is canonical, complete, bounded, and acyclic
  and every external reusable-workflow target is immutable
  and every adapter byte is a regular blob fetched at the authenticated
      workflow SHA
  and every optional shard manifest is a regular blob fetched at the exact
      request head SHA
  and the generated request, plan, and gate control plane is exact
  and missing, malformed, or excessive cross-job plan transport yields FullCI
  and no reachable adapter job or step can convert failure to success
  and action and container identities are immutable
  and route conditions have no ambient authority
  and every mandatory static job executes in selected mode
  and cold exact-snapshot reads and concurrency stay within their declared
      bounds
  and a failed snapshot read leaves no request-owned provider I/O running
  and targetRegistryHash changes for every adapter digest change
  and old production admission cannot authorize the changed registry
  and unknown provider or target transport evidence yields FullCI
  and requirements and Proofkit bindings are complete
  and targeted, full, lint, type, import-boundary, schema, and documentation
      witnesses pass
```

Local emulation is not an acceptance substitute. The repository contract
laboratory reduces provider attempts by exercising the real planner, ephemeral
test signing and issuance, bounded response transport, target consumer,
validators, and gate across selected and fallback routes for both execution
kinds. A separate exact-byte oracle executes the coordinator-owned requester
script with synthetic OIDC and HTTP ports. Neither oracle proves provider graph
resolution or claims. A target-specific scenario profile and one exact-head
GitHub canary remain necessary for target and provider-owned semantics.

Provider graph resolution is a separate availability boundary. If GitHub cannot
resolve or authorize the remote reusable workflow, no job in the caller can
convert that pre-execution failure to FullCI. Adoption therefore retains an
independently runnable native Full Check until exact live access, outage, and
rollback evidence is accepted; fail-closed graph resolution is not described as
runtime fallback.

## 5. Rollback

Removing path identities restores exact-ref-only authentication but makes
unbounded pull-request adoption unreachable. Removing adapter binding while
retaining path identities is forbidden because it reopens stale route and
validator authority.

The operational rollback is therefore configuration-only: remove path
identities and retain adapter verification. FullCI remains the total runtime
fallback.
