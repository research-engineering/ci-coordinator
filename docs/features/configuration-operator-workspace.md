# Configuration Operator Workspace

Status: implementation design; independent qualification pending
Date: 2026-09-13
Baseline: `3886bd244a651e8810b55e6a1dd4ff749a1fc25e`
Plan: [Configuration workspace plan](configuration-operator-workspace-plan.md)

## Scope And Authority

This design owns the administrator UI projection, not configuration policy.
The existing [API lifecycle](api-first-configuration-lifecycle.md), HTTP
`config_lifecycle_contracts.py`, query/command routers, `config_control` source
identity, and `config_epochs` replay/rollback contracts retain domain authority.
The assigned oracle obligation is `core.spec-test-code`: witnesses must match
those owners, not merely agree with the new implementation.

As-is: the backend validates and registers exact JSON/YAML source, lists scoped
epochs, exports retained bytes, and performs revision-safe rollback. Workflows
already owns proof-gated activation. The console lacks their configuration page.
Intended delta: a Configuration destination with Source and Retained epochs
views, explicit review/confirmation, typed failures, and a Workflows handoff.
Protected observations: API bytes/aliases, roles/CSRF, operation replay, rollback
coverage checks, reviewed activation, archive task retention and navigation.
No backend, existing design/plan, generated contract, or deployment changes.

## Minimum Sufficient Model

Disposition: reuse the API lifecycle model, extended with a UI transition model.
The change is material: it exposes authenticated durable writes and retained
source bytes. A happy-path component test alone cannot distinguish wrong-source
registration, stale export, or lost-response replay.

Let S be installation/repository, A be authority revision plus actor, roles,
CSRF and expiry, D be exact source text/format, V be an admitted validation,
Q be one read generation, P be the reviewed current/target epoch and revision,
and C be one immutable command including its operation ID.

```text
CanRegister = current(A) & configure(A) & validUtf8(D) & bounded(D)
              & V.scope=S & V.sourceHash=hash(D) & V.epochIdentityMatches
              & reviewed(D,V) & explicitlyConfirmed & noUnresolvedCommand
CanRollback = current(A) & configure(A) & activate(A)
              & currentRead(Q,S) & retained(P.target) & active(P.current)
              & P.target!=P.current & boundedNonblankReason
              & reviewed(P,reason) & explicitlyConfirmed & noUnresolvedCommand
ReadPublish = same(S,A,Q) & visibleView & admittedShapeAndIdentity
Replay = explicitGesture & current(A) & same(S,A,C) & exactRetainedCommand
```

Each conjunct is independent; remove or negate it in an isolated witness.
Registration/rollback proceed idle -> review -> pending -> complete/rejected/
uncertain. Uncertain retains C and blocks replacement, including after a later
failed replay. It has no timer retry. Navigation preserves C within S/A;
scope/authority changes unmount it and cancel obsolete work. Hiding a view
cancels reads and invalidates export/review without automatically replaying C.
Page reload does not retain commands; browser storage never contains sources,
CSRF tokens or commands. This is a deliberate same-task lifetime boundary.

## Representation And Transport

Use native textarea, file input, selects, tabs, tables and existing icons.
There is no new parser/editor framework. File admission preserves BOM and line
endings as bytes, rejects malformed UTF-8 and lone surrogates, and checks the
2,097,152-byte limit before allocation and after decoding. Editing invalidates
validation; registration uses the exact captured validation source/format.
Source and epoch identities use the existing domain-separated SHA-256 contract;
this checks binding, not policy compilation in the browser.
Preserving a file BOM does not admit it: the backend explicitly rejects a BOM
with `decode.byte_order_mark_forbidden`. Its diagnostic must remain visible;
the browser must not silently remove the bytes to make validation succeed.

Types come from Pydantic-generated `api/generated.ts`; strict runtime schemas
reject malformed hashes, unsafe integers, UTF-8 metadata overflow and extra
fields. Fetch uses the existing bounded transport, no-store, same-origin
credentials, current CSRF and response observation. Requests do not redirect.
The actual status cursor alias is `afterEpochId`; limit is at most 100.
Pages are independent live reads, not a concurrent snapshot. Refresh/navigation
invalidates the selection and review; active-pointer drift restarts pagination.

Export binds scope/request, epoch header, strong ETag, media type, exact byte
count, raw RFC 9530 digest and domain source hash before publishing a download.
Aborted/stale results never create a download; at most one object URL is retained
per selected epoch, and it is revoked on replacement or selection/view disposal.
No provider string is rendered as HTML.

## Activation And Rollback

Validation is not registration, and registration is not activation. This page
cannot mint `proposalManifestId`, proof or repository attestation. The next
valid activation action opens the existing Workflows review journey; arbitrary
uploaded policies cannot be activated unconditionally. No manual manifest input
or alternate activation route is added.

Rollback review displays full current and target epoch identity, revision and
reason. It is a local command preview, not a backend safety verdict. The server
still checks current revision and non-reducing coverage. A conflict requires a
fresh read and fresh review, not silent rebasing. A lost response retains the
original operation ID, expected revision, target and reason for explicit replay.

## Alternatives And Cost

Selected: a capability-local client/schema/identity boundary, one retained
workspace, source editing, epoch reads and a single serialized mutation hook.
This reuses transport and task lifetime while keeping policy in the backend.
Immediate cost is explicit admission and UI state; end-to-end cost is focused
wire/UI witnesses plus root integration/CI. Adding a generic query framework or
an editor library increases dependencies without satisfying an unmet contract.
Adding inputs to the existing activation control is cheaper locally but mixes
uploaded-source registration with proposal authority and has higher proof cost.
Revisit if API owner adds a reviewed arbitrary-source activation flow, durable
command recovery, snapshot pagination, or a measured editor requirement.

## Writer Readiness

| Changed owner                    | Delta and protected behavior                                               | Independent operands / isolated falsifiers                                                                                                                                                                | Derived surfaces and post-batch gate                                                                                              |
|----------------------------------|----------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------|
| `api/configLifecycle`            | Project existing wire contracts; preserve exact source and role boundaries | Scope, format, bytes, hashes, metadata, status, cursor, count, operation request, revision, headers; mutate each independently                                                                            | Native client witnesses using raw responses and Node crypto independent of browser helpers; static TypeScript; root exact-head CI |
| `features/configuration`         | Add source/epochs views and explicit writes; preserve authority and replay | Source edit after validate, review absent, stale read, missing role, expired session, lost response, changed reason/revision, late response, hidden view; each must disable publication/write or retain C | Component witnesses and browser accessibility/keyboard/mobile witnesses; root independent review and native CI                    |
| Workbench navigation/composition | Add Configuration and handoff; preserve retained archive tasks             | Sidebar/tab switches must not replay or lose C; scope/actor/CSRF/revision change must clear C                                                                                                             | Existing archive/navigation suite plus new workspace witnesses; root Activity integration                                         |
| Development proxy                | Admit only lifecycle methods, paths and actual query aliases               | Wrong method, unsafe scope, duplicate/unknown cursor or limit, out-of-range limit, foreign URL                                                                                                            | Proxy native witnesses and static typecheck; root API transport gate                                                              |
| New design and plan              | Specify only this UI delta; preserve existing document bytes               | No pre-existing design/plan changes; no stronger evidence claim                                                                                                                                           | Root documentation/index/requirements/traceability rebinding                                                                      |

These rows authorize the cohesive writer batch, not an affirmative result for
new bytes. Every semantic edit invalidates old-epoch proof. Root retains the
new `frontend-api.config-lifecycle` machine-profile registration, generated
OpenAPI/TypeScript currentness, proof routes, requirements, roadmap/index and
combined independent review. Their absence is a pending integration predicate,
not permission for this writer to edit governance.

## Qualification Boundary

Local work permits static formatting and no-emit TypeScript only. Native tests,
browser witnesses and their independent oracles are authored but not executed
here. Root must rebind the final source and qualify the integrated commit.
This is not complete roadmap delivery, provider, deployment or production proof.
