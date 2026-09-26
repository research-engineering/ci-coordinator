# Explicit Review Renewal

Status: admitted design; native qualification pending
Date: 2026-09-26
Tasks: CI-054, CI-018
Owners: [proposal review](../architecture/modules/proposal-review-registration.md),
[identity](../architecture/modules/control-plane-identity-and-repository-attestation.md),
[config lifecycle](../architecture/modules/config-epoch-lifecycle.md)
Execution: [implementation plan](explicit-review-renewal-implementation-plan.md)

## Decision

A currently authorized human may explicitly verify the same proposal again
under a new operation identity. Each successful verification creates its own
immutable proof. Epoch content remains deduplicated by config_epochs, not by
review identity. No first-reviewer ownership, impersonation or implicit latest
receipt selection is introduced.

Activation requires the exact reviewOperationId, matching scope, manifest,
target epoch and original active baseline, unexpired evidence, the current
authority profile and a fresh GitHub maintain/admin recheck. Existing transport
roles, human-versus-machine separation, Origin/CSRF and provider boundaries stay.

## User-Visible Cutover

UI and API move together. New requests use ci-repository-attestation-start/v2
and ci-config-epoch-activation/v2. Completed legacy operations retain exact
replay; unknown legacy operations return legacy_new_operation_unsupported.
Unknown or null versions are malformed, never legacy fallbacks.

At the exclusive database cutover, only unconsumed verification challenges
are invalidated. Interrupted verification must be explicitly restarted.
Completed reviews, sessions, audit and epochs are unchanged. There is no
automatic resubmission, session extension, provider write or stored credential.
After an uncertain command, retry the identical operation and original
baseline before deciding to start a new verification.

The UI admits an activation result only when both its target and revision match
the command captured before transport. For a null original revision the result
is one; otherwise it is the original revision plus one. Exact duplicate replay
obeys the same equation even if the current workspace has since advanced.
A shape-valid contradictory result remains uncertain and cannot raise the
confirmed revision floor. Reuse the existing explicit identical-command retry;
no new wire field, server policy or automatic resubmission is needed.

## Identity and Replay

After ordinary authorization, retained operation lookup precedes fresh
baseline, provider, clock, pending or write work. Changed actor, scope,
manifest, baseline or client identity conflicts rather than inheriting a proof.

The generic envelope remains ci-audit-event/v1 and the pair-owned outer family
remains config-epoch-activation/v1. Only the activation payload gains
config-epoch-activation-audit/v2 with literal reviewOperationId. The new config
reader has three closed branches: legacy activation payload/v1 with wire/v1,
current activation payload/v2 with wire/v2, and unchanged rollback/v1.
Cross-generation replay and unknown/hybrid payloads fail closed. Old serializers,
hashes, authority projection and shared operation namespace remain exact.

Generic audit already rejects this outer family independently of its payload;
generic replay treats payload as bounded JSON. Activity's committed action does
not change. No audit-ledger/v2, new action, root table or generic version adapter
is needed. Persisting the selector in the existing audit pair is sufficient;
no activation-table selector column is necessary.

## Forward Compatibility

Two proper forward transitions retire proposal-review-registration/v1 and
config-epoch-lifecycle/v1, then add their v2 successors. Existing descriptor
meanings remain immutable. All unrelated capabilities, including audit-ledger/v1
and identity-state/v1, remain provided. Current requirement consumers rebind to
the exact new config/proposal descriptors without narrowing other dependencies.

The contract transition verifies its exact predecessor and pending schema,
deletes only pending challenges and requires an empty postcondition under the
existing exclusive compatibility fence. The expand transition drops only the
scope/manifest uniqueness constraint; operation, attestation and audit
uniqueness, foreign keys, immutable-history triggers and ACLs remain.
Existing migration execution holds both transitions in one outer transaction.
Failure rolls back the cutoff; stopping at the contract head is explicit
unavailability. Replaying an applied migration must not delete new challenges.

Old mint/callback/config UoWs require retired capabilities and cannot write
after the fence. A callback already doing provider I/O must independently
consume the exact pending row in its final transaction; deletion prevents an
old cookie from crossing into the new writer. New nonces cannot be consumed or
retired by an old transaction. Completed-operation replay runs through the new
reader, not through a spent OAuth code.

## Alternatives and Proof Boundary

Permanent manifest uniqueness prevents renewable proof. A root marker or
second table primarily preserves legacy NEW-request deduplication, which this
coordinated cutover deliberately rejects. Natural expiry adds clock/drain
assumptions and delay; accepted pending-only invalidation needs neither a new
generation nor a cryptographic rotation. Changing the outer event family would
needlessly enlarge the generic-writer fence and Activity surface.

Reopen for a different deployed old mint path, a published family-to-payload-only
contract, failed native fence/replay tests, or a migration exceeding existing
bounds. No unbounded cleanup or compatibility bypass follows from failure.
Source implementation and authored tests do not prove deployment, old artifact
inventory, migration duration, provider availability or native qualification.
