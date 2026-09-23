# Authority Transition Safety

Status: governing conditional architecture contract

Date: 2026-08-31

## 1. Decision

An authority-transfer mechanism is implemented only when its failure mode is
reachable in the current product. The revision condition is documented before
it becomes reachable.

For mechanism `M`, trigger `T`, protected authority `A`, and counterexample
set `C`:

```text
RequiredNow(M) :=
  Reachable(T)
  and exists c in C: CurrentControls and c permit an invalid A transition

Implement(M) only if RequiredNow(M)
DocumentRevisionCondition(M) even if not Reachable(T)
```

This rule preserves both safety and architectural economy. Omitting a required
mechanism admits a known counterexample. Implementing an unreachable mechanism
adds state, persistence, recovery, and proof obligations without protecting a
current observable.

The current dispositions are:

| Mechanism                             | Activation predicate                                                                                                                           | Current state                      | Disposition                                                  |
|---------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------|--------------------------------------------------------------|
| Per-copy durable conservation         | Semantic authority moves between durable representations or a removal claim covers live data, WAL/archive, backup, quarantine, or key bindings | false                              | Specify the trigger; do not implement copy tracking          |
| Ambiguous remote-effect lifecycle     | The coordinator can issue a target-changing provider operation whose applied result can be unknown                                             | false                              | Specify the trigger; do not implement an effect engine       |
| Independent target-authority relation | Native FullCI authority may be transferred to selective omission for a target repository                                                       | reachable at enforcement admission | Implement before any production omission authority is issued |

The predicates are evaluated independently. Activating one does not activate
the other two.

## 2. Durable Representation Transfer

### 2.1 Activation

This contract becomes applicable when any of these conditions is true:

- one authority-bearing value has concurrent old and new durable
  representations;
- a table, store, partition, or encryption domain becomes the new semantic
  owner;
- a destructive migration retires a reader or representation;
- a deletion, preservation, or crypto-shredding claim includes backup,
  WAL/archive, quarantine, or key-binding copies; or
- restore or failover is claimed to preserve an authority transition.

Ordinary additive schema migration is not sufficient to activate the model.
The current database compatibility protocol already governs additive and
contract revisions, but explicitly does not claim backup or failover
correctness.

### 2.2 Required future proof

Let `K` be the owner-admitted copy-kind set and `Copies(k, e)` the independently
inventoried copies of kind `k` at epoch `e`:

```text
TransferComplete(old, new, e) :=
  ExactCopyKindSet(K, e)
  and forall k in K:
    ConservationOrAuthorizedDeletion(Copies(k, e), old, new)
  and RestoreWitnessMatches(new, e)
  and KeyBindingsMatchEveryRetainedCopy(e)
  and NoUnknownCopyKind(e)
```

The minimum copy kinds are not hard-coded by the application. They are owned
by the deployment and data platform for the exact environment. A PostgreSQL
adapter may prove live-row and migration mechanics; it cannot attest external
backup inventory or key custody.

`unknown`, an absent copy-kind inventory, or a restore witness from another
epoch rejects the transfer. It does not authorize destructive cleanup.

## 3. Target-Changing Provider Effects

### 3.1 Activation

This contract becomes applicable before the first repository-changing GitHub
operation, including workflow dispatch, rerun, cancellation, check
publication, ruleset or branch mutation, issue mutation, or content write.
Read-only provider requests and installation-token or OAuth credential exchange
do not activate it because they do not themselves change target business
state.

### 3.2 Required future state machine

For one immutable effect subject and generation:

```text
Proposed -> Reserved -> Applying
Reserved -> ClosedNotApplied
Applying -> Applied
Applying -> Rejected
Applying -> Uncertain
Uncertain -> ReconciledApplied
Uncertain -> ReconciledAbsent
ReconciledAbsent -> Reissued(nextGeneration)
ReconciledAbsent -> ClosedNotApplied
```

The durable reservation precedes provider I/O. The `Reserved -> Applying`
transition is a fenced compare-and-swap that commits before it issues one
durable, initially unconsumed capability bound to the exact effect, generation,
revision, attempt, and holder. `Reserved -> ClosedNotApplied` competes for the
same prior revision and atomically makes issuance impossible. The provider
adapter must then win a second durable compare-and-swap that consumes that
capability before its first provider I/O. Therefore no provider call can start
from `Reserved`, and at most one caller can cross the provider-I/O boundary for
one attempt.

```text
ProviderIOAllowed(e, g, r, a, h, c) :=
  DurableState(e, g) = Applying
  and r = CurrentRevision(e, g)
  and c = CurrentApplyingCapability(e, g, r, a, h)
  and not Revoked(c)
  and ConsumeApplyingCapabilityCAS(
    e, g, r, a, h, c, unconsumed, consumed) = won

ApplyingCapabilityConsumedAtMostOnce(e, g, r, a, c) :=
  CountSuccessfulConsumeCAS(e, g, r, a, c) <= 1

ReservedToApplyingCAS and ReservedToClosedNotAppliedCAS
  compete on the same prior revision
```

A consumed capability is never reusable or reissued within its generation. A
crash after capability consumption, including the cut before the first provider
I/O, leaves a durable `Applying + consumed` record; recovery must classify it as
`Uncertain` because local state cannot distinguish pre-I/O from post-I/O crash.
A timeout, lost response, crash, or ambiguous provider result likewise
transitions to `Uncertain`; it never returns to `Reserved` by local inference.
`Rejected` requires an authoritative provider rejection. `ClosedNotApplied` is
terminal and is allowed only after the pre-I/O close CAS wins or after
authoritative no-effect reconciliation that also proves the prior egress can no
longer produce an effect. An observation that the effect is absent at one
instant is insufficient: a previously admitted request may still be delayed in
the transport or provider. Retry exhaustion without both absence and
prior-egress finality remains `Uncertain`; it cannot manufacture a terminal no-effect
result. Reissue requires the same closure and a strictly greater generation.

```text
NoFutureEffectFromGeneration(e, g) :=
  TerminalProviderRejectionForExactRequest(e, g)
  or ProviderAcknowledgedIrrevocableCancellation(e, g)
  or (
    ExactProviderIdempotencyOrFencingContract(e)
    and EveryGenerationUsesSameLogicalEffectIdentity(e)
    and LateRequestFromGenerationCannotAddAnotherEffect(e, g))

ReconciledAbsentAllowed(e, g) :=
  State(e, g) = Uncertain
  and AuthoritativeNoEffectEvidence(e, g)
  and PriorEgressIrrevocablySettledOrCancelled(e, g)
  and NoFutureEffectFromGeneration(e, g)

ReissueAllowed(e, g + 1) :=
  State(e, g) = ReconciledAbsent
  and AuthoritativeNoEffectEvidence(e, g)
  and PriorEgressIrrevocablySettledOrCancelled(e, g)
  and NoFutureEffectFromGeneration(e, g)
  and FencedGeneration(e) = g + 1
```

The default recovery profile provides no automatic reissue. Provider
idempotency keys enable reissue only when the admitted provider contract proves
the exact logical-effect identity, at-most-one effect semantics across all
generations, and behavior of requests that arrive after reconciliation.
Process termination, local socket close, elapsed time, retry exhaustion, or an
eventually consistent absence read does not by itself prove egress finality.

## 4. Target Authority Relation

### 4.1 Protected transition

The current applicable transition is:

```text
native FullCI owns validation completeness
  -> production-admitted selective plan may omit registered work
```

The existing target registry and exact-revision adapter admission protect the
runtime snapshot that is already registered. They do not prove that the
registration itself contains every authority-relevant workflow, job, profile,
and provider gate present before the handoff.

### 4.2 Phase-zero baseline and independent inventories

Let:

- `B` be the content-addressed, owner-admitted, non-empty Phase-0 inventory of
  native validation authority before adoption;
- `D` be the owner-approved transition relation whose rows classify each
  baseline member as retained, replaced, or retired and each new member as an
  authorized introduction;
- `U = Apply(B, D)` be the non-empty expected, totally classified target
  relation after the transition; every row is explicitly `authority` or
  owner-approved `non_authority`;
- `R` be the registration producer's complete inventory of `U`; and
- `O` be the observation producer's independently complete inventory of `U`.

The Phase-0 baseline is captured before target adaptation. It binds the exact
repository, provider, source, policy, and owner epochs and is not generated
from `R`, `O`, or the adapted target. Every baseline row has exactly one
owner-approved disposition. Every retained or replacement successor is
attributed by full row, every introduction has an explicit owner approval, and
every retirement has an explicit reason and authority. Unknown dispositions,
unmapped baseline rows, unapproved introductions, and key collisions reject
the transition.

```text
BaselineClosed(B) :=
  OwnerAdmitted(B)
  and IndependentlyInventoriedNativeDomain(B)
  and NonEmpty(Keys(B))
  and ExactPreAdoptionEpoch(B)
  and UnknownCount(B) = 0

DeltaClosed(B, D, U) :=
  U = Apply(B, D)
  and EveryBaselineRowHasExactlyOneDisposition(B, D)
  and EverySuccessorHasAttributedPredecessorOrApprovedIntroduction(D, U)
  and EveryRetirementIsAuthorized(D)
  and NonEmpty(Keys(U))
  and UnknownCount(D, U) = 0
```

This conservation relation prevents two incomplete post-adoption producers
from agreeing on the same omission. Equality of `R` and `O` is insufficient
unless both are independently proved complete against `U`, `U` is itself a
closed transformation of `B`, and every member discovered in the raw target and
provider domains has exactly one row in `U`.

```text
Independent(R, O) :=
  ProducerIdentity(R) != ProducerIdentity(O)
  and Keys(O) are not enumerated from R, U, D, or B
  and SourceEpoch(R) = SourceEpoch(O)
  and Subject(R) = Subject(O)
```

Different process identities alone are insufficient if both enumerate the
same registration keys. Shared immutable source bytes are permitted; key-space
derivation must remain independent.

The registration producer derives owner-intended authority from the approved
transition relation, validation catalog, target execution registry, and
consumer-lab scenarios. The observation producer independently enumerates the
exact Git tree and workflow closure plus provider required-check and run
evidence for the same post-transition target epoch. Its raw candidate set is
derived before comparison and without accepting `B`, `D`, `U`, or `R` keys as
enumeration input.

The complete workflow domain is not limited to registry adapter files. The
observation producer emits two separately domain-separated values:

- a stable `ci-coordinator.workflow-authority-manifest/v1` containing the exact
  repository identity, admitted Git object format, workflows-subtree object id,
  and canonical rows sorted by repository-relative path for every entry under
  `.github/workflows`; and
- an epoch-specific `ci-coordinator.workflow-source-binding/v1` provenance
  receipt proving that one exact source commit resolves through its root and
  ancestor tree objects to that stable workflows-subtree object and manifest
  digest.

Each stable manifest row contains path, Git mode, object type, object id,
provider-declared size or `null`, observed byte count or `null`, and blob
SHA-256 or `null`. Source commit, root tree, and ancestor tree identities are
provenance fields and are deliberately excluded from the stable manifest.

Regular blobs require non-null equal declared and observed sizes, retained
exact bytes, a SHA-256 over those bytes, and a recomputed Git object id under
the admitted repository object format. Tree rows require the three byte fields
to be `null`, a complete terminating traversal, and an object id recomputed from
the exact Git tree serialization of all direct children. The verifier
recursively recomputes every retained tree object and verifies the bounded
ancestor-object chain from the provider-bound source-commit root tree to the
workflows subtree. Symlinks, gitlinks, special or unknown modes, unknown object
types, duplicate paths, missing descendants, object-id mismatch, inconsistent
nullable fields, or a commit/root/subtree chain mismatch reject the manifest.
Provider blob or tree metadata alone is insufficient.

GitHub's Git commit endpoint returns a structured commit projection and does
not expose the exact raw object bytes for every commit; in particular, the
signature payload is null for an unsigned commit. The source-binding contract
therefore does not claim to recompute a generic commit object id from bytes the
provider did not return. It instead admits the minimum provider trust already
required by the product: one authenticated, version-pinned, bounded Git commit
response must return the exact requested commit id and one root-tree id. Tree
and blob identities below that provider-bound root remain independently
recomputed from complete retained evidence. This distinction follows the
[Git commit endpoint](https://docs.github.com/en/enterprise-cloud@latest/rest/git/commits?apiVersion=2026-03-10)
and [Git tree endpoint](https://docs.github.com/en/enterprise-cloud@latest/rest/git/trees?apiVersion=2026-03-10)
response contracts.

```text
RecomputedTreeOID(t) := GitObjectId(
  objectType = "tree",
  content = ExactGitTreeSerialization(AllDirectChildren(t)))

forall t in RetainedTreeObjects:
  DeclaredObjectId(t) = RecomputedTreeOID(t)

ValidWorkflowSourceBinding(e, W, M) :=
  ExactAuthenticatedCommitRequest(e)
  and ProviderCommitResponseId(e) = RequestedSourceCommitId(e)
  and ProviderCommitResponseRootTreeOID(e) = SourceCommitRootTreeOID(e)
  and BoundedCanonicalCommitBindingEvidenceRetained(e)
  and WorkflowsTreeOID(W) = ResolveVerifiedTreeChain(
    SourceCommitRootTreeOID(e), [".github", "workflows"])
  and StableManifestDigest(W) = M = WorkflowAuthorityManifestDigest(e)
```

```text
WorkflowAuthorityManifestDigest(e) := SHA256(
  ASCII("ci-coordinator.workflow-authority-manifest/v1\0")
  || CanonicalJsonV1(StableWorkflowAuthorityManifest(e))
)
```

`CanonicalJsonV1` uses UTF-8, exact integer and string domains, sorted object
keys, path-sorted rows, and no insignificant whitespace. Implementations must
bound path count, path bytes, blob and tree-object bytes, aggregate bytes,
nesting, ancestor-chain length, and provider output before decoding or retaining
evidence.

The exact commit id remains provenance for the observation. It is not itself a
stable authority identity. A non-CI source commit with unchanged target-policy,
catalog, registry, and provider epochs may retain the relation receipt after a
new source-binding receipt proves the same stable workflow digest. Any workflow
add, removal, mode change, type change, or content change changes the stable
digest and requires a new relation proof.

### 4.3 Full-row closure

An authority row is domain-specific and versioned. Its key identifies the
relation member; its value contains every field whose change can alter
execution, omission, or provider conclusion. At minimum, the admitted row
families cover:

- workflow path, exact content identity, trigger surface, and active state;
- job identity, role, complete `needs`, execution kind, and profile set;
- validation profile, semantic owner, and owner-reviewed evidence identity;
- stable provider gate, required-check identity, and merge-queue relation; and
- explicit `not_applicable` or `unknown` state for conditional fields.

Let `T` be the independently enumerated raw candidate domain: every regular
workflow blob and every workflow or job parsed from it; every versioned target
policy, validation-catalog, execution-registry, and profile member; and every
provider required-check, ruleset, merge-queue, and stable-gate member for the
exact subject and epoch. Invalid parsing rejects the candidate domain; it does
not remove a member. `RawDomainClosed(T, U)` requires each candidate to map to
exactly one row in `U`, and each row to carry either authority semantics or an
explicit owner-approved non-authority disposition. Structural tree rows prove
manifest completeness but are not themselves semantic candidates.

```text
RawDomainClosed(T, U) :=
  IndependentlyEnumerated(T)
  and CompleteTargetAndProviderDomain(T)
  and T = DeterministicCandidates(
    StableWorkflowAuthorityManifest(SourceEpoch(T)),
    CompleteTargetArtifactInventory(TargetArtifactEpoch(T)),
    CompleteProviderAuthorityInventory(ProviderEpoch(T)))
  and ValidWorkflowSourceBinding(
    SourceEpoch(T),
    StableWorkflowAuthorityManifest(SourceEpoch(T)),
    WorkflowAuthorityManifestDigest(SourceEpoch(T)))
  and EveryRegularWorkflowBlobHasExactlyOneParseOutcome(T)
  and forall t in T: exists exactly one u in U: Projects(t, u)
  and forall u in U: exists t in T: Projects(t, u)
  and forall u in U:
    Disposition(u) in {authority, owner_approved_non_authority}
  and UnknownCount(T, U) = 0
```

Equality is canonical full-row equality, not ID or digest equality without
available attributed bytes:

```text
RelationClosed(B, D, U, R, O, T, M) :=
  BaselineClosed(B)
  and DeltaClosed(B, D, U)
  and Independent(R, O)
  and RawDomainClosed(T, U)
  and RawDomain(O) = T
  and ProducerComplete(R, U)
  and ProducerComplete(O, U)
  and Keys(R) = Keys(O) = Keys(U)
  and forall k in Keys(U):
    CanonicalBytes(R[k]) = CanonicalBytes(O[k]) = CanonicalBytes(U[k])
  and RegisteredWorkflowManifestDigest(R) = M
  and ObservedWorkflowManifestDigest(O) = M
  and ExpectedWorkflowManifestDigest(U) = M
  and M = WorkflowAuthorityManifestDigest(SourceEpoch(O))
  and ValidWorkflowSourceBinding(
    SourceEpoch(O), StableWorkflowAuthorityManifest(SourceEpoch(O)), M)
  and UnknownCount(B, D, U, R, O) = 0
```

Missing, extra, stale, malformed, unattributed, or unknown rows reject
production omission. An empty baseline, empty expected relation, agreed
omission, unclassified raw candidate, incomplete producer domain, or
workflow-manifest mismatch also rejects omission. Every rejection preserves
native FullCI.

### 4.4 Generation-fenced production cutover

Source-level atomicity does not prove runtime atomicity. Production activation
uses one durable scope generation and does not permit mixed-version selective
service:

```text
SelectedPlanAllowed(subject, generation) :=
  RuntimeSupportsSuccessor(subject, generation)
  and DurableScopeGeneration(subject) = generation
  and ValidSuccessorRelationReceipt(subject, generation)
  and ValidCurrentWorkflowSourceBinding(subject, generation)
  and CurrentProviderGovernanceObservedWithinBound(subject, generation)
  and LiveProviderGovernanceDigest(subject) =
    ReceiptProviderGovernanceDigest(subject, generation)
  and NoOlderRuntimeCanMintOrConsumeSelectedAuthority(subject)
```

The operational cutover first latches the existing v1 `disable_omission`
transactional guard for the scope, revokes all v1 scope grants, waits for issued
v1 plans, leases, and their target executions to expire or terminate, drains old
replicas, and proves that no old binary remains routable. Only then may one
transaction activate a successor generation and its exact relation receipt and
clear that latch. Any old binary, missing generation, mixed replica set, stale
plan, stale lease, non-terminal old execution, or failed drain keeps the latch
set and the scope on FullCI. This bounded outage of optimization is preferred to
a mixed-version protocol because FullCI preserves validation availability
without introducing dual authority semantics.

### 4.5 Evidence binding

The relation receipt binds:

- repository and workflow subject;
- exact Phase-0 baseline, transition-delta, target-policy, catalog, registry,
  provider-observation, and owner epochs;
- baseline, delta, and expected-relation identities and retained evidence;
- complete workflow-authority manifest digest;
- initial source-binding provenance identity, retained provider commit-binding
  evidence, and exact tree/blob evidence, without treating its commit id as the
  stable authority identity;
- complete raw target/provider candidate-domain digest and total-projection
  ledger;
- registration and observation producer identities and versions;
- registration and observation manifest digests;
- canonical relation digest and retained evidence-byte locations;
- policy, catalog, target-registry, and production-admission subject digests;
- mismatch, missing, extra, and unknown counts; and
- observation time, expiry, signer identity, and durable cutover generation.

The production-admission signer may attest the result only when all failure
counts are zero and the underlying evidence bytes remain content-addressable.
The request hot path continues to verify the immutable adapter, opaque
production authority, a source-binding receipt for the current workflow source
epoch, and a bounded live provider-governance observation whose digest equals
the relation receipt. It does not rebuild the relation. Failure, timeout, or
uncertainty in either current-epoch check yields FullCI. Authorized provider
mutation additionally follows the latched drain protocol because a remote read
is not linearizable with a later administrator write. The safety claim is
therefore conditional on the owner-admitted rule that every authorized provider
mutation acquires that latch before changing provider state; an out-of-band
mutation is an incident that invalidates the production-evidence premise rather
than an event the receipt can silently tolerate.

## 5. Minimality Proof

The selected design uses the weakest sufficient boundary:

1. A per-request relation scan is unnecessary because the revision-bound
   adapter already verifies the exact immutable runtime snapshot.
2. Equality of two post-adoption inventories is insufficient because both can
   omit the same native authority; the independent Phase-0 baseline and closed
   transition relation eliminate that vacuous agreement.
3. ID-only comparison is insufficient because equal IDs can carry different
   dependencies, commands, profiles, or gate semantics.
4. One producer is insufficient because a missing registration member cannot
   be discovered by an observation domain derived from that registration.
5. A generic authority-transfer framework is unnecessary because the three
   triggers have different state, evidence, and ownership semantics.
6. A typed pre-enforcement receipt is sufficient because uncertainty must
   withhold omission authority, while native FullCI remains available.
7. Binding the whole target commit is stronger than necessary: it invalidates
   authority for non-CI changes. Binding the complete workflow-manifest digest
   invalidates exactly the relevant source domain.
8. A mixed-version selective protocol is unnecessary while FullCI is available;
   a generation-fenced drain is simpler and removes dual-semantics states.

Therefore the minimal safe current change is a target-specific, independently
produced full-row relation at production admission, plus dormant revision
conditions for the two currently unreachable mechanisms.

## 6. Falsifiers

- one registration-only or observation-only row is admitted;
- an empty or post-adoption-derived Phase-0 baseline is admitted;
- both producers omit the same baseline row without an authorized retirement;
- a raw workflow or provider candidate has no row, multiple rows, or only an
  implicit non-authority disposition;
- a baseline row has no disposition, multiple dispositions, or an unattributed
  successor;
- an added, removed, modified, retyped, or mode-changed workflow outside the
  registry-bound adapter leaves authority valid;
- an unknown object type, symlink, gitlink, duplicate path, incomplete
  traversal, tree-object mismatch, commit/root/subtree-chain mismatch,
  nullable-field mismatch, or provider/local size mismatch is accepted by the
  workflow manifest;
- provider blob metadata is admitted without retrieving, hashing, and retaining
  the attributed workflow bytes;
- equal row IDs with different canonical row bytes are admitted;
- the observation producer obtains its key set from registration;
- a v1 plan, v1 grant, stale lease, or old replica retains selected authority
  after successor-generation activation;
- the same `Applying` capability authorizes two provider calls, or a crash after
  capability consumption is inferred locally to mean that no provider effect
  occurred;
- an absence observation permits `ReconciledAbsent`, `ClosedNotApplied`, or
  reissue while a request from the prior generation can still reach or change
  the provider;
- a current source-binding receipt is missing, or the bounded live provider
  observation is missing, stale, unavailable, or differs from the receipt, yet
  a selected plan is issued;
- evidence from different repository, workflow, source, provider, policy,
  catalog, registry, or signer epochs is combined;
- missing retained evidence bytes are treated as digest equality;
- `unknown` is coerced to `not_applicable` or equality;
- a target-changing provider call is added without the ambiguous-effect state
  machine;
- provider I/O starts before a durable fenced `Applying` transition;
- destructive authority transfer is added without per-copy inventory and
  platform-owned restore evidence; or
- any failure grants selected execution instead of preserving FullCI.

## 7. Non-Claims

This contract does not claim that durable copy conservation or remote mutation
is currently implemented or required. It does not make workflow semantics,
provider behavior, backup inventory, or test adequacy observable by assertion.
It does not replace revision-bound adapter admission, shadow evidence, stable
gate proof, rollback, or external owner approval. The authenticated provider is
the authority for the commit-to-root-tree binding; this contract does not claim
to detect a malicious provider that returns a false commit projection.
