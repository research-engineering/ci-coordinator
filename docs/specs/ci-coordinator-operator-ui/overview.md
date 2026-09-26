# CI Coordinator Operator UI Specification

Status: active requirement package

Owner: `ci-coordinator.operator-ui`

## Purpose

This package owns the browser projection boundary for the operator workbench:
transport compatibility, runtime response admission, truthful state rendering,
secret exclusion, organization and repository catalog discovery, accessibility,
responsive interaction integrity, and exact-commit workflow evidence with
non-authoritative proposal disclosure. It also owns the browser projection of
Keycloak session state, proposal-bound GitHub repository attestation, explicit
config activation, and the bounded unbaselined projection of active
default-branch rules.
The supported container artifact embeds the admitted production bundle and
serves its shell and immutable assets from the backend origin; the development
Vite proxy is not part of that artifact.

Task navigation separates the organization catalog from a repository workspace.
Its closed view/tab coordinates, bounded retained-form lifetime and keyboard
contract are specified by `REQ-CI-UI-016`; the
[navigation design](../../features/operator-navigation.md) explains the choice.
The [layout refinement](../../features/operator-console-layout.md) retains a
compactable desktop sidebar and the native, unobscured organization selector.
The [catalog navigation refinement](../../features/catalog-navigation-ergonomics.md)
and [plan](../../features/catalog-navigation-ergonomics-implementation-plan.md)
bind explicit native-select chevron geometry and contextual pagination to
`REQ-CI-UI-005` and `REQ-CI-UI-007` without changing provider traversal.

## Authority

```text
requirements.v1.json
  -> proofkit/requirement-bindings.json
  -> frontend and connected-browser witnesses
  -> browser implementation
```

The feature design explains the chosen experience but does not create
additional normative requirements.

## Read And Command Lifetimes

The following projects `REQ-CI-UI-001`, `REQ-CI-UI-010`, `REQ-CI-UI-016`,
`REQ-CI-UI-021` and `REQ-CI-UI-024`; their requirement records remain canonical.
Repository/session authority `E`, a read owner's replacement revision `R`, and
the captured command `Q` have separate lifetimes. Changing `E` retires foreign
reads, callbacks and commands. Replacing a read rejects its older responses
without replacing a pending or uncertain `Q`; explicit retry preserves its
operation ID and reviewed payload under still-valid authority. Same-scope
navigation does not submit commands. Discovery identity contains its actual
request coordinates, not the workbench display limit or scope object identity.

Confirmed rollback and activation invalidate the shared workbench snapshot and
the independent enabled Configuration status read. Only an admitted response
from the current command owner can contribute an active-pointer lower bound;
activation additionally requires the response epoch to equal the captured
command target. Invalid responses remain uncertain and contribute no bound.
The maximum confirmed revision is retained: a missing or older active pointer,
or another epoch at that revision, cannot satisfy the next read. Contradictory
confirmed identities at one revision fail closed. A later admitted revision
may be shown. Registration invalidates reads without inventing an active pointer.

A recorded or duplicate activation is a historical receipt, displayed separately
from the observed current configuration. An unproved baseline is not admitted
absence. New verification uses a current admitted baseline; uncertain retry
keeps the original `Q`. Explicit refresh and fresh verification do not renew
expired manifest approval or guarantee activation. Backend receipt, expiry,
permission and concurrency admission remain authoritative; no polling or
automatic mutation replay follows from a receipt.

Archive list, selected jobs and detail share read invalidation. Manual refresh,
confirmed retention and relevant inactive/return read replacement retire
unsubmitted previews and stale cursors/responses, without remounting the pending
or uncertain command owner. A confirmed retention receipt is separate from
current reads; only its admitted non-null data revision can raise their lower
bound. A refused result with no preview is not deletion; detail deletion does
not erase statistics.
An uncertain retry retains the exact selection, digest and operation identity.

Under `REQ-CI-UI-002`, development callback forwarding follows the separate
[identity owner](../../architecture/modules/control-plane-identity-and-repository-attestation.md#8-http-projection)
query contracts, not a shared permissive callback rule. Under `REQ-CI-UI-014`
and `REQ-CI-UI-024`, local timeout/bound RangeError classification is network
failure, while malformed, cross-scope or oversized response evidence is invalid
response. Reusing classification does not change caller-specific abort
precedence or lifecycle cancellation handling; cancellation must not publish
stale state, start a download or escape as an unhandled rejection.

## Browser Support Contract

Blocking browser witnesses cover Playwright Chromium in three admitted
profiles: desktop Chrome, Pixel 7 touch/mobile emulation, and a 320 by 800
narrow reflow viewport. These profiles prove the checked interactions,
rendering, overflow, and accessibility assertions only for the bundled
Chromium provider.

Firefox, WebKit, other device/browser combinations, virtual-keyboard behavior,
orientation changes, and exhaustive assistive-technology compatibility are not
claimed. They require explicit product support requirements and corresponding
provider witnesses before they can become release gates.

## Non-Claims

This package does not prove backend authentication, authorization, atomic
review or activation persistence, deployment readiness, or provider
availability. Catalog visibility, a reviewable proposal, and a retained
attestation are not active repository configuration; active configuration is
still not provider enforcement or CI-omission authority.
