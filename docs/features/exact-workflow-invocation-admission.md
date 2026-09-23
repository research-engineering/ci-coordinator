# Exact Workflow Invocation Admission

Status: current successor design

Date: 2026-08-31

Predecessor:
[Revision-Bound Adapter Admission](revision-bound-adapter-admission.md)

Implementation plan:
[Exact Workflow Invocation Admission Implementation Plan](exact-workflow-invocation-admission-implementation-plan.md)

Owner requirement: `REQ-CI-RUNTIME-027`

## 1. Decision

A target workflow may expose direct dynamic events, manual dispatch, and
same-repository reuse without granting all invocation modes selected-execution
authority. One fixed, credential-free `ci-invocation` job classifies the
invocation before the OIDC requester can run.

```text
DirectInvocation :=
  callerWorkflowRef != empty
  and callerWorkflowRef = definingWorkflowRef

PlanRequestActive :=
  DirectInvocation
  and eventName in (declaredTriggers intersection DynamicEvents)
  and DraftPolicyAllows(event)

DynamicEvents := {pull_request, push, merge_group}
```

The comparison is a case-sensitive shell equality over provider values passed
as environment data. Workflow paths are never interpolated into GitHub
expressions or shell source.

## 2. Problem

The GitHub expression language compares strings without case sensitivity, and
`startsWith` proves only a prefix. It therefore cannot prove exact invocation
identity. In a reusable workflow, the `github` context identifies the caller,
while `job.workflow_ref` identifies the workflow that defines the current job.

The `job` context is unavailable in `jobs.<job_id>.if`. Consequently, no
job-level expression can directly compare these two identities with the
required semantics. An exact runtime comparison is necessary.

## 3. Control Topology

The admitted control plane is one content-addressed tuple:

```text
ControlPlane :=
  (invocationClassifier, planRequester, planConsumer, aggregateGate)

needs(invocationClassifier) = empty
needs(planRequester) = {invocationClassifier}
needs(planConsumer) = {planRequester}
```

The fixed classifier has:

- `runs-on: ubuntu-24.04`;
- `timeout-minutes: 1`;
- `permissions: {}`;
- no checkout, action, network command, credential, or repository byte;
- one `direct` output restricted by exact projected script bytes to `true` or
  `false`.

The requester retains the only `id-token: write` permission. The plan consumer
retains `if: !cancelled()`, so classifier or requester non-success produces
absent plan data and reaches the existing native FullCI route. Cancellation is
not relabelled as success.

## 4. Safety Proof

Assume GitHub supplies `github.workflow_ref` and `job.workflow_ref` according
to its documented contexts, the admitted adapter digest matches executed
workflow bytes, and SHA-256 collision resistance holds.

Let `C` be the caller ref, `D` the defining-job ref, and `A` the classifier
output.

```text
ProjectedClassifier
  => A = true iff C != empty and C = D

ReusableInvocation
  => C identifies caller and D identifies called workflow
  => C != D
  => A = false

DirectInvocation
  => C and D identify the same workflow execution
  => C = D
  => A = true

A != true or unsupported event
  => requester skipped
  => no signed plan chunks
  => plan validation cannot authorize selected execution
  => native FullCI
```

The complete four-job projection is matched as one tuple. Therefore hashes
from different admitted draft-policy candidates cannot be combined into a new
control plane.

The path-injection counterexample is excluded because:

```text
workflowPath not in requester expression source
and workflowPath not in classifier script source
and provider identities enter shell only through environment values
```

## 5. Failure Algebra

| Observation                                     | Result                                                                       |
|-------------------------------------------------|------------------------------------------------------------------------------|
| Empty or missing defining identity              | `direct=false`; native FullCI                                                |
| Case-only identity difference                   | `direct=false`; native FullCI                                                |
| Manual or unsupported event                     | requester skipped; native FullCI                                             |
| Same-repository reusable invocation             | requester skipped; native FullCI                                             |
| Classifier timeout or failure                   | requester skipped; native FullCI after non-cancelled plan job                |
| Remote requester failure after graph resolution | absent or fallback plan; native FullCI                                       |
| Remote reusable workflow cannot resolve         | provider hard failure; independent native adoption workflow remains required |
| Unsupported `job.workflow_ref` provider profile | no selected-execution admission                                              |

## 6. Alternatives

| Alternative                                   | Verdict               | Proof obligation it fails or adds                                              |
|-----------------------------------------------|-----------------------|--------------------------------------------------------------------------------|
| `startsWith(github.workflow_ref, ...)`        | Rejected              | Prefix and case-insensitive equality do not imply exact identity               |
| GitHub expression equality                    | Rejected              | Equality is case-insensitive and job-level `if` cannot read `job.workflow_ref` |
| Add a user-configurable invocation-mode field | Rejected              | Duplicates trigger and content authority without adding behavior               |
| Add a configurable classifier job id          | Rejected              | Creates a new collision/configuration surface for one protocol-fixed role      |
| Split direct and reusable workflows           | Allowed, not required | Strong isolation but duplicates target topology and migration work             |
| Credential-free classifier job                | Adopted               | Minimum mechanism that can observe both identities and compare them exactly    |

## 7. Falsifiers

The design is rejected if any of these reaches selected execution:

1. a workflow path changes requester expression syntax;
2. empty caller and defining refs produce `direct=true`;
3. a case-only mismatch produces `direct=true`;
4. caller or defining identity is replaced by the other context;
5. equality is inverted, weakened, or replaced by a prefix comparison;
6. classifier runner, timeout, permissions, output, environment, shell, script,
   steps, or dependency set changes without projection rejection;
7. requester no longer depends exactly on the classifier;
8. any protected role aliases `ci-invocation` under GitHub comparison rules;
9. hashes from separate control-plane candidates can be combined;
10. plan or gate uses caller `github.workflow_ref` or `github.workflow_sha` in
    place of defining-job identity;
11. classifier or requester failure can authorize omission;
12. manual, unsupported, or reusable invocation can mint the selected-plan
    request accepted by the coordinator.

## 8. Platform Sources

- [Contexts reference](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts)
- [Evaluate expressions](https://docs.github.com/en/actions/reference/workflows-and-actions/expressions)
- [Reuse workflows](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows)
- [OIDC with reusable workflows](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-with-reusable-workflows)

## 9. Non-Claims

This design does not prove GitHub Enterprise Server support for defining-job
identity, cross-repository fallback before reusable-workflow graph resolution,
provider availability, branch-protection wiring, target test adequacy,
production admission, deployment readiness, or a five-minute workflow target.
