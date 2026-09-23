# Browser Session Recovery Implementation Plan

Status: implementation plan
Behavior owner: [design](browser-session-recovery.md), `REQ-CI-UI-009`
Baseline: `04773e69316dd6acf9046a3056493514d45735ea`

## Ordered Delivery

1. Add policy-free bounded-response subscriptions. Capture subscriber identity
   before I/O; notification cannot alter response classification. Identity
   admission selects only current same-origin cookie API 401 challenges.
2. Replace the terminal expiry transition with single-flight fresh admission.
   Fence expiry, challenge, logout, unmount and late responses. Preserve old
   authority invalidation, role checks and pending-write non-replay.
   Install response observation before descendant passive requests. A repeated
   challenge after budget exhaustion invalidates UI authority without a new
   automatic request. Keep sign-out failures operation-specific in the UI.
3. Add bounded same-tab return hints and SSO-loop protection. Wire automatic
   recovery only after fresh anonymous admission; keep explicit sign-in and
   service-failure retry. Restore only canonical repository/view coordinates;
   reading a malformed hint never clears its automatic-login veto.
4. Update the canonical requirement, proof routes, documentation index and
   roadmap status without rewriting pre-existing designs or plans.
5. Perform static checks and one independent review under `AGENTS.md`. Publish
   only the reviewed candidate. Run native frontend/component/browser proof
   through GitHub and require exact-head mandatory CI before squash merge.
6. Qualify post-merge CI, immutable release and owner-approved development environment deployment.
   Verify actual administrator login, expired-session recovery, safe return and logout;
   then resume the non-enforcing pilot target observation pilot. No pilot target workflow or
   omission change belongs to this batch.

## Writer Readiness And Oracles

| Owner and changed surface   | Protected behavior                                      | Independent falsifiers and gate                                                                                                                                                                                                                                                                        |
|-----------------------------|---------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Shared response observation | Bounded fetch output, timeout and cancellation          | Removed/replaced subscriber, aborted request, callback failure; native transport tests.                                                                                                                                                                                                                |
| Identity client and hook    | Fresh authority, distinct errors, no command replay     | Each origin/path/credential/status operand; concurrent 401, renewed cookie, failed session check, client-past expiry, logout/late success and unmount; native hook and existing app tests.                                                                                                             |
| Return navigation           | Fixed login target, no secret storage, no open redirect | Required/extra keys, bad types/version/age, oversize/foreign path/query, blocked storage, one-use hint, cooldown and explicit sign-in; native navigation tests.                                                                                                                                        |
| Production shell            | Correct wiring and accessible recovery                  | First descendant 401 is observed; a real pending command crosses expiry and one login navigation without replay, restored drafts or stale success. An independent no-command case forbids spontaneous writes. Strict fixture endpoints and content assertions; desktop/touch matrix and accessibility. |
| Requirements and routes     | One semantic owner and complete witness routing         | Exact changed-path admission, requirement/binding checks, docs graph, typecheck and native command closure.                                                                                                                                                                                            |

Keep positive controls for each rejected relation. A green route or mock session
does not close production login. Record native failures and reviewer findings
with their exact target; do not waive a baseline failure merely because the
changed code is frontend-only. A control review is reserved for material
remaining or newly introduced risk, not an unbounded review loop.

The pending-command browser oracle separates durable metadata from command
feedback. An original write commits enabled revision 1; a concurrent owner
advances the stored configuration to disabled revision 2. Fresh admission and
status read must display revision 2 before and after the delayed revision 1
reply, with exactly one browser write. A saved-revision label is not itself a
stale success message.

## Closeout Conditions

The source batch is complete only with consistent specification, implementation,
native oracles and required exact-head checks. Deployment is separate and must
bind the immutable image, running service and live browser observation. The
global roadmap remains open; historical Actions ingestion, analytics, dynamic
CI and production qualification are not implied by this repair.
