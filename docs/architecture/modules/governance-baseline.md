# Governance Baseline Module Specification

Status: as-built module specification

Date: 2026-07-26

Owner: `governance_baseline`

## 1. Decision

The module turns one freshly re-observed effective-governance state into an
explicit, durable, owner-approved baseline. It is a separate capability from
provider observation and drift classification.

```text
ApprovedBaseline(A, S, O, P) :=
  FreshReviewAuthority(A, S)
  and ExactSameOriginMutation(A)
  and CurrentObservation(O, S)
  and O.stateDigest = requestedStateDigest
  and ActivePointer(S) = P
  and AtomicAppend(Baseline(O, P), Audit(Baseline(O, P)), Operation(O))
```

The only valid downstream implication is:

```text
ApprovedBaseline(B) -> DurableExpectedState(B)
ApprovedBaseline(B) -/-> CurrentCompliance(B)
ApprovedBaseline(B) -/-> ProviderEnforcement(B)
ApprovedBaseline(B) -/-> OmissionAuthority(B)
```

## 2. State Algebra

For repository scope `S`, retained baselines form one append-only chain:

```text
Unbaselined(S)
  -> Active(S, version=1, supersedes=null)

Active(S, version=n, id=x)
  -> Active(S, version=n+1, supersedes=x)
```

The active baseline is the unique retained row with maximum version for `S`.
Every successor stores the exact predecessor id and version. No update or
delete operation exists; PostgreSQL rejects both.

Every completed command has one immutable operation receipt. An exact retry
returns its original `duplicate` or `unchanged` result without provider I/O.
Reusing an operation id with different actor, reason, requested state, or
expected predecessor is a conflict. A new command for state byte-identical to
the active baseline returns `unchanged` and creates one operation receipt but
no baseline row or audit event.

## 3. Approval Order

```text
fresh admin-or-maintain grant
  -> exact operation replay lookup
  -> active predecessor read
  -> predecessor equality
  -> fresh bounded provider observation
  -> requested digest equality
  -> prepared baseline-and-audit capability
  -> compatibility fence
  -> repository-scope lock
  -> operation replay revalidation
  -> predecessor revalidation
  -> atomic result append and commit
```

Authorization precedes store and governance provider access. Replay precedes a
new provider traversal. The provider state is re-read instead of accepting
browser-supplied rule bytes. The transaction revalidates the complete active
pointer after acquiring the same scope lock used by concurrent writers.

## 4. Baseline Identity

One retained baseline binds:

- installation and repository ids;
- baseline id and positive safe version;
- complete predecessor pointer or exact absence;
- provider API version and exact repository/default-branch identity;
- canonically ordered complete effective-rule state;
- exact canonical governance-state digest;
- best-effort observation instant;
- approving actor and bounded reason;
- operation id; and
- exact audit event id and input hash.

The baseline id is derived from the complete approval identity. Digest equality
is never used as a substitute for byte equality when deciding `unchanged`.

## 5. Persistence

`governance_baselines` and `governance_baseline_operations` are append-only.
The baseline primary scope-and-version index supports reverse active-head
scans. The operation primary key is the sole durable operation-id authority
for both accepted and unchanged commands. Database constraints enforce:

- safe positive scope and version values;
- canonical identity shapes and bounded bytes;
- version-one versus predecessor shape;
- predecessor version equals `version - 1`;
- same-scope predecessor and audit foreign keys;
- unique scope/version, scope/operation, baseline id, and audit event;
- bounded canonical command bytes and a same-scope result-baseline foreign key;
- exactly one `accepted` or `unchanged` result per scope and operation id; and
- immutable retained rows.

The domain and browser share one explicit reason profile: bounded UTF-8 scalar
text, no C0/C1 controls, and no leading or trailing code point from the fixed
Unicode whitespace boundary set. PostgreSQL independently enforces necessary
byte and ASCII-space bounds; canonical application admission remains the
semantic authority.

The runtime capability requires exact schema, codec, routine, data-domain, and
least-privilege facts. Before the first release, the complete schema is created
by one initial migration; no successor compatibility history is claimed.

## 6. HTTP And Browser Contract

The browser endpoint exposes:

- an authenticated read of the current active baseline or explicit absence;
- one CSRF-protected approval command bound to the displayed observation
  digest and complete active predecessor pointer; and
- bounded typed accepted, duplicate, unchanged, stale, conflict, forbidden,
  unauthenticated, and unavailable outcomes.

Baseline responses include the complete retained canonical state so runtime
admission can recompute its digest. They use `Cache-Control: no-store`.
Provider text remains inert. A stale authority, scope, observation, baseline,
or request generation cannot overwrite newer UI state.

## 7. Ownership

| Surface                                                                 | Responsibility                                                 |
|-------------------------------------------------------------------------|----------------------------------------------------------------|
| `governance_baseline/model.py`                                          | Command, pointer, record, and result algebra                   |
| `governance_baseline/codec.py`                                          | Canonical operation-command bytes                              |
| `governance_baseline/acceptance.py`                                     | Single-use baseline-and-audit capability                       |
| `governance_baseline/ports.py`                                          | Minimal authorization and persistence capabilities             |
| `app/governance_baseline.py`                                            | Authorization-first replay and fresh-observation orchestration |
| control-plane HTTP authentication                                       | Exact principal, role, and request-integrity admission         |
| `persistence/governance_baseline_*.py`                                  | Exact schema, transaction, codec, and attestation              |
| `api/http/governance_baseline_contracts.py`                             | Baseline request and response DTOs                             |
| `api/http/governance_state_contracts.py`                                | Shared governance-state HTTP projection                        |
| `api/http/routers/governance_baselines.py`                              | Raw mutation admission and bounded projection                  |
| `frontend/src/api/governanceBaseline/`                                  | Runtime response admission and transport                       |
| `frontend/src/features/workbench/GovernanceBaselineControl.tsx`         | Capability composition and request-generation ownership        |
| `frontend/src/features/workbench/GovernanceBaselineEvidence.tsx`        | Active-baseline evidence projection                            |
| `frontend/src/features/workbench/GovernanceBaselineApprovalControl.tsx` | Explicit approval command lifecycle                            |

The observation module remains free of persistence. The baseline module does
not acquire GitHub transport, HTTP, SQL, browser, or environment dependencies.

## 8. Rejected Alternatives

| Alternative                                          | Rejection proof                                                                                                      |
|------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------|
| Treat the first observation as an automatic baseline | Observation authority does not imply owner approval.                                                                 |
| Store only a digest                                  | A digest cannot reconstruct, inspect, or later compare the expected state.                                           |
| Accept browser-supplied rule bytes                   | Browser transport is not provider evidence authority.                                                                |
| Store baselines only as audit events                 | The audit payload bound is smaller, active lookup is not its owner, and state access would require unbounded replay. |
| Mutate one active row in place                       | It destroys supersession and exact historical evidence.                                                              |
| Add a mutable head table                             | Maximum append-only version provides the same active lookup without a second write authority.                        |
| Retain no-op ids in a separate table                 | Two relations cannot enforce one cross-table operation-id uniqueness constraint without a stronger shared authority. |
| Classify matching or drift in this slice             | Comparison and policy classification are dependent capabilities with distinct failure semantics.                     |

## 9. Required Falsifiers

- authorization denial reaches the store or governance provider;
- a replay performs provider I/O;
- a completed unchanged operation loses its exact result after a successor;
- one operation id denotes two commands or result kinds;
- stale observation or predecessor state is accepted;
- equal digests but unequal bytes are treated as unchanged;
- a cross-scope or non-adjacent predecessor is stored;
- two concurrent successors both commit for one prior version;
- accepted audit, baseline, and operation receipt publication separate;
- unchanged creates a baseline row or audit event, or omits its operation receipt;
- cancellation before commit is reported as accepted;
- retained bytes fail re-admission but are returned;
- update or delete mutates history;
- a browser response crosses scope or fails digest reconstruction but renders;
- an older request generation replaces newer evidence; or
- baseline approval is labelled compliance, enforcement, release readiness, or
  omission authority.

## 10. Non-Claims And Revision Conditions

This module does not prove a provider point-in-time snapshot, current baseline
conformance, policy-classified drift, provider enforcement, ruleset mutation,
workflow integrity, release readiness, CI omission safety, deployment, backup,
or production availability.

Revise the design if approval ownership changes from repository managers,
provider observations gain snapshot semantics, retention acquires a deletion
policy, or an admitted comparison contract requires a different baseline
identity.
