# Exact Workflow Invocation Admission Implementation Plan

Status: implemented and locally verified; provider CI and merge pending

Date: 2026-08-31

Design authority:
[Exact Workflow Invocation Admission](exact-workflow-invocation-admission.md)

Owner requirement: `REQ-CI-RUNTIME-027`

## 1. Objective

Replace expression-based workflow-path inference with exact, case-sensitive
direct-invocation evidence while preserving one target workflow, one OIDC
requester, and the existing fail-closed FullCI route.

## 2. Implementation Order

1. Preserve the predecessor design and plan byte-for-byte; record this change
   only in successor documents.
2. Define the fixed `ci-invocation` role in static target authority and include
   it in case-insensitive role-collision admission and exact job topology.
3. Generate the credential-free classifier and hash it with request, plan, and
   gate jobs as one complete candidate tuple.
4. Remove workflow-path interpolation from requester conditions. Require exact
   classifier output, the trigger-derived dynamic event set, and the admitted
   optional draft policy.
5. Require exact classifier, requester, plan, fallback, execution, and gate
   dependencies in provider inventory and adoption assessment.
6. Keep plan and gate checkout and validation bound to
   `job.workflow_ref`/`job.workflow_sha`.
7. Add compact parameterized falsifiers for identity sources, equality,
   non-empty handling, job shape, permissions, outputs, dependencies,
   candidate tuple integrity, path injection, and role collisions.
8. Update normative architecture, requirement, bootstrap, and fixture
   projections without introducing a configurable invocation-mode field.
9. Regenerate target artifacts from their canonical source and prove generated
   checks are stable.
10. Run repository-owned static gates locally and behavioral gates on GitHub.
11. After squash merge, pin pilot target target artifacts to the exact merge revision
    and rerun the provider-independent consumer laboratory without opening a
    pilot target pull request.

## 3. Responsibility Map

| Responsibility                               | Owner                                    |
|----------------------------------------------|------------------------------------------|
| Fixed classifier role in target topology     | `execution_orchestration`                |
| Exact workflow syntax and control projection | `repo_context`                           |
| Provider-observed topology admission         | `repo_context`                           |
| Non-authoritative adoption blockers          | `workflow_discovery`                     |
| Exact provider snapshot                      | `integrations.github`                    |
| Generated target files                       | `target_artifacts`                       |
| Runtime selected-plan identity               | `identity_admission` and `plan_issuance` |

`execution_orchestration` remains the sole owner of the fixed role.
`repo_context` receives its nominal identity as an explicit input and imports
neither registry behavior nor concrete provider code.

## 4. Acceptance

```text
Accept iff
  predecessor design and plan equal their base bytes
  and exact classifier projection matches the target workflow
  and workflow path cannot alter requester expression source
  and classifier output is true only for non-empty exact identity equality
  and requester depends exactly on classifier
  and plan depends exactly on requester and runs after non-cancelled failure
  and protected role identities are pairwise distinct
  and complete candidate hashes cannot be mixed
  and plan and gate use defining-job workflow identity
  and every design falsifier is rejected
  and generated target artifacts are current
  and lint, type, import-boundary, schema, docs, Proofkit, and GitHub behavioral
      witnesses pass on one frozen head
```

## 5. Rollback

Rollback is one successor commit reverting classifier, topology, projection,
fixtures, and successor routing together. Partial rollback is forbidden: a
requester without exact invocation evidence or a topology that omits the
classifier reopens the rejected counterexamples. Native FullCI remains the
operational fallback before, during, and after rollback.
