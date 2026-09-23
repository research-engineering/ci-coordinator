# Bounded Policy Admission Implementation Plan

Status: implemented and locally verified; provider CI and merge pending

Date: 2026-07-29

Owner requirements:

- `REQ-CI-CORE-014`
- `REQ-CI-CORE-016`
- `REQ-CI-RUNTIME-008`

Design authorities:

- [Config Policy Admission](../architecture/modules/config-control.md)
- [Config Epoch Lifecycle](../architecture/modules/config-epoch-lifecycle.md)
- [HTTP API](../architecture/modules/api-http.md)

## 1. Objective

Preserve the complete valid policy-input domain while making CPU-bound policy
admission preemptible by the existing absolute request deadline.

The pure parser is total and byte-bounded, but synchronous execution on the
event-loop thread prevents `asyncio.timeout` from running until parsing
returns. Therefore the existing timeout text was stronger than the executable
runtime behavior.

## 2. Decision

One backend process owns one no-queue policy-admission slot. An admitted caller
executes the existing pure `admit_policy_document` operation in an AnyIO worker
process with cancellation enabled.

For backend process `P`:

```text
RunningPolicyAdmissions(P) <= 1
WaitingPolicyAdmissionQueue(P) = 0

slot unavailable => typed service unavailability
request cancelled while worker runs => worker process terminated
worker process failure => typed service unavailability
parser result => unchanged PolicyAdmissionResult
```

The HTTP route still authenticates the bearer before invoking the use case.
Policy admission still precedes scope authorization because the admitted
document owns the repository scope that must be authorized.

## 3. Rejected Alternatives

| Alternative                                | Rejection proof                                                                                                                               |
|--------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------|
| Lower the source, node, or scalar limit    | It rejects inputs in the currently valid machine-owned domain and does not prove event-loop preemption.                                       |
| `asyncio.to_thread`                        | A Python CPU-bound parser still competes for the GIL, and cancellation cannot terminate the running function.                                 |
| Unbounded process submission               | Authenticated callers can accumulate CPU work and retained request state faster than it completes.                                            |
| A waiting in-process queue                 | Request tasks retain source payloads while waiting; the queue adds no correctness value because callers can retry a typed unavailable result. |
| Reimplement parsing in the runtime adapter | It creates a second semantic owner and risks divergence from pure policy admission.                                                           |

## 4. Implementation Slices

### Slice A: application port

- inject one async policy-admission callable into `ConfigManagementService`;
- map only explicit worker unavailability to the existing unavailable result;
- propagate cancellation and programming failures unchanged.

### Slice B: runtime adapter

- add one process-lifetime non-waiting admission gate shared by every adapter
  instance;
- pass one private single-token worker limiter instead of entering the ambient
  AnyIO process queue;
- execute the existing top-level pure admission function with
  `cancellable=True`;
- map a broken worker process or process-spawn failure to typed unavailability;
- add AnyIO as a direct locked runtime dependency.

### Slice C: witnesses and proof routing

- prove the process call receives exact source bytes and format;
- prove only one concurrent admission is submitted and the second fails
  without queueing even through a different adapter instance;
- prove cancellation releases local admission capacity;
- prove the installed AnyIO runtime terminates an actual progressing worker
  after cancellation and permits a subsequent real policy admission;
- prove a broken worker does not reach persistence;
- prove a real admission result round-trips across the process boundary;
- bind design, code, dependency, and falsifier paths to all three owners.

## 5. Acceptance Predicate

```text
Accept iff
  pure policy semantics are unchanged
  and valid source limits are unchanged
  and RunningPolicyAdmissions <= 1
  and no application-owned admission queue exists
  and no ambient AnyIO process queue owns policy-admission progress
  and cancellation is configured to terminate a running worker
  and a native worker-process falsifier observes progress stop after cancellation
      before a subsequent real admission succeeds
  and busy or broken worker state reaches no persistence
  and the existing absolute request deadline still owns the HTTP response
  and Proofkit reports no unknown changed-path edge
```

## 6. Non-Claims

- The bound is per backend process, not a deployment-wide CPU quota.
- The change does not prove ingress, load-balancer, or horizontal-scaling
  configuration.
- It does not authorize an invalid policy, bypass scope authorization, or
  change registration, activation, rollback, or persistence semantics.
