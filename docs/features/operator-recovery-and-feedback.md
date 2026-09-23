# Operator Recovery And Feedback

Status: implementation design for the independent recovery portion of B4.

Owner: `ci-coordinator.operator-ui`; requirements `REQ-CI-UI-001/002/003/005/006`
retain normative admission, privacy, state presentation, accessibility and
browser-verification authority. The [implementation plan](operator-recovery-and-feedback-implementation-plan.md)
owns execution and acceptance. This change does not complete the entire UI
roadmap or the separate webhook, logout and deployment work in B2.

## 1. Intended Behavior

An uncaught descendant rendering error currently removes the entire console.
Scope validation reports a generic error without associating individual
inputs with their errors. Evidence identifiers can require a hover tooltip to
inspect their full value. Correct these three presentation boundaries without
changing API admission, authentication, authority revisions or mutation policy.

| Boundary              | Intended delta                                                                                                                                                      | Protected observations                                                                                                    |
|-----------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------|
| Application rendering | A native React error boundary provides a static recovery screen and explicit same-URL refresh.                                                                      | No automatic mutation retry, credential storage, error-detail disclosure or success claim about an interrupted operation. |
| Scope input           | One Zod scope contract drives both transport admission and per-field validation. Errors have stable programmatic associations and a persistent announcement region. | The same positive safe ID and finite item-limit domain; no network request for invalid input.                             |
| Evidence identity     | Native disclosure exposes complete admitted identifiers to keyboard and touch users.                                                                                | Exact underlying identifier bytes and existing compact summaries; no interpretation as authorization or fresh evidence.   |

## 2. Recovery Without Hidden Authority

```text
RenderFailure -> StaticRecoveryScreen
StaticRecoveryScreen -> no automatic network or mutation effect
UserRefresh -> same URL -> fresh application/session admission
RenderFailure !=> PreviousMutationFailed
RenderFailure !=> PreviousMutationSucceeded
```

Use React's existing error-boundary lifecycle instead of another dependency or
a shared state store. Recovery refreshes the current document; it does not
attempt to infer or replay an interrupted mutation. Preserve the URL scope and
normal browser cookie custody. The fallback emits no raw exception, stack,
request payload or token. Event-handler errors, detached asynchronous errors,
failure before root mounting, and failures in the fallback itself are outside
this boundary; existing request outcome handling remains necessary.

React logs caught errors by default. Bind its native root `onCaughtError`
callback to a fixed diagnostic that consumes neither the exception nor the
component stack. This preserves a visible diagnostic without disclosing
provider payloads; it is not remote crash telemetry. See the official
[root error-reporting contract](https://react.dev/reference/react-dom/client/createRoot#error-logging-in-production).

A generic automatic retry boundary is rejected because remounting or replaying
an unknown operation can change remote state. A static error message without a
reachable recovery action is insufficient for an operator console.

## 3. One Scope Admission Predicate

For candidate scope `s`, preserve:

```text
Valid(s) = SafePositiveInteger(s.installationId)
  and SafePositiveInteger(s.repositoryId)
  and Integer(s.limit) and 1 <= s.limit <= MaximumSectionItems
```

Reuse the already admitted Zod dependency for these independent field
predicates. The transport retains its existing public input/output algebra and
returns the original candidate on success; unknown extra object properties do
not gain network semantics or become a new rejection condition. Rendering
errors must come from the same field schemas, not a second numeric policy.
Empty, fractional, negative, non-finite and unsafe integer inputs remain invalid.

Every field has a stable error target and `aria-invalid` state after validation.
The announcement region exists before its text changes. A correction clears
the corresponding error without submitting or changing repository scope.
Field labels, limits and keyboard ordering remain visible and predictable.

## 4. Inspectable Identifiers

Use native `details` and `summary` for values that are currently abbreviated.
Short values remain ordinary text. The disclosure shows the exact full value
with wrapping; opening it must not require hover, clipboard permission or a
custom popover lifecycle. Retain the existing abbreviation function as the
compact projection. Do not add cross-component selection or cache state.

## 5. Writer Readiness And Falsifiers

| Semantic owner    | Changed surfaces                                                     | Independent acceptance                                                                                                                                                                         |
|-------------------|----------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Console rendering | App boundary, recovery style and component witnesses                 | Descendant failure yields accessible fallback with no sensitive error text; healthy rendering remains unchanged; explicit refresh neither changes URL scope nor sends an application mutation. |
| Scope admission   | Existing workbench client, ScopeForm and tests                       | Parameterized domain boundaries; invalid fields are associated with their own messages; invalid input never calls submit; correction and valid input retain exact scope.                       |
| Evidence display  | Shared identifier control and existing workbench/economics consumers | Exact value appears through keyboard/touch disclosure and narrow reflow, while compact output is unchanged.                                                                                    |
| Derived routes    | Operator UI bindings and documentation navigation                    | Existing strict route, source-digest, documentation and ownership checks, followed by GitHub component/browser gates.                                                                          |

Local execution remains static-only. Chromium desktop, Pixel 7 emulation and
320-pixel reflow retain the admitted browser scope. Native component and browser
tests run in GitHub; accessibility scans alone do not prove complete assistive
technology compatibility. No new browser-support or production-readiness claim
is made.

Reopen the design if recovery must preserve unsaved drafts, navigation gains
history semantics, mutations become resumable, identifiers require privileged
redaction, or field admission gains cross-field constraints. Those changes can
invalidate the present simple boundary; they do not justify adding that
complexity in advance.
