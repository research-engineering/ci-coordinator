# Service Level Objectives

Status: normative initial production policy

Date: 2026-07-17

## 1. Scope

These objectives govern the coordinator control plane. They do not measure
native target-repository test reliability, GitHub Actions availability, or CI
optimization savings.

Safety invariants and service-level objectives are different logical classes:

```text
SafetyViolationCount must equal 0
Availability and latency may consume a bounded error budget
```

Therefore an unsafe omission or replay mismatch is never averaged into a
monthly budget.

## 2. Plan Availability

Objective: at least 99.9% over a rolling 28-day window.

An eligible request has passed caller syntax and trusted identity admission
before issuance, or explicitly returned authentication dependency unavailable.
Pre-route rejections and other pre-authentication failures are not inferred
eligible. The observed terminal populations are:

```text
G = issued
B = dependency_unavailable or issuance_unavailable or internal_error
    or timed_out or response_failed
X = cancelled
classified = G + B
observed eligible = G + B + X
```

`issued` requires an admitted envelope and completed ASGI response, including
a signed FullCI fallback or duplicate. Durable issuance alone is not success.
Owned work/response expiry before completion is `timed_out`, even when
cancellation is suppressed. Other cancellation before completion is
`cancelled`, separately visible outside GOOD/BAD, not attributed to a client
or server without evidence. Timely completion is not retroactively failed by
cleanup. `invalid`, `unauthenticated`,
`forbidden`, and `conflict` are excluded because they represent caller-policy
or idempotency conflicts rather than service availability.

```promql
sum(increase(ci_coordinator_plan_requests_total{result="issued"}[28d]))
/
sum(increase(ci_coordinator_plan_requests_total{result=~"issued|dependency_unavailable|issuance_unavailable|internal_error|timed_out|response_failed"}[28d]))
and on()
sum(increase(ci_coordinator_plan_requests_total{result=~"issued|dependency_unavailable|issuance_unavailable|internal_error|timed_out|response_failed"}[28d])) > 0
```

The error budget is 0.1% of classified requests in the same 28-day window:
`B <= 0.001 * (G+B)`. For `G+B+X > 0`, display classification coverage
`(G+B)/(G+B+X)` and cancellation exclusion `X/(G+B+X)` beside availability.
These are conditional observations, not universal ingress availability. This
request-based ratio does not define unavailable minutes; that conversion would
require a separate time-based indicator or an admitted traffic model. No
classified requests means no availability observation, including
cancellation-only traffic, not demonstrated success or 100%. Zero observed
eligible requests also means no coverage/exclusion observation.

## 3. Issued-Plan Latency

Objective: at least 99% of successful plan responses complete within 10
seconds over a rolling 28-day window.

```promql
sum(increase(ci_coordinator_http_request_duration_seconds_bucket{method="POST",route="/api/v1/dynamic-ci/plan",status_class="2xx",le="10.0"}[28d]))
/
sum(increase(ci_coordinator_http_request_duration_seconds_count{method="POST",route="/api/v1/dynamic-ci/plan",status_class="2xx"}[28d]))
```

Completion means returned ASGI send, not client receipt. The histogram excludes
incomplete or timed-out plan responses even if their start status was 2xx.
Duration ends at completion, not later cleanup. Ten seconds is an explicit
bucket below the default 15-second request deadline, leaving five seconds for
network and target-workflow overhead. Failed requests
belong to availability and are excluded from latency to avoid double counting.

## 4. Non-Budgetable Safety Signals

The following counters must never increase:

- `ci_coordinator_shadow_unsafe_omissions_total`;
- `ci_coordinator_shadow_replay_mismatches_total`.

Any increase blocks rollout expansion and requires evidence review. Neither
counter alone proves user impact; both prove that a safety assumption failed.

## 5. Operational Alerts

The executable rules use paired short and long windows so one isolated sample
does not consume an entire incident channel while sustained or fast budget burn
does. The minimum-volume guards prevent division by tiny samples.

| Alert                             | Condition                                                       | Action class      |
|-----------------------------------|-----------------------------------------------------------------|-------------------|
| `CIPlanAvailabilityFastBurn`      | 14.4x budget burn over 5m and 1h, at least 20 classified requests | Page              |
| `CIPlanAvailabilitySlowBurn`      | 6x budget burn over 30m and 6h, at least 100 classified requests  | Ticket            |
| `CIPlanLatencyFastBurn`           | 14.4x latency budget burn over 5m and 1h, at least 20 successes | Page              |
| `CIUnsafeOmissionObserved`        | Any unsafe omission in 5m                                       | Safety escalation |
| `CIReplayMismatchObserved`        | Any replay mismatch in 5m                                       | Safety escalation |
| `CIReconciliationTerminalFailure` | Terminal background state                                       | Page              |
| `CICoordinatorNotReady`           | One instance remains not ready for 5m                           | Investigate       |

## 6. Deployment Obligations

Production admission additionally requires:

1. Prometheus scrapes every serving replica at an interval no greater than 30 seconds.
2. Counter-reset-aware `rate` and `increase` functions are used; raw counters are not compared across restarts.
3. Rule evaluation is no slower than 30 seconds.
4. Alertmanager routing, receiver ownership, retention, and access control are configured outside this repository.
5. The metrics endpoint is reachable from the scraper but not exposed as a public internet API.
6. At least one synthetic alert is delivered and acknowledged before rollout.

Without those external facts, the repository proves rule shape and runtime
signals, not production observability or SLO attainment.
