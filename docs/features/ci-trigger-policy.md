# CI Trigger Policy

Status: active event-policy successor

Date: 2026-09-19

Owner: repository CI and `ci-coordinator.release`

## Scope and precedence

This document supersedes only the automatic post-merge scheduling and release
Full Check event requirement in [release artifact publication](../architecture/cross-cutting/release-artifact-publication.md)
and [automatic mutation discovery](automatic-mutation-testing.md).
Existing design and implementation-plan payloads remain historical descriptions
of their original scheduling. Publication, identity, attestation and all other
release predicates remain unchanged. The machine release requirement is
[`REQ-CI-RELEASE-001`](../specs/ci-coordinator-release/requirements.v1.json).

## Decision

| Workflow                     | Automatic events                                | Explicit event      |
|------------------------------|-------------------------------------------------|---------------------|
| Full Check                   | `pull_request`, `merge_group: checks_requested` | `workflow_dispatch` |
| Automatic mutation discovery | `pull_request`                                  | `workflow_dispatch` |
| Release Artifact             | None                                            | `workflow_dispatch` |

A merge to `master` does not automatically repeat either validation workflow.
Full Check retains the complete required job graph, including curated mutation
witnesses and diagram rendering. Discovery retains both engine profiles on PRs.
The strict required `Pull Request Gate` remains the merge prerequisite; branch
protection is not relaxed. Direct pushes must not bypass that prerequisite.

## Release admission

The release query and its independent response validator both require
`G.event = workflow_dispatch`, replacing the old `push` predicate. They retain
exact workflow identity, immutable repository identity, `master`, source SHA,
completed success, unique run selection, and the run attempt. A PR result,
merge-group result or historical push result cannot substitute for this gate.

Use the existing workflow controls when a release candidate needs qualification:

```sh
gh workflow run python-persistence.yml --ref master -f proof_scope=full
```

Wait for that exact run to succeed, then dispatch `Release Artifact` from
`master`. If `master` moved between the two dispatches, the release rejects the
old result: qualify the new source before retrying. Re-run the existing Full
Check run when retrying a failure; multiple distinct successful dispatches for
one source remain ambiguous under the existing release admission rule.

Dispatching Full Check grants no publication authority. A separate release
dispatch remains necessary, and this change performs no deployment. The
`proof_scope` input controls Proofkit routing evidence; it does not omit native
jobs. An input-dependent reduction of that job graph must first revise release
admission so a partial run cannot authorize publication.

## Rationale and revision conditions

Previously every merge eagerly paid the complete verification cost, even when
no release was requested. The release workflow consumed the resulting exact
commit proof. Scheduling that proof explicitly preserves the release predicate
while avoiding the unconditional duplicate. This deliberately retains a full
qualification when a release is requested; it does not claim PR evidence reuse.

Deleting only the `push` triggers would strand release admission because no
future run could satisfy its old event filter. Reusing a PR result by tree
identity would require additional provider, merge-subject and workflow trust
rules. That is a separate optimization with a larger proof obligation, not a
prerequisite for removing automatic post-merge runs. Revisit if releases also
need to avoid exact-source requalification or if branch protection changes.

## Acceptance

Existing workflow tests require the exact event sets. Release falsifiers admit
a successful dispatch and reject wrong events, wrong source or repository,
non-success, missing and ambiguous results. The shell witness executes the
tracked preflight and verifies the actual provider query arguments. Workflow
inventory digests are recomputed from all four tracked workflow files.

Local validation is static. Behavioral falsifiers and complete regression run
through the repository GitHub workflows; a green PR run does not prove an
actual release or that the new scheduling is active before merge.
