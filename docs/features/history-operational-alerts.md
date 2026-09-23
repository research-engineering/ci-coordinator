# History Operational Alerts

Status: bounded design; native qualification pending

Date: 2026-09-13

## Scope And Owners

This design owns the new history warning policy and the rationale for sensitive
outcome witnesses. The [plan](history-operational-alerts-plan.md) owns execution
and acceptance. The [runbook](../how-to/operate-production-observability.md)
owns investigation, not notification routing or automatic remediation.

Baseline: `a7962f874602ad409ee2267ece19fd68b3682a6b`.
The [roadmap](../../ROADMAP.md) D4/E1 row and
[validated review](../adoption/snapshot-review-validation-2026-09-13.md) request
history alerts, failed-replica attribution and seven missing named rule oracles.
The [observability contract](../architecture/modules/observability.md) preserves
loss-tolerant telemetry and finite labels. Existing objectives remain owned by
[service-level objectives](../reference/service-level-objectives.md); this is
not a replacement SLO or a whole-history progress monitor.

Support sources that integration must rebind:

- [metric help and label admission](../../backend/src/ci_coordinator/observability/runtime_metrics.py);
- [collection producer](../../backend/src/ci_coordinator/app/ci_history_collection.py),
  including `_collect`, `_scan`, `_recheck`, `_read` and `_observe_provider`;
- [delivery producer](../../backend/src/ci_coordinator/app/ci_history_delivery.py),
  including per-scope and round failure handling;
- [selected inbox samples](../../backend/src/ci_coordinator/persistence/ci_history_delivery_transfer.py);
- [provider population and pagination](../../backend/src/ci_coordinator/integrations/github/ci_history_provider.py)
  and [nonterminal decoding](../../backend/src/ci_coordinator/integrations/github/ci_history_decoding.py);
- [producer witnesses](../../backend/tests/unit/app/test_ci_history_collection.py)
  and [delivery witnesses](../../backend/tests/unit/app/test_ci_history_delivery.py);
- [rule runner](../../scripts/prometheus_rules.py) and its
  [native invocation](../../.github/workflows/python-persistence.yml).

## Current, Intended And Protected Observations

Current: eleven executable alerts have four named alert-test identities.
The seven without named expectations are `CIUnsafeOmissionObserved`,
`CIReplayMismatchObserved`, `CIReconciliationTerminalFailure`,
`CIPlanAvailabilityFastBurn`, `CIPlanAvailabilitySlowBurn`,
`CICoordinatorNotReady` and `CIPlanLatencyFastBurn`.
There is no history-specific warning. Existing fixtures are useful evidence,
not proof that absent product predicates already hold.

Intended delta: add five warning predicates over existing metrics, append
sensitive named fixtures, and document target drilldown. Preserve every
predecessor scenario and all eleven existing rule expressions, intervals,
labels, pending periods, holds and annotations byte-for-byte.

Protected observations: planning, issuance, reconciliation, access decisions,
SQL state, provider calls, scheduling, scrape topology, metric cardinality and
existing notification grouping do not change. In particular, terminal failure
keeps service-level `max`, not `max by(job, instance)`. No repository, delivery,
run, exception or credential becomes an alert label.

## Minimum Sufficient Model

Disposition: reuse the root's finite observation model, refined by the exact
producer contracts. This is a material operational change because it adds
operator-visible warnings; it does not need a new queue or domain state model.

For target `s`, finite exporter labels `l`, time `t` and window `w`, let
`I(s,l,w,t)` be Prometheus's reset-aware extrapolated counter increase.
Apply `increase` per series before aggregation. A warning is a predicate of
these observations followed by the existing Prometheus pending/hold state
machine. Missing or insufficient samples yield no observed-event warning,
not a healthy dataset. The existing telemetry alerts own missing scrapes.

For histogram count `C` and cumulative bucket `B(k)`,
`sum(I(C,w,t) - ignoring(le) I(B(k),w,t))` estimates observations strictly above `k`.
It is neither a quantile nor current backlog size. Both operands and the exact
bucket are required; absence must not be filled with fabricated zero values.
Do not subtract raw counters before reset handling or aggregate across resets.
Pair operands on every label except `le` before summing. Summing independently
would let a missing bucket on one target fabricate old/expired observations
from that target's count. Missing counts on another target could instead hide
real observations. This countermodel justifies one native matching modifier,
not a new metric or evaluator. Reopen if exporter labels cease to form pairs.

The estimator assumes coherent histogram families with comparable scrape
histories. Counter extrapolation, cold starts and uneven sample gaps can distort
the difference; it is not proof of an exact number of events inside the window.
Investigation must inspect the paired series and timestamps before asserting
durable impact. Qualifying the scrape/metric-relabel configuration remains a
deployment obligation, not a predicate this writer can authenticate.

Distinguishable states include idle, routine progress/deferral, observed
failure, an old selected source, a nonzero expired sample, missing telemetry,
pending warning, firing hold and recovered expression. Merging any of these
would erase an owner-relevant falsifier. No inferred enabled/paused gauge,
per-repository freshness, exact oldest source, or no-progress timer is admitted.

`applied` can be durable recording of a provider deferral or unavailable
attempt. It is not provider success and cannot veto a provider warning.
`page`, `complete`, `partial`, `unavailable`, `unavailable_attempt`,
`provider_not_terminal`, `conflict` and `other` are not selected as provider
failures. In particular, `unavailable` describes archived job population;
`unavailable_attempt` can be a legitimate absent historical attempt.
`provider_incomplete` and `provider_unstable` describe failed consistent reads,
not normal bounded `partial` population. Repetition merits investigation but
does not establish a GitHub outage or quota exhaustion.

## New Warning Policy

All new rules filter scrape metadata `job="ci-coordinator"`, evaluate every
30 seconds and use warning severity. Item/provider warnings aggregate by the
finite `lane`; delivery/sample warnings aggregate at service level. `job` and
`instance` belong to Prometheus target metadata, not intrinsic exporter labels.

| Alert                                    | Independent selection and predicate                                                                                                                                                                                    | Pending / hold | Meaning                                                                            |
|------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------|------------------------------------------------------------------------------------|
| `CIHistoryCollectionFailuresObserved`    | Items, outcomes `store_unavailable` or `unexpected_error`; sum by lane of 15m increases >= 3                                                                                                                           | 5m / 15m       | Repeated item-processing failures were observed.                                   |
| `CIHistoryProviderFailuresObserved`      | Provider results, outcomes `access_unavailable`, `timed_out`, `provider_unavailable`, `provider_binding_mismatch`, `provider_malformed`, `provider_incomplete`, `provider_unstable`; sum by lane of 15m increases >= 3 | 5m / 15m       | Repeated access or provider-read problems were observed before durable completion. |
| `CIHistoryDeliveryFailuresObserved`      | Delivery outcomes `store_unavailable`, `unexpected_error`, `timed_out`; sum of 15m increases >= 3                                                                                                                      | 5m / 15m       | Inbox processing repeatedly reported failures, not a count of lost deliveries.     |
| `CIHistoryOldPendingSamplesObserved`     | Pending-age count minus `le="3600.0"` bucket after separate 15m increases, paired ignoring only `le`, then summed; result >= 3                                                                                         | 5m / 15m       | Repeated selected pages contained a source older than one hour.                    |
| `CIHistoryExpiredPendingSamplesObserved` | Expired-sample count minus `le="0.0"` bucket after separate 15m increases, paired ignoring only `le`, then summed; result > 0                                                                                          | None / 15m     | At least one sampled observation contained expired pending sources.                |

Three estimated observations plus a five-minute pending period filter an
isolated transient while remaining useful for low-volume workers. They do not
prove three distinct affected objects or failures continuing at evaluation
time. An expired sample warrants investigation without that delay because its
source-retention opportunity has already elapsed. One hour is an initial
selected-page age threshold, not an admitted service deadline. Initial import
and replay may legitimately select old sources. Fifteen-minute windows and
holds support investigation under intermittent sampling, at the cost of delayed
clearance; a held alert never asserts a fresh failure.

Tune only after measuring scrape gaps/resets, normal turn cadence, import/replay
age distribution, warning frequency, actionable incidents and time to response.
Repeated non-actionable warnings overturn these thresholds. A requirement for
per-dataset no-progress, quota classification or exact backlog age needs new
owner telemetry and a separately admitted change; silence here cannot satisfy it.

## Independent Operands And Falsifiers

The complete operand inventory for this bounded rule portfolio is below.
Static mapping is not mutation execution; the native gate must replay final
fixtures and the reviewer must challenge the causal isolation.

| Predicate family           | Independent operands                                                                                                                                                                                           | Isolated causal challenges                                                                                                                                                                |
|----------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Two safety counters        | Correct metric, 5m reset-aware increase, sum across targets, > 0, 15m hold                                                                                                                                     | Grow only one counter at a time; zero/non-growing, reset-only and absent signals; one event ages out and hold expires.                                                                    |
| Terminal failure           | Gauge, service max across replicas, == 1, 1m pending, 15m hold                                                                                                                                                 | One failed among healthy replicas; transient under 1m; zero/absent; fail then recover; target handoff must keep one service alert.                                                        |
| Availability fast/slow     | Two failure outcomes in numerator; those plus issued in denominator; independent short and long windows; strict burn threshold in each; short eligible increase minimum; service aggregation; pending and hold | Only short burns, only long burns, each failure outcome alone, excluded outcomes, below/above threshold, exact/below volume, reset-only, absent numerator/denominator and recovery.       |
| Successful-plan latency    | POST, plan route, 2xx, exact 10-second bucket; bucket and count independently in both windows; complement ratio; strict thresholds; short count minimum; pending and hold                                      | Isolate wrong method/route/status/bucket, only one window burns, below/above threshold and volume, missing bucket/count, reset-only and recovery.                                         |
| Readiness                  | Per-target gauge == 0, 5m pending, 5m hold, target labels                                                                                                                                                      | Mixed replicas, brief not-ready, ready/absent, recovery.                                                                                                                                  |
| Webhook warning            | Job, POST, webhook route, 5xx, 5m increase > 0, service aggregation, 5m hold                                                                                                                                   | Retain all predecessor positive, each wrong selector, zero, absent, reset and hold cases.                                                                                                 |
| Targets absent             | Coordinator job selector, absence, 5m pending/hold                                                                                                                                                             | Retain unrelated-job and timing witnesses; add restoration and hold expiry.                                                                                                               |
| Scrape failed              | Coordinator job selector, up == 0, per-target identity, 5m pending/hold                                                                                                                                        | Retain mixed, healthy, missing and recovery witnesses; add unrelated down target.                                                                                                         |
| Readiness telemetry absent | up == 1, unless readiness exists, both job and instance matching, 5m pending/hold                                                                                                                              | Retain mixed/unrelated/healthy witnesses; add different-instance readiness, down/absent up and restored telemetry hold.                                                                   |
| History failure counters   | Metric identity; coordinator job; every included/excluded outcome; lane partition where present; per-series resets; 15m increase; >= 3; 5m pending/15m hold                                                    | Each selected outcome alone; routine/idle outcomes alone; applied alongside failed reads; other job; split lanes; two targets; threshold, single event, resets, absence, window and hold. |
| Old-page samples           | Count and 3600 bucket, coordinator job, exact target pairing ignoring only le, 15m increases before subtraction/sum, >= 3, pending/hold                                                                        | Age <= 3600 yields equal operands; above yields difference; wrong bucket/job, missing operand on one or all targets, per-target reset, insufficient observations and expiration.          |
| Expired samples            | Count and zero bucket, coordinator job, exact target pairing ignoring only le, 15m increases before subtraction/sum, > 0, hold                                                                                 | Zero-only samples, one positive sample regardless of its size, wrong bucket/job, missing operand on one or all targets, resets, absence and expiration.                                   |

## Oracle Repair Trajectories

At repair baseline `656c9bb7262dd1a1482e3b3c798d3b6963a1050b`, four
root-admitted findings require additional causal isolation. The
[repair readiness rows](history-operational-alerts-plan.md#oracle-isolation-repair)
preserve their separate obligations. Production expressions do not change.

ORACLE-1: for each latency method/route/status denominator selector, append
separate short and long cases with 100 valid successes per minute and 10000
foreign counts per minute. A short-window case starts with 50% slow traffic
for 60m then becomes entirely fast; at 90m the short burn is zero, the long burn
is still above 14.4%, and the normal recovery hold has expired. A long-window
case starts entirely fast for 60m then becomes 20% slow; at 70m only the long
burn is below threshold. Including foreign counts in the selected denominator
makes that guard true while the other guard and volume are already true.
These are not two false-window negatives. For le, retain a coherent positive
histogram with 80 observations/minute in the 10-second bucket and 100 in both
the 15-second and infinite buckets, with count 100; including all buckets in
either numerator suppresses an otherwise required alert.

ORACLE-2: all-failure 100/minute controls separately supply
`dependency_unavailable` and `issuance_unavailable` with no other eligible
series. Both alerts must fire. Removing that outcome from either denominator
or the minimum selector makes the required observation absent. The separate
98 issued + 1 dependency failure + 1 issuance failure per minute control needs
issued for volume: failures alone yield only 10/60 observations in 5m/30m.
Its two burn windows otherwise remain true.

For each failure outcome separately, use the following reset-free per-minute
counts. `T` is total eligible traffic, `F` is the selected failure outcome,
and issued is `T - F`. The new slope starts immediately after the phase split.

| Selected false window |      T | F before / after split | Split / evaluation | Other guard and falsifier                                                                                               |
|-----------------------|-------:|-----------------------:|--------------------|-------------------------------------------------------------------------------------------------------------------------|
| Fast short            |  10000 |              500 / 143 | 60m / 90m          | Long burn remains high; 143/10000 is below 0.0144 but 143/9857 is above it.                                             |
| Fast long             |  10000 |              142 / 145 | 60m / 70m          | Short burn is 0.0145; long burn stays below 0.0144 but removing the failure from its denominator crosses the threshold. |
| Slow short            | 100000 |             2000 / 598 | 360m / 480m        | Long burn remains high; 598/100000 is below 0.006 but 598/99402 is above it.                                            |
| Slow long             | 100000 |              597 / 610 | 360m / 420m        | Short burn is 0.0061; long burn stays below 0.006 but removing the failure crosses it.                                  |

Removing issued from either denominator also crosses these boundaries. The
all-failure controls and issued-dependent volume case separately distinguish
each eligible member of the minimum selector. Volumes have ample margin;
the negative short-window evaluations occur after the old hold can expire,
while the long-window mutants have time to satisfy their pending period.

Nominal strict-boundary cases additionally target each burn comparison. Short
windows settle at F/T = 8640/600000 (fast) or 180/30000 (slow), after a higher
burn phase. For the fast long boundary, T is 29400000 and F changes from
419760 to 441000 after 60m. At 70m the 59 sampled minute deltas contain 49 old
and 10 new deltas, nominally yielding 0.0144; absence at 72m and firing at 73m
distinguish the pending start under strict versus inclusive comparison. For
the slow long boundary, T is 89700000 and F changes from 536400 to 547170
after 360m. At 420m, 299 old plus 60 new sampled deltas nominally yield 0.006;
the analogous expectations are absence at 435m and firing at 436m.
These input identities are algebraic design aids, not proof that Prometheus
rounding yields the exact threshold. Native execution must establish the
actual float result and causal pending boundary before accepting sensitivity;
an unexpected result requires input calibration, not an unsupported pass.

ORACLE-3: each counter has target A constant at 1000 until a reset at 6m and
target B constant at 1000 throughout. Correct per-target increases remain zero;
an aggregate 2000-to-1000 reset can invent an increase. Each histogram uses A
count/bucket 2000/1000 resetting together and B stationary at 2000/1000.
Infinite buckets equal their counts; sums describe realizable historical
observations. Both paired increases are zero, but aggregating first can invent
a positive count-minus-bucket increase. Check 5m, 6m, 11m, 19m, 34m and 36m to
cover the pre-reset state, immediate observation, pending, window and hold.
No concurrently growing target is allowed to hide this negative falsifier.

ORACLE-4: two simultaneous terminal-failure gauges equal one and a third equals
zero. After the existing one-minute pending period, expect the existing single
service-level alert without target labels. `max == 1` detects this state;
`sum == 1` does not. The previous handoff and recovery scenarios remain intact.

## Alternatives And Cost

Rules-only edits are cheaper locally but leave the missing-oracle requirement
unmet and move detection failures to integration. Existing promtool YAML
`alert_rule_test`, series sequences and anchors provide independent outcomes
without a custom evaluator, template framework or new runtime dependency.
Five separate warnings cost five predicates and their witnesses; merging them
with `or` would erase failure-stage or sample meaning and complicate response.
Per-instance paging would add notification identities and change existing
pending semantics; runbook drilldown costs only queries and preserves routing.

A ratio over `applied`, absence-as-failure, or a histogram-derived exact backlog
would be simpler text but unsound semantics. Additional metrics or a broker
would require cross-owner runtime, lifecycle, database and capacity proof with
no need for this bounded objective. Reopen if an existing owner-valid cheaper
rule preserves every listed distinction and has lower end-to-end proof and
incident cost. No global optimum is claimed.

## Writer Readiness

| Changed owner     | Intended delta and protected observations                                               | Derived surfaces and post-batch gate                                        | Independent validator                                          | Readiness                                         |
|-------------------|-----------------------------------------------------------------------------------------|-----------------------------------------------------------------------------|----------------------------------------------------------------|---------------------------------------------------|
| Rule YAML         | Append five warnings; preserve existing portfolio bytes and finite labels               | Promtool native check/test on final commit; all operands above              | Root's single batch reviewer under AGENTS.md and native runner | Ready to write; native acceptance remains pending |
| Fixture YAML      | Append named causal outcomes; retain predecessor scenarios                              | Exact deployed rule file via existing runner, no test-side reimplementation | Native promtool and independent oracle review                  | Ready to write                                    |
| Runbook           | Add sample-aware investigation and target drilldown; no receiver or remediation changes | Static link/ASCII inspection, root documentation gate and semantic review   | Root integration and batch reviewer                            | Ready to write                                    |
| New design / plan | Separate policy/rationale from execution; preserve existing design history              | Reachability through runbook, static scope check, final source rebinding    | Root integration and batch reviewer                            | Ready to write                                    |

No writer row depends on an unavailable native receipt before implementation.
New bytes invalidate old-epoch proof; root must bind the complete commit before
review and qualification. The writer runs no local behavior tests, containers,
database, browser or server, and does not publish or deploy. Warning delivery,
capacity, sustained progress, retention recovery and production reliability
remain unqualified.
