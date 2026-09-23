# Governance Comparison Module Specification

Status: as-built module specification

Date: 2026-07-26

Owner: `governance_comparison`

## 1. Decision

The module compares one owner-approved governance baseline with one freshly
observed effective-governance state. It owns exact deterministic relation
evidence and no policy interpretation.

```text
ExactComparison(B, O) :=
  SameRepositoryScope(B.state, O.state)
  and StableActiveBaseline(B)
  and (
    relation = matches
      iff Encode(B.state) = Encode(O.state)
    relation = differs
      iff Encode(B.state) != Encode(O.state)
  )
```

The valid implications are deliberately narrow:

```text
ExactComparison(matches) -> EqualCanonicalStateBytes
ExactComparison(differs) -> UnequalCanonicalStateBytes

ExactComparison -/-> ProviderSnapshot
ExactComparison -/-> Compliance
ExactComparison -/-> PolicyClassifiedDrift
ExactComparison -/-> Enforcement
ExactComparison -/-> RemediationAuthority
ExactComparison -/-> OmissionAuthority
```

## 2. Why Comparison Precedes Classification

Let `P1` and `P2` be two admitted policies over the same exact comparison `X`:

```text
Classify(P1, X) may differ from Classify(P2, X)
```

Therefore classification is not part of the comparison truth function.
Conversely, every truthful classifier needs an exact subject relation before
it can interpret that relation. The dependency direction is:

```text
policy-classified drift -> exact comparison
exact comparison -/-> drift policy
```

This is the minimum boundary that preserves deterministic evidence while
leaving policy versioning, severity, waivers, and remediation to later owners.

## 3. Pure State Relation

The comparator consumes two exact `GovernanceState` values with one repository
scope. It compares the canonical bytes produced by the existing
governance-state codec. Digest equality is evidence but never substitutes for
byte equality.

The result contains:

- `matches` or `differs`;
- the exact baseline and current state digests;
- a canonical tuple of changed coordinates;
- exact added-rule and removed-rule counts.

Changed coordinates form this closed ordered set:

```text
api_version
repository.owner_id
repository.owner
repository.name
repository.full_name
repository.default_branch
rules
```

Rules are compared as sets of their complete canonical JSON bytes. A provider
rule whose parameters change is represented as one removed rule and one added
rule. The module does not infer a semantic rule identity or claim that the two
objects are one modified rule.

Scope inequality is an invalid invocation, not a governance difference.
Observation time is excluded because it is not part of governance-state
identity.

## 4. Read State Machine

For actor `A`, scope `S`, baseline reads `B0` and `B1`, and fresh observation
`O`:

```text
FreshReadAuthority(A, S)
  -> B0 := ReadActiveBaseline(S)
  -> O := ObserveCurrentGovernance(A, S)
  -> B1 := ReadActiveBaseline(S)
  -> require Pointer(B0) = Pointer(B1)
  -> Unbaselined(O) or Compared(B1, O)
```

Within the comparison service, the baseline store is read before
governance-state observation I/O, so store unavailability does not consume an additional
governance-state traversal. Browser grant acquisition is an independent
prerequisite and may already consume bounded provider capacity. The baseline is
read again after governance-state observation I/O so one response cannot combine
an observation with a baseline replaced during that operation.

The append-only baseline chain excludes ABA:

```text
Active(version=n) -> Active(version=n+1)
Active(version=n+1) -/-> Active(version=n)
```

Therefore equal exact pointers before and after the observation prove that no
baseline replacement committed during the bounded read interval. No database
lock is held across provider I/O.

An absent baseline still permits the existing provider observation because the
workbench must present evidence needed to approve the first baseline. Absence
before and after observation yields `unbaselined`; a transition in either
direction yields `stale`.

Fresh read authority precedes every baseline-store and governance-state reader
access. Acquiring that authority may itself require bounded provider I/O. Store
or provider unavailability, provider rate limiting, malformed evidence,
repository-binding failure, and observation-limit exhaustion remain typed
non-success outcomes. Cancellation propagates and cannot become a successful
comparison.

## 5. HTTP And UI Contract

One read-only endpoint, also used by the same-origin browser UI, projects:

- exact repository scope;
- the complete current `best_effort` observation;
- an explicit absent or complete active baseline;
- `unbaselined` or `compared` state; and
- for `compared`, the exact relation, coordinate ledger, and set-delta counts.

The response is `Cache-Control: no-store`. It contains no credentials, provider
request body, policy verdict, severity, mutation command, or omission control.

The browser transport admits at most 32 MiB. Let `A = 2,097,152` be the
aggregate canonical-rule byte bound, `N = 1,000` the rule-count bound, and
`M = 1,537` the combined direct rule-metadata byte bound. Outer JSON escaping
expands already-canonical rule JSON by at most `2A`; direct scalar metadata by
at most `6NM`; closed framing, repository identity, timestamps, pointers, and
record metadata remain below 1 MiB per state projection. Therefore:

```text
2 * (2A + 6NM + 1 MiB) < 32 MiB
```

The previous 8 MiB single-state budget is not closed under composition and is
therefore invalid for this endpoint.

The frontend admits the complete response at runtime, recomputes both state
digests, requires exact scope and baseline-pointer binding, and independently
recomputes the comparison projection before rendering. A refresh may keep prior
evidence visible but disables mutation until the new generation settles.
Authority, scope, observation, baseline, or request changes abort and discard
older incomplete mutations. A completed exact-command receipt may remain as
historical evidence only under the same non-secret authority revision, exact
repository scope, and observation digest; it cannot replace current comparison
evidence or gain retry authority under a new binding. The CSRF token remains a
transport-only input and is not retained in receipt identity.

The composed endpoint intentionally requires an admitted Keycloak human or
workload principal with the exact `read` role because it projects the
owner-approved baseline and shares a surface with browser mutation controls.
Break-glass authority is not admitted by this route. The
standalone observation endpoint remains the narrower read-only degraded path
and does not expose baseline or comparison evidence.

The UI uses informational treatment for `matches` and warning treatment for
`differs`. It labels both as exact comparison only and never renders
`compliant`, `drift-free`, `protected`, `enforcing`, or `safe to omit`.

## 6. Ownership

| Surface                                                            | Responsibility                                                      |
|--------------------------------------------------------------------|---------------------------------------------------------------------|
| `governance_comparison/model.py`                                   | Closed exact-comparison result algebra                              |
| `governance_comparison/comparison.py`                              | Pure canonical-byte and exact-set comparison                        |
| `app/governance_comparison.py`                                     | Authorization, baseline revalidation, and observation orchestration |
| `control_plane_identity` and HTTP authentication                   | Exact principal and read-role admission                             |
| `api/http/governance_comparison_contracts.py`                      | Bounded response DTO and projection                                 |
| `api/http/routers/governance_comparisons.py`                       | Authentication and HTTP outcome mapping                             |
| `frontend/src/api/governanceComparison/`                           | Runtime response admission and transport                            |
| `frontend/src/features/workbench/GovernanceComparisonEvidence.tsx` | Exact relation presentation                                         |

`governance_comparison` is an in-process capability boundary, not a service or
deployment boundary. It may depend on public governance-state and baseline
contracts plus kernel primitives. Neither source context may import it. The
Python import-boundary gate rejects unadmitted first-party edges plus direct
framework, HTTP-client, process-environment, OS-capability, and SQL-framework
dependencies from this domain package. Known standard-library dynamic loading
and reflective built-in authorities are rejected because they would make the
static dependency graph incomplete. The executable gate derives both rule
coverage and scanned files from one no-follow source inventory. It requires a
bijection between every Python-bearing regular or namespace `governance_*`
package and one exact context rule that applies to every actual Python file in
that package. Any symlink in the scanned source tree is rejected before rule
evaluation rather than followed or omitted. This obligation does not depend on
pytest execution.

This is a conservative static source-policy witness, not a Python runtime
sandbox. It proves that admitted source bytes contain none of the recognized
dependency or loader authorities; it does not claim control over code generated
after admission, interpreter modification, or dependencies executed outside
the inventoried source tree.

## 7. Rejected Alternatives

| Alternative                            | Rejection proof                                                                              |
|----------------------------------------|----------------------------------------------------------------------------------------------|
| Compare digests only                   | Equal digests do not replace the required exact-byte authority.                              |
| Compare only in the browser            | Two independently loaded client responses do not prove one server-authorized baseline epoch. |
| Hold a database lock during GitHub I/O | Pointer revalidation closes the baseline race with less contention and coupling.             |
| Persist every comparison               | The result is deterministically reproducible and has no current durability consumer.         |
| Pair rules by selected metadata        | Current admission does not prove that metadata tuple is a unique semantic identity.          |
| Classify policy drift in this module   | Different policies can classify the same exact relation differently.                         |
| Add a scheduler or provider mutation   | Neither is required to answer the read-only comparison question.                             |

## 8. Required Falsifiers

- denied authorization reaches the baseline store or governance-state reader;
- store failure still performs governance-state reader I/O;
- a baseline replacement during observation returns a comparison;
- absent-to-active transition returns `unbaselined`;
- equal digests with unequal canonical bytes return `matches`;
- unequal scope is treated as a governance difference;
- observation time alone changes the relation;
- additive provider fields disappear from exact comparison;
- changed rule bytes are reported as a semantic modification rather than exact
  removal and addition;
- provider or store failure becomes `differs`;
- a cross-scope, stale, malformed, or internally contradictory response renders;
- a valid composed response above 8 MiB is rejected by the browser;
- non-forbidden access failures collapse into `forbidden`;
- a refresh leaves an older baseline-bound mutation active or renderable;
- `matches` is labelled compliance, protection, enforcement, release
  readiness, or omission safety; or
- comparison creates durable state, provider writes, or an audit claim.

## 9. Non-Claims And Revision Conditions

This module does not prove provider snapshot isolation, stable rule membership
across pagination pages, classic branch protection, policy intent, compliance,
severity, continuous monitoring, remediation, provider enforcement, release
readiness, CI omission safety, deployment, or production availability.

Revise the design if the provider offers a stronger admitted snapshot contract,
baseline history becomes mutable, a durable comparison consumer is accepted,
or an owner-approved policy requires a different exact evidence projection.
