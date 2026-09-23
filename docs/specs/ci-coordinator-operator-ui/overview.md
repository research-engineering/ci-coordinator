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
