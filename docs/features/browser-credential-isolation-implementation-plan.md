# Browser Credential Isolation Implementation Plan

Status: implementation and validation plan
Date: 2026-09-26

Design: [browser credential isolation](browser-credential-isolation.md).
Normative owners: [identity](../architecture/modules/control-plane-identity-and-repository-attestation.md),
[HTTP](../architecture/modules/api-http.md),
[settings](../architecture/modules/runtime-settings.md).
Execution remains under CI-054 in the [roadmap](../../ROADMAP.md).

## Ordered Work

1. Bind the current review/activation cutover before editing. Preserve V2 starts,
   replay-only legacy bodies, exact operation and baseline identity, and retained
   completed replay before fresh provider, clock or mutation work.
2. Author independent tests for exact cookie attributes and names, old-only and
   duplicate inputs, outer response cleanup, both finite secret equalities and
   strong distinct controls. Keep constructor/mapping errors and existing guards.
3. Change only settings contracts/admission, the two HTTP transaction setters
   and the existing app cleanup policies. Use a local pure finite equality
   predicate and native set/delete APIs, without a new cookie framework or state.
4. Extend the existing connected administrator authentication step. Capture the
   actual backend cookie and hold the actual provider form POST response with
   redirects/retries disabled. Exercise browser Domain rejection with an accepted
   carrier control, require victim denial, then complete the SAME unused callback
   in the original browser before the existing persisted-effect checkpoints.
5. Supply Node only the already mounted disposable CA for the held provider
   request; retain TLS verification and the current browser CA installation.
   Keep credential observations private, fixed diagnostic labels, one browser
   journey, all nine SQL checkpoints and the remaining budget/replay/logout path.
6. Update only current module and deployment contracts plus this new pair.
   Preserve pre-existing design/plan payloads. Root integrates existing requirement
   wording, exact routes/required tuples and derived hashes; no new requirement,
   schema version, migration, credential kind or task is needed.
7. Run only admitted static checks locally, freeze the source and obtain an
   independent review under [AGENTS.md](../../AGENTS.md). Behavioral qualification
   follows the [verification placement rule](../architecture/cross-cutting/testing-and-proofkit.md#6-required-gates-and-execution-placement)
   through exact-candidate GitHub gates, never a local test fallback.

## Causal Acceptance

| Case | Required independent observation |
| --- | --- |
| C1 | HTTPS and loopback HTTP have their exact distinct names, path, Domain absence, Secure, HttpOnly, Lax and existing lifetimes |
| C2 | Old-only and duplicate-current cookies reject before callback use-case/provider exchange; current-only and old-plus-current select exactly current authority |
| C3 | Success, classified failure, malformed input, overload, deadline and redacted defect retain one correctly scoped outer deletion; no second cleanup framework |
| C4 | Real service/crypto admission of a valid bundle and existing-session authentication survive unchanged key/profile recomposition; no provider token retention |
| C5 | Foreign reviewer session/actor/profile rejects before provider exchange with existing retirement; same binding and completed exact replay remain valid |
| C6 | B=W rejects via mapping and direct non-enforcing/enforcing construction, with and without optional identity; changing only B admits |
| C7 | B=canonical encoded S rejects through both paths with otherwise-valid distinct secrets; changing only B admits |
| C8 | Metrics/triple rejection priority, redaction, formats, optional identity, role/scope/action and public projection remain unchanged |
| C9 | Actual browser stores/sends the Domain control but not the backend-issued Host transaction; victim stays anonymous; SAME held callback succeeds for the original browser and one durable session |

The backend route fixtures alone are not browser cookie-store evidence. The
ordinary operator UI ASGI fixture has no identity dependency; do not use its
mocked API responses as C9 authentication proof. Pinned Playwright's redirect
interception limitation requires holding the provider form POST, not merely
registering a route handler on the redirected callback. Missing form/CA/redirect
premises block that oracle rather than justify weaker TLS or an invalid bundle.

## Handoff And Non-Claims

Record exact source hashes, protected replay/crypto/metadata bytes and actual
static results. Root must retain runtime 008/021/022 obligations and the connected
browser's native container route, not mislabel it as the ordinary browser cohort.
Schema and generated-client equality remain required even without an intended
wire delta. No deployment, real hostile origin, universal key separation,
production capacity or all-browser claim is authorized by this plan.
