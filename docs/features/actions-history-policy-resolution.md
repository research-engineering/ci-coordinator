# Actions History Policy Resolution

Status: implementation refinement; public controls not yet enabled

Owner: `ci_economics`, with PostgreSQL authority owned by `persistence`.
This refines [retention](actions-history-retention.md) and
[storage](actions-history-storage.md); delivery follows the
[population implementation plan](actions-history-population-implementation-plan.md).

## Decision

Represent an explicit repository override or inheritance, not a field-level
merge. A null override means inheritance only; `forever` and `disabled` are
explicit, distinct policy variants. A singleton default has its own revision.
Repository configuration has a separate scoped revision. Reads return both
the configured source and the resolved policy reference.

```text
EffectivePolicy(repository, defaults) =
  override present ? (repository_override, configurationRevision, override)
                   : (service_default, defaultRevision, defaults.policy)

PolicyReference = (source, revision)
```

An imported detail record keeps the exact source, revision and full admitted
policy with its immutable first-import timestamp. A scalar version alone is
insufficient: default revision100 and repository revision4 are not ordered
versions of the same authority. Moving from one source to another cannot be
rejected merely because its revision is numerically lower.

Within one source, a lower revision is stale and the same revision must name
the same policy. Across sources, the current authorized configuration command
and locked resolution determine applicability. No reference grants write
authority without those checks. All records still expire under their applied
policy, not a newly resolved default.

Validate reference consistency before a lifecycle no-op: reaching the old
deadline or already being expired does not make a contradictory same-reference
policy admissible. Expiry still precedes applying any genuinely new policy, so
neither valid replay nor a longer duration revives expired detail.

## Transaction And Change Boundaries

Add one singleton policy relation rather than overloading repository identity
with a reserved numeric ID. Use the existing audit and operation-id mechanisms.
Configuration changes take the global policy/admission lock before a dataset
scope lock; detail imports resolve the currently admitted default under the
same ordering before their scoped transaction. Provider I/O holds neither lock.

A default change affects only inheriting repositories, and only future detail
imports unless an explicit existing-data operation was requested. A repository
override remains unchanged. The preview identifies affected inheritors and
their current configuration/data revisions. Existing-data application is
bounded and resumable; its exact target and policy reference are immutable.
A configuration change while application is pending invalidates the remaining
work rather than silently retargeting it. Already committed applications keep
their receipt and first-import time.

Prospective configuration changes do not need to rewrite every archived
attempt. Applying to retained detail uses the current detail row after locking,
first evaluates its old expiry, then applies the new reference. This prevents
a delayed cleanup worker from making logically expired data restorable.

```text
oldDeadline <= databaseNow -> expired remains expired
same source and same revision -> same policy
source changes -> compare current owner bindings, not cross-source integers
new policy applied -> firstDetailImportedAt unchanged
```

## Alternatives And Falsifiers

Copying defaults into every repository erases inheritance semantics and makes
a default change ambiguous. Sharing one global numeric sequence for every
repository policy adds contention without explaining policy origin. A tagged
reference is sufficient with the existing scoped command and CAS mechanisms;
it is not a new general-purpose version framework.

Native witnesses must cover override-to-default and default-to-override with
opposite numeric order, same-reference conflict, stale same-source revision,
prospective versus existing application, unchanged explicit overrides, a
changed inheritor set, stale preview, mid-batch revision changes and the exact
expiry boundary. Include import/default-change and cleanup/application races
in PostgreSQL. Pydantic admission alone does not prove any transaction order.

Reconsider this design only if policies acquire independent owners or rollout
epochs beyond the two declared sources, or measured global-lock contention
requires a weaker safe lock protocol. Keep existing active-evidence lifetime,
permanent statistics, provider authority and non-enforcing planning unchanged.
