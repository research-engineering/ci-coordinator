# Governance, Drift, And Release Evidence

Status: effective-governance observation, durable baseline, and exact
comparison implemented locally; drift, enforcement invalidation, and release
evidence remain planned

Date: 2026-07-26

Owners: `governance_observation`, `governance_baseline`,
`governance_comparison`, `ci-coordinator.runtime`,
`ci-coordinator.operator-ui`

## 1. Decision

Governance delivery is an evidence dependency graph, not one indivisible
feature:

```text
authorized effective-state observation
  -> owner-approved durable baseline
  -> exact-state comparison
  -> policy-classified drift
  -> omission invalidation and governed remediation
  -> release-evidence projection
```

The observation, durable-baseline, and exact-comparison slices implement the
first three nodes as separate deterministic capabilities.
Observation preserves every active rule returned along one terminal bounded
GitHub pagination trajectory for the current default branch and labels the
result `unbaselined`. Baseline approval separately retains an explicit
owner-approved expected state without classifying current conformance.

```text
Observed(S, t) does not imply Baselined(S)
Baselined(S) does not imply Compliant(S, t)
Compliant(S, t) does not imply Enforcing(S)
Enforcing(S) does not imply OmissionAuthorized(S)
```

## 2. Observation Contract

For actor `A`, exact repository scope `S`, repository identity `R`, default
branch `B`, effective rule pages `P`, and observation `O`:

```text
TraversalObservation(A, S, O) :=
  Authenticated(A)
  and FreshReadAuthorized(A, S)
  and RepositoryBinding(S, R)
  and CredentialPlanePathConstructible(R, B)
  and BoundedTerminalRuleTraversal(R, B, P)
  and StrictCanonicalAdmission(P)
  and RepositoryBindingAfterRead(S, R)
  and O.stateDigest = Digest(CanonicalGovernanceState(S, R, B, P))
```

The two repository reads reject transfer, rename, identity, or default-branch
changes observed around the paginated rule read. They do not create provider
snapshot isolation or prove stable membership across pages; successful
consistency is therefore exactly `best_effort`. A terminal traversal proves
that every admitted next-page edge was followed, not that its union equals the
rule set at any single instant.

GitHub's
[effective branch-rules endpoint](https://docs.github.com/en/rest/repos/rules?apiVersion=2026-03-10#get-rules-for-a-branch)
is selected because it returns active rules across repository and organization
levels, excludes `evaluate` and `disabled` rules, requires only Metadata read
permission, and supports bounded pagination. The pinned provider version is
`2026-03-10`.

Each rule preserves:

- bounded rule type;
- source type, source, and ruleset id;
- byte-bounded canonical JSON for the complete provider rule object.

The canonical JSON keeps additive provider fields observable without assigning
them local semantics. Unknown or malformed structure, nonterminal pagination,
resource overflow, identity change, or provider failure yields a typed
unavailable result rather than partial or empty success.

## 3. State Digest

`stateDigest` excludes observation time and includes:

```text
schema id
provider API version
installation id
repository id
owner id
owner/name assertion
default branch
canonical ordered effective rules
```

Consequently equal admitted states produce equal digests, while identity,
branch, source, ruleset, parameter, or additive-field changes alter the
digest. The digest is comparison material, not a baseline or compliance
verdict.

## 4. UI Contract

The observation panel renders:

- exact repository and branch identity;
- observation time and `best effort` consistency;
- state digest;
- explicit `Observation only` status;
- active-rule count and canonical rule details;
- distinct loading, empty, forbidden, unauthenticated, unavailable,
  rate-limited, not-found, malformed, binding-mismatch, and network-failure
  states.

The separate baseline panel renders explicit absence or the complete approved
expected state and one same-origin approval or replacement control for an
authorized browser session. It distinguishes accepted, duplicate, unchanged,
stale, conflict, forbidden, unavailable, invalid-response, and network-failure
outcomes without presenting any of them as compliance.

Stale authority, scope, observation, baseline, comparison-read, or request
generations disable mutation and discard incomplete older work. Completed
exact-command receipts may remain as historical outcomes only under the same
non-secret authority revision, exact repository scope, and observation digest;
they cannot replace current query evidence or gain retry authority. CSRF proof
remains transport-only input and is not retained in receipt identity. Provider
text is inert. Neither panel exposes activation, enforcement, release, secret,
dispatch, or omission control.

## 5. Implementation Route

The execution order and acceptance gates for observation are owned by the
[governance-observation implementation plan](governance-observation-implementation-plan.md).
The durable-baseline node is owned by the
[governance baseline implementation plan](governance-baseline-implementation-plan.md).
The exact-comparison node is owned by the
[governance comparison implementation plan](governance-comparison-implementation-plan.md).
Dependent nodes require separate plans after their owner contracts are
accepted.

## 6. Capability Status

### Implemented: Durable baseline

Status: implemented locally.

Persist an owner-approved exact state digest and complete canonical state with
owner, reason, version, activation time, supersession, and retention.

The normative contract is the
[governance baseline module specification](../architecture/modules/governance-baseline.md).
Its execution order and closeout gates are owned by the linked implementation
plan.

### Implemented locally: Exact comparison

Compare one stable active baseline with one fresh best-effort observation using
exact canonical bytes. Emit only `matches` or `differs`, exact changed
coordinates, and exact added/removed rule counts. This node does not classify
policy drift or compliance.

### Planned: Drift

Compare current and baseline states, classify only through an admitted policy,
and treat unknown or stale evidence as blocking for dynamic omission.

### Planned: Release evidence

Bind source SHA, plan, policy, workflow inventory, governance baseline and
current state, selected and omitted checks, results, artifact digest, and
deployment target into one immutable release manifest.

## 7. Required Falsifiers

- denied authorization reaches GitHub;
- repository id, owner, name, owner id, or default branch changes across the
  read but success is returned;
- nonterminal or over-budget pagination becomes a successful observation;
- duplicate, malformed, non-scalar, unsafe-number, over-depth, over-node, or
  over-byte rule data is accepted;
- a provider additive field is silently lost from canonical rule bytes;
- state digest changes only because `observedAt` changed;
- a cross-scope response is rendered;
- empty active rules are labelled unprotected or compliant;
- an observation is labelled baselined, drift-free, enforcing, or
  omission-authorizing; or
- a credential, provider body, mutable token, or write capability reaches the
  browser.

## 8. Non-Claims And Revision Conditions

These implemented slices do not observe classic branch-protection details that
require Administration permission, inactive/evaluate rulesets, bypass actors
omitted by provider authorization, secrets policy, workflow bytes, or App
permission configuration. Baseline history is durable, but current compliance,
release readiness, provider enforcement, and CI omission safety remain
unproved.

Revise the selected endpoint if GitHub changes its effective-rule semantics,
Metadata permission is insufficient, or a cheaper provider projection closes
the same identity, terminal-traversal, and information-preservation
obligations.
