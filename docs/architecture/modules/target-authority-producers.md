# Target Authority Producers

Status: as-built dormant Stage B capability

Implemented requirement: `REQ-CI-RUNTIME-032`

Deferred production-successor requirement: `REQ-CI-RUNTIME-030`

## 1. Responsibility

`target_authority_producers` builds separately attributed registration and
observation inputs for the dormant target-authority relation. It cannot compare
the final relation, persist a receipt, activate omission, read providers, read
clocks, or obtain credentials.

The accepted Stage B design is intentionally asymmetric:

```text
registration derives owner intent from U and owner declarations
observation derives candidate keys without B, D, U, or R
```

Requiring registration to ignore `U` would contradict its role as the
owner-intended projection. Requiring observation to consume `U` would make an agreed
omission invisible. Independent producer identities alone are insufficient;
the key-derivation relation above is the protected property.

## 2. Source Groups

Let:

- `B` be the pre-adoption baseline;
- `D` be the owner-approved transition;
- `U = Apply(B, D)` be the expected adapted relation;
- `A` be one target-artifact epoch containing admitted policy, projected
  validation catalog, target registry, consumer profile, and scenario corpus;
- `W` be exact workflow authority evidence plus one source binding;
- `X` be an exact workflow discovery report for the same repository and source
  commit;
- `G` be subject-bound workflow inventory and best-effort provider governance;
  and
- `P` be the owner projection policy.

`TargetArtifactSources(A)` is admitted only when policy, catalog, registry,
profile, scenario corpus, repository identity, generator version, workflow,
event surface, and fallback/selected scenario modes close in one digest.

`ProviderAuthoritySources(G)` binds installation and repository ids, owner,
name, default branch, API version, workflow inventory, and governance state.
Its evidence class is explicitly `best_effort_unbaselined`. Fields unavailable
from effective-rules evidence, including bypass actors, remain `unknown`.
The GitHub workflow-inventory loader creates this subject-bound evidence from
the same repository value used for every provider request; relabelling an
inventory after retrieval is not an admitted provider path.

`P` content-addresses its exact subject, stable workflow-manifest digest,
target-artifact epoch digest, stable observation-domain digest, registration-
declaration-domain digest, and every candidate classification. The observation
domain excludes commit-only provenance but includes every stable candidate.
Changing any bound coordinate changes the adapted owner epoch.

## 3. Registration Producer

The owner declaration domain is independently enumerated from `A`:

```text
DR = EnumerateOwnerDeclarations(A)
R  = Register(U, A, P), where Register enumerates DR internally
```

`DR` contains policy, obligation, witness, execution-profile, registry
metadata, adapter, workflow, profile, and consumer-scenario declarations. Its
candidate set binds the exact policy, catalog, registry, and target-artifact
epoch digests.

The public producer accepts `A`, not a caller-supplied `DR`. Therefore a caller
cannot omit one declaration and then recompute a self-consistent declaration
digest before registration validation.

Registration is admitted only when:

```text
Subject(P) = Subject(U) = Scope(DR)
OwnerDigest(U.epoch) = Digest(P)
RegistrationDomainDigest(P) = Digest(DR)
Keys(P) = Keys(U)
forall d in DR:
  exists p in P:
    p.candidate_id = d.id
    and p.evidence_digest = Digest(d)
    and Family(p.row_key) = ProjectedFamily(d)
    and Project(d, p) = U[p.row_key]
```

The final registration inventory is the owner-approved full relation `U`.
This is not observation evidence. The declaration-domain digest and count are
retained separately so later receipt work can bind the independently derived
owner declarations.

## 4. Observation Producer

The raw observation domain is:

```text
T = Enumerate(W, X, G, A)
O = ProjectAll(T, P)
```

Enumeration accepts no baseline, transition, expected-relation, registration,
or registration-key input. It covers every regular workflow-tree blob, every
parsed workflow and job, target policy, validation-catalog member,
target-registry member, consumer scenario, provider repository, and observed provider
gate.

Before enumeration, these equalities must hold:

```text
Repository(W) = Repository(X) = Repository(G) = Repository(A)
Commit(SourceBinding(W)) = Revision(X) = Revision(G.workflow_inventory)
ApiVersion(SourceBinding(W)) = ApiVersion(G)
ManifestBlobs(W) = DiscoverySources(X)
TargetArtifactEpoch(A) is internally closed
```

Every safety-critical discovery unknown projects to an `unknown` authority
field. It is never converted to present or not-applicable. Provider fields not
disclosed by the admitted API remain unknown. A later comparator therefore
cannot close production authority from best-effort evidence alone.

After enumeration, `P` must classify every raw candidate exactly once. Multiple
raw candidates may project to one semantic row only when their canonical full
rows are identical. Missing, extra, stale, cross-family, or conflicting
classification rejects observation production.

```text
ObservationDomainDigest(P) = StableDomainDigest(T)
```

Consequently, deleting a candidate after enumeration cannot pass under the
same owner epoch, while a non-CI commit with unchanged stable authority does not
force a new owner policy.

## 5. Stable And Epoch-Specific Identity

Observation retains both semantic and provenance identities:

```text
StableAuthority := Digest(stable workflow manifest,
                          revision-free discovery projection,
                          subject-bound stable provider authority,
                          target artifact epoch)

Provenance := Digest(source commit, source binding, exact discovery report,
                     exact provider workflow evidence)
```

Revision-derived discovery subject ids are not semantic row fields. If a new
commit leaves workflow bytes, target artifacts, and provider authority
unchanged, row evidence remains equal while source-binding, report, and
candidate-set provenance change. A workflow or authority change changes the
stable row evidence.

## 6. Independence Argument

Assume observation omitted candidate `t` because registration omitted key `k`.
That causal path requires `Enumerate` to consume `U`, `R`, or their keys. The
observation API has no such input and its import boundary excludes the relation
producer's expected-key derivation. Therefore that path is absent from the
implemented producer.

The remaining correlated-failure class is a defect in shared immutable source
evidence. It is bounded by three independent relations:

1. stable Git-object reconstruction versus workflow parser output;
2. owner target declarations versus observation-side target projection; and
3. `B + D -> U` conservation versus both final inventories.

This does not prove open-world independence or external provider truth. It
proves only the declared finite producer relation.

## 7. Failure Algebra

Typed failures distinguish source binding, repository, API, revision,
workflow-domain, discovery, target-artifact epoch, provider subject, policy,
classification, family, row, and declaration mismatches. Every failure returns
no producer result. Unknown authority fields survive as values and cause later
relation closure to reject.

## 8. Ownership

| File                         | Sole responsibility                                       |
|------------------------------|-----------------------------------------------------------|
| `sources.py`                 | Subject- and epoch-closed source groups.                  |
| `registration_candidates.py` | Owner declaration enumeration from target artifacts.      |
| `enumeration.py`             | Observation-domain assembly without expected keys.        |
| `workflow_candidates.py`     | Workflow, blob, and job projections.                      |
| `control_candidates.py`      | Target-control and provider-governance projections.       |
| `model.py`                   | Candidate, policy, and producer-result values.            |
| `production.py`              | Registration validation and total observation projection. |

The package is pure and may depend only on owner capabilities named above and
minimal kernel/relation values. Provider transport, SQL, HTTP, runtime,
environment, clock, subprocess, and filesystem mechanisms are forbidden by
executable policy.

## 9. Non-Claims And Revision

This capability does not capture the native Phase-0 baseline, provide complete
live GitHub governance, persist evidence, sign a receipt, fence a generation,
activate omission, mutate a repository, deploy code, or prove production
readiness. `REQ-CI-RUNTIME-030` remains deferred.

Revise this contract if a source group, candidate family, projection rule,
stable/provenance identity, producer boundary, evidence class, or target
artifact epoch changes. Adding a mutating provider operation activates the
`Reserved -> Applying -> Uncertain -> Reconciled/Reissued` remote-effect state
machine with generation fencing. Moving or deleting durable authority activates
per-copy conservation for the owner-admitted live-data, WAL/archive, backup,
quarantine, and key-binding inventory. A real owner transfer between
representations continues to require independent registration and observation
inventories compared by canonical full-row bytes, never IDs alone. The latter
relation is implemented here; the first two mechanisms remain dormant until
their independent trigger becomes reachable.
