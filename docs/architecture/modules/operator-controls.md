# Operator Controls Module Specification

Status: module specification

Date: 2026-07-17

## 1. Owned Invariant

`operator_controls` admits only authenticated, repository-scoped, audited state
transitions that preserve or increase validation.

```text
ControlAdmissible(c) iff
  ActorAuthorized(c.actor, c.kind, c.scope)
  and CommandShapeValid(c)
  and TransitionMonotonic(c, CurrentControl(c.scope))
  and StateAndAuditCommitAtomically(c)
```

No control can select tests, broaden credentials, publish provider success, or
create production authority.

## 2. Command Algebra

```text
force_full_ci(scope, subject, operation, actor, reason, expiry)
disable_omission(scope, operation, actor, reason)
enable_omission(scope, disabled_override_id, operation, actor, reason)
```

The commands form a closed discriminated union:

| Kind               | Scope                                        | Lifetime                           | Transition                                       |
|--------------------|----------------------------------------------|------------------------------------|--------------------------------------------------|
| `force_full_ci`    | one reconciliation subject in one repository | explicit aware expiry              | independent validation-increasing override       |
| `disable_omission` | complete repository                          | latched; expiry is forbidden       | admitted only when no disable is already latched |
| `enable_omission`  | complete repository                          | release event; expiry is forbidden | must name the exact latest retained disable id   |

The latch is deliberate:

```text
DisableApplied(scope) and not ExactEnableApplied(scope, disableId)
  => OmissionDisabled(scope)
```

Process restart, elapsed time, a new production receipt, or an unrelated enable
command cannot weaken the control. This is safer than expiry for a
repository-wide incident because time passage is not evidence that the incident was
resolved.

An operation identity is unique only inside exact
`(installation_id, repository_id)` scope. Independent repositories may reuse
the same caller-generated identity without aliasing.

## 3. Atomic Persistence And Replay

The HTTP route authenticates first and derives `actor`; no caller-owned actor
field exists. The application owner authorizes the exact action and scope. The
persistence owner then locks the repository scope and operation identity before
reading or changing state.

A successful transition writes the immutable override and its pair-owned audit
event in one PostgreSQL transaction. The generic audit repository cannot append
`operator_override_applied` independently. An unauthorized or expired command
is reported only after its rejection event is durably recorded; unavailable
audit persistence yields `unavailable`, not an unaudited decision.

```text
ExactOperationReplay => duplicate with original override and applied_at
DivergentOperationReplay => conflict and no write
UnknownCommitRetry => exact replay rules above
```

Server clock time is not part of caller operation identity. Therefore retrying
the same command later cannot create another event or change `applied_at`.

## 4. Planning And Issuance Projection

Planning resolves the current subject force and repository latch before
candidate reduction. Selected issuance repeats the decisive control check in
its repository-locked database transaction:

```text
ActiveForceFullCI(subject, now) or LatchedDisableOmission(scope)
  => SelectedIssuanceRejected
```

This second check is necessary. An application-only read could race a control
commit between planning and selected-envelope insertion.

A retained, unexpired force for the exact subject whose `applied_at` is later
than the reader instant makes override knowledge unavailable, not absent or
active. The latest unexpired force by application time and identity is sufficient
to detect that uncertainty. Unavailable knowledge preserves
the existing FullCI policy and rejects selected persistence under the same scope
lock. This does not change `applies_at`, durable timestamps, audit bytes or replay;
force commands expose an expiry, not a scheduled start time.

## 5. Policy Rollback Boundary

Config rollback is not an override command. It is owned by `config_epochs` and
is admitted only when the retained target epoch has equal or greater validation
coverage:

```text
RollbackAllowed(active, target) iff
  TargetEpochRetainedAndReadmitted(target)
  and CoverageRelation(target, active) in {equal, greater}
  and ActorAuthorized
  and ExpectedRevisionMatches
```

`less`, `incomparable`, stale revision, wrong scope, and unavailable proof all
reject without pointer mutation.

## 6. Failure Algebra

```text
UnauthorizedCommand -> audited forbidden
ExpiredForceFullCI   -> audited invalid override
InvalidLatchRelease -> conflict
ExactReplay         -> duplicate
DivergentReplay     -> conflict
PersistenceFailure  -> unavailable; no partial state/audit pair
```

## 7. Implementation Mapping

| Path                                          | Responsibility                                                                 |
|-----------------------------------------------|--------------------------------------------------------------------------------|
| `operator_controls/override.py`               | immutable command, override, and audit identities                              |
| `operator_controls/permissions.py`            | authorization port                                                             |
| `operator_controls/resolution.py`             | current-control projection                                                     |
| `operator_controls/use_cases.py`              | authorization and outcome orchestration                                        |
| `config_epochs/rollback.py`                   | rollback command, comparison port, and fail-closed lifecycle admission         |
| `planning_core/rollback_coverage.py`          | conservative validation-coverage relation provider                             |
| `persistence/operator_override_repository.py` | repository and operation locks, transition CAS, atomic state/audit persistence |
| `api/http/routers/operator_controls.py`       | authenticated discriminated HTTP contract                                      |
| `app/config_management.py`                    | separately governed config rollback orchestration                              |

## 8. Required Falsifiers

- a caller supplies its own actor or acts outside its admitted scope;
- a force-FullCI command without an aware future expiry is applied;
- a repository disable has an expiry or silently expires;
- enable omission does not name the exact active disable id;
- receipt renewal or restart releases a disable latch;
- equal operation ids alias across repositories;
- exact replay changes `applied_at` or appends another audit event;
- divergent replay mutates state;
- cancellation or conflict commits only one member of the state/audit pair;
- selected insertion races and bypasses a newly committed control; or
- rollback activates a coverage-reducing or incomparable epoch.

## 9. Non-Claims

Operator controls do not authenticate production evidence, rotate secrets,
configure GitHub required checks, choose a rollback artifact, or prove that an
incident is resolved. `enable_omission` records an authorized release of one
exact latch; it is not production admission by itself.
