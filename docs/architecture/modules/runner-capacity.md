# Runner Capacity Module Specification

Status: module specification

Last updated: 2026-09-05

## 1. Owned Invariant

Shard and parallelism decisions optimize declared CI economics without changing
the selected validation coverage. This module applies only to
`witness-shards`; `native-job-set` retains repository-owned job topology and
does not read runner capacity.

## 2. Public API

```text
capture_runner_snapshot(plan_request, capacity_class_selectors) -> RunnerSnapshot | unknown
build_test_manifest(selected_witnesses, complete_test_inventory) -> TestManifest
optimize_shards(manifest, snapshot, policy, history) -> ShardPlan
```

## 3. Objective

```text
choose shard_count n minimizing:
  cpu_weight * expected_cpu_seconds(n)
+ wall_weight * expected_wall_time(n)
+ operator_weight * operational_complexity(n)

subject to:
  every selected test is assigned exactly once
  selected validation coverage is unchanged
  n <= policy.max_shards
  optimized max_parallel <= trusted positive free-slot evidence
  conservative max_parallel = 1
  expected_setup_overhead is bounded
```

## 4. Runner Snapshot

```text
RunnerSnapshot =
  observed_at
  source
  freshness_ttl_seconds
  canonical(capacity_class_id, free_slots)
```

The snapshot is evidence, not a reservation. The connected runtime projects
each witness-shard capacity class from its exact adapter workflow job to one
static `runs-on` selector, then reads complete bounded repository-runner,
repository-visible group, and group-membership inventories. It counts only
online, non-busy runners satisfying every admitted ASCII label and the optional
exact group.

Every visible group is read, including workflow-restricted groups, because a
repository runner response alone does not expose that restriction. Restricted
groups are excluded. Conflicting identity labels reject the snapshot;
transient state from repeated observations is combined conservatively with
online-AND and busy-OR. Ambiguous group membership, provider denial, incomplete
pagination, or intersecting capacity-class pools withhold affected
optimization. The observation time is captured before the first provider read.

```text
RunnerFreeAt(t0) does not imply RunnerReservedAt(t1).

MultiPageObservation does not imply AtomicProviderSnapshot.

A future-dated snapshot is not fresh evidence and therefore selects the same
conservative path as a stale snapshot.
```

## 5. Failure Behavior

```text
StaleCapacitySnapshot => conservative shard plan
UnknownCapacity => conservative shard plan
MissingDurationHistory => conservative shard plan
InvalidTestManifest => FullCI-compatible execution, not omission
MissingTestManifest => target authority retained, selected shards unavailable
```

The complete repository test inventory is validated before projection. Tests
for known unselected witnesses are excluded; unknown witnesses, duplicate test
identities, and missing selected-witness coverage are invalid.

The GitHub adapter reads the manifest from the exact request `head_sha` through
the Git commit, tree, and blob APIs. The manifest path must resolve directly to
a regular `100644` or `100755` blob. Contents API dereferencing, symlinks,
submodules, special objects, mutable refs, and path substitution are not
admitted:

```text
ManifestBytes(headSha, path)
and RegularBlob(headSha, path)
  => CapacityEvidenceMayBeParsed

not RegularBlob(headSha, path)
  => MissingTestManifest
```

This identity check does not prove test adequacy. It prevents a repository
symlink from moving the admitted inventory outside the diff-policy path that
owns manifest invalidation.

## 6. Proof Obligations

| Obligation                                       | Falsifier                                                                             |
|--------------------------------------------------|---------------------------------------------------------------------------------------|
| Capacity never reduces coverage.                 | Busy runners drop selected tests.                                                     |
| Tests are assigned exactly once.                 | Test omitted or duplicated without explicit replication policy.                       |
| More shards may be rejected.                     | Setup overhead makes high shard count cheaper by assumption only.                     |
| Unknown capacity is conservative.                | Missing snapshot increases shard count aggressively.                                  |
| Eligibility is exact within the admitted subset. | A missing label, restricted group, or unknown Unicode collation contributes capacity. |
| One runner is not double-counted.                | Overlapping capacity classes independently consume the same runner.                   |
| Freshness is not artificially extended.          | Snapshot time is captured after provider pagination.                                  |
| Manifest path identity is preserved.             | A symlink returns another file's bytes through the Contents API.                      |

## 7. Implementation Mapping

Current pure-core and conservative application files:

```text
ci_coordinator/runner_capacity/model.py
ci_coordinator/runner_capacity/manifest.py
ci_coordinator/runner_capacity/optimizer.py
ci_coordinator/runner_capacity/inputs.py
ci_coordinator/runner_capacity/evidence.py
ci_coordinator/runner_capacity/selector_binding.py
ci_coordinator/app/capacity_planning.py
ci_coordinator/repo_context/runner_selector.py
ci_coordinator/integrations/github/runner_capacity.py
ci_coordinator/integrations/github/runner_capacity_decoding.py
```

The GitHub integration implements the request-scoped provider under one total
deadline and fixed page, byte, group, runner, and occurrence bounds. Missing
organization runner-group permission, including an ambiguous `404`, returns
unknown rather than treating repository runner inventory as complete.

## 8. Acceptance Tests

- property test: every selected test assigned exactly once.
- full-inventory to selected-manifest round trip.
- unknown, duplicate, and missing selected-witness manifest rejection.
- capacity stale/unknown tests.
- static-selector, group-access, overlap, and provider-bound tests.
- cost counterexamples where more shards increase total cost.
- bounds tests for `max_shards`, `max_parallel`, and setup overhead.
