# Consumer Contract Laboratory

Status: approved design

Date: 2026-07-30

Owner requirement: `REQ-CI-RUNTIME-028`

## 1. Decision

CI Coordinator provides a provider-independent laboratory that composes the
real planning and issuance path with the exact generated consumer adapter of
one target repository:

```text
private exact-commit coordinator image
  + exact target contract bytes
  + independent target-owned scenarios
  -> admitted configuration epoch
  -> DynamicPlanService
  -> deterministic planning and verification
  -> selected: native execution projection and reconciliation
     or fallback: bypass selected-only controls
  -> source-epoch-derived, non-persisted lab admission and signing
  -> bounded provider-output projection
  -> generated plan consumer
  -> generated plan validator
  -> synthetic labelled job results
  -> generated aggregate gate
  -> content-addressed local receipt
```

The laboratory is a conformance boundary, not a GitHub emulator. It proves
coordinator-to-consumer control-flow composition. GitHub event delivery, hosted
runner behavior, repository settings, credentials, required checks, OIDC, and
provider rollout remain live-canary obligations.

## 2. Problem

The existing central test and a target repository's separate adapter tests can
both pass while their composition fails:

```text
CentralFixturePass and TargetFixturePass
  does not imply
Pass(CentralOutput -> ExactTargetAdapter)
```

The missing implication can fail through a stale generator revision, a changed
envelope, an incompatible registry, a target-specific dependency closure, or a
gate result algebra mismatch. Repeating the planning algorithm in the target
repository would create a second oracle and would not close this gap.

## 3. Proof Boundary

### 3.1 Protected observables

For every admitted scenario, the laboratory preserves:

1. target-owned policy and dependency-graph semantics;
2. the real `DynamicPlanService` orchestration path;
3. deterministic planning and verification for every route;
4. exactly one native capacity projection and reconciliation registration for
   selected, and neither operation for fallback;
5. signed selected or FullCI envelope bytes;
6. canonical bounded chunks that reconstruct the exact signed plan bytes in
   the generated target consumer;
7. the generated plan validator's mode and selected-job closure;
8. the generated aggregate gate's acceptance of labelled synthetic results;
9. an exact loaded coordinator image and sealed target contract byte set;
10. rejection of persistent target-source drift at the post-run observation.

### 3.2 Explicit non-observables

The laboratory does not execute target jobs and therefore does not prove:

- shell, action, container, service, or reusable-workflow runtime behavior;
- GitHub scheduler, expression, cancellation, concurrency, or merge-queue
  semantics;
- provider OIDC, REST, webhook, repository, organization, or policy behavior;
- credentials, private dependencies, hosted images, self-hosted runners, or
  external services;
- production admission evidence, omission safety, deployment, or readiness.

These exclusions are necessary. Treating a local approximation as a provider
oracle would make a false proof stronger than the evidence.

## 4. Formal Admission

Let `G` be the coordinator Git object database, `I` the private loaded
coordinator image, `T` the target worktree, `P` the target-owned profile, `S`
the target-owned scenario corpus, and `A` the exact generated adapter.

```text
SourceClosed(G, I, T, P, S, A) :=
  PinnedCoordinatorCommit(G) = P.expectedCoordinatorCommit
  and CoordinatorPackageTree(G, PinnedCoordinatorCommit) = PackageImage(I)
  and LoadedPackageSource = I/backend/src
  and BytecodeCacheRoot is fresh and outside I
  and MutableCoordinatorWorktree is not on the child import path
  and ProfileDigestBinds(P, S)
  and ProfileDigestBindsTargetInputs(P, T)
  and TargetLockBindsGeneratorAndArtifacts(T, C)
  and Render(CurrentCoordinator, TargetSource(T)) = LockedArtifacts(T)
  and RegistryAdapterDigestsMatchSealedBytes(T)
  and forall p in GeneratedControlProcesses:
    NodeRuntimeVersion(p) = 24.18.1
  and PlanningBytes = GeneratedControlBytes = SealedTargetBytes
  and TargetManifestAtClosure = TargetManifestAtPostRunObservation

ScenarioAdmitted(s, P, S) :=
  s in S
  and s.event in P.eventSurface
  and CompleteBoundedDiff(s)
  and IndependentExpectedOutcome(s)

RouteClosed(s) :=
  Selected(s) implies
    CapacityProjectionCalls(s) = 1
    and ReconciliationRegistrations(s) = 1
  and Fallback(s) implies
    CapacityProjectionCalls(s) = 0
    and ReconciliationRegistrations(s) = 0

ComposedPass(s) :=
  SourceClosed(G, I, T, P, S, A)
  and ScenarioAdmitted(s, P, S)
  and RealApplicationPathUsed(s)
  and RouteClosed(s)
  and RequestClientRoundTripPasses(s)
  and PlanValidatorMatchesExpectedOutcome(s)
  and GateAcceptsLabelledSyntheticResults(s)

LaboratoryPass :=
  S is non-empty
  and every event in P.eventSurface has a scenario in S
  and exists selected scenario in S
  and exists fallback scenario in S
  and forall s in S: ComposedPass(s)
```

Unknown, stale, mixed, malformed, changing, or incomplete evidence is a failed
local contract run. It can never become selected success.

## 5. Ownership

### 5.1 Coordinator-owned mechanics

The coordinator owns:

- strict profile and scenario admission;
- source-epoch and content-manifest calculation;
- real application-path composition;
- source-epoch-derived, non-persisted signing and synthetic lab admission;
- bounded Node invocation of generated target controls;
- deterministic receipt generation.

This code lives in `ci_coordinator.consumer_contract_lab`. Production runtime
composition does not import it.

### 5.2 Target-owned meaning

The target repository owns:

- repository identity and event surface;
- the exact coordinator revision it adopts;
- content bindings for its authored policy inputs;
- its scenario corpus;
- expected selected or fallback outcomes;
- its generated adapter and lock.

Expected outcomes are never generated by the coordinator compiler under test.
This preserves oracle independence.

## 6. Profile Contracts

The profile schema is `ci-coordinator-consumer-lab-profile/v1`. It contains:

- one canonical profile identity;
- one exact 40-character coordinator commit;
- repository identity and default branch;
- one workflow path;
- a non-empty subset of `pull_request`, `push`, and `merge_group`;
- one scenario-corpus path and SHA-256 digest;
- canonical target-owned opaque regular-file bindings and SHA-256 digests.

Profile, corpus, lock, generated control, and adapter inputs retain their
format-specific non-empty and NUL-free admission. An additional opaque target
binding denotes bounded bytes rather than text, so zero-length and NUL-bearing
regular files are valid when their exact SHA-256 digest matches.

The scenario schema is `ci-coordinator-consumer-lab-scenarios/v1`. Each
scenario contains:

- one canonical scenario identity;
- one admitted event;
- a complete bounded list of file changes;
- exactly one expected mode, `selected` or `fallback`;
- a canonical selected-job set, non-empty only in selected mode.

The profile and corpus are intentionally separate. The profile binds the corpus
bytes, while the corpus remains independently authored evidence.

The published JSON Schemas are portable structural preflight projections. They
cannot express UTF-8 byte bounds, canonical UTF-16 ordering, or uniqueness by a
nested identity. The Python codec is therefore the exact admission authority:

```text
CodecAdmitted(document) -> SchemaValid(document)
SchemaValid(document) -/-> CodecAdmitted(document)
```

Callers must use the CLI or codec before treating a document as admitted.

## 7. Source Epoch

The coordinator source must name an exact commit because its Python package is
the implementation under test. A non-runtime bootstrap resolves one commit,
bounds its recursive non-cache package tree, validates every archived blob, and
materializes a private image. The child interpreter runs in isolated mode with
the private `backend/src` as its only project source and a new external
bytecode-cache root. The laboratory independently compares the complete loaded
image inventory and bytes with the pinned commit before and after execution.
Therefore an unchecked worktree `.pyc`, `assume-unchanged`, `skip-worktree`, or
an uncommitted coordinator edit cannot change the implementation under test.
The mutable coordinator worktree need not be clean because it is not executed.

The target may be either:

- `commit`: every sealed contract path has the exact blob identity found at
  the pinned Git head; or
- `worktree`: contract paths that differ from that head are reported and their
  exact bytes are content-addressed without being called a GitHub revision.

Files outside the closed contract-path universe do not affect the receipt, and
the receipt does not claim global worktree cleanliness.

The real planning service requires one SHA-shaped execution-authority
coordinate. For a commit source, the laboratory uses the exact target commit.
For a worktree source, it uses a domain-separated 160-bit coordinate derived
from the complete source epoch. The receipt labels the latter
`sealed-worktree-manifest` and `providerEvidence=false`; it is neither a GitHub
workflow revision nor provider identity evidence.

The target contract manifest covers:

1. the profile and scenario corpus;
2. every profile-bound target input;
3. the target artifact source and lock;
4. every lock-listed generated artifact;
5. every registry-bound workflow and control file.

Symlinks, special files, duplicate paths, digest mismatches, generator
mismatches, and persistent post-run manifest changes are rejected. Leaf paths
are opened non-blockingly before their file type is admitted, so a FIFO or
similar special file cannot defer rejection until the outer process timeout.
Every planning read uses the sealed in-memory bytes. Generated Node controls
execute from a private image containing exactly those sealed bytes, so a target
path replacement after closure cannot change the executed program. The
laboratory does not claim to observe a transient pathname mutation that is
restored before the post-run observation; such a mutation has no execution
authority.

## 8. Lab Authority

Selected issuance requires the normal opaque production-admission capability.
The laboratory obtains it only through the real receipt verifier using:

- a deterministic Ed25519 admission key derived from the exact source epoch and
  scenario;
- a separately domain-bound deterministic Ed25519 plan key derived from the
  same identities;
- fixed time;
- `environmentId = consumer-lab`;
- a subject bound to the exact scenario candidate and target registry;
- synthetic evidence explicitly marked only in the final lab receipt;
- in-memory issuance and reconciliation state.

The keys are reproducible test fixtures, not secrets or entropy claims. They are
never persisted or accepted by a production entrypoint.

The generated Node controls run with the stable Node permission model. An early
probe rejects an unavailable or mismatched runtime, and every generated-control
process loads a coordinator-owned guard that checks its own
`process.version = v24.18.1` before loading target code. Therefore replacing the
admitted executable pathname cannot transfer admission to a different Node
version. Each process receives only the filesystem reads and writes required by
the private images and scenario outputs; child processes, worker threads,
native addons, and WASI are not granted. The bounded launcher applies one
aggregate output limit and wall deadline, holds the original process-group
identity through cleanup and reap, and closes inherited output pipes after the
deadline.

The laboratory has no GitHub adapter, credential provider, production key,
database adapter, publication path, or production runtime import. This proves
absence of provider authority within the declared composition. It does not
claim hostile same-user confinement, a generic operating-system sandbox, or
cleanup of a separately trusted executable that creates a new detached session.
The local Python interpreter, Git executable and object database, Node runtime,
operating system, and installed bootstrap remain explicit trust roots.

## 9. Provider Proof Ladder

```text
actionlint and repository static gates
  -> provider-independent consumer contract laboratory
  -> exact-head GitHub canary
  -> versioned provider-profile evidence
  -> shadow admission
  -> production omission decision
```

The future provider profile must separately bind tenant rollout and security
hold cohorts, REST API epoch, workflow syntax and feature epoch, runner and
image identities, action digests, OIDC subject semantics, and the live workflow
revision. Those facts are deliberately absent from the local laboratory.

`runner.server` may later be evaluated as an optional execution substrate.
`act` may be used for bounded job smoke tests. Neither may issue the semantic
receipt owned by this design.

## 10. Falsifiers

The implementation must reject at least:

1. pinned coordinator commit, loaded image, profile, generator, or lock
   mismatch;
2. coordinator image inventory or bytes that differ from the commit, including
   an unchecked worktree bytecode cache or changes hidden by Git index flags;
3. changed profile-bound, locked, or registry-bound bytes;
4. missing, duplicate, symlinked, blocking-special, oversized, or
   non-canonical files, while accepting digest-bound zero-length and NUL-bearing
   opaque regular files;
5. a corpus digest, profile identity, event surface, or workflow mismatch;
6. a missing selected or fallback scenario;
7. a scenario event outside the declared event surface;
8. incomplete, duplicate, unsafe, or excessive diff evidence;
9. an invalid admitted configuration projection;
10. bypass of `DynamicPlanService`, signing, bounded transport projection, plan
    consumer, plan validator, or aggregate gate; bypass of selected capacity or
    reconciliation; or access to either selected-only control during fallback;
11. mode or selected-job divergence from independently declared expectations
    on each admitted event;
12. execution of target bytes outside the sealed image or persistent target
    source mutation at the post-run observation;
13. any receipt that claims provider enforcement or executed target jobs;
14. a generated-control process running a Node runtime other than exact
    `24.18.1`, including executable-path replacement after the early probe; a
    direct process that exceeds its aggregate output or wall-time bound;
    same-group survival after leader exit; inherited pipes beyond the deadline;
    or Node process-creation authority.
15. a model, codec, Git-tree, archive-inventory, bootstrap, or process request
    outside the declared finite input language.

## 11. Alternatives

| Alternative                                    | Verdict  | Reason                                                                                                                                                |
|------------------------------------------------|----------|-------------------------------------------------------------------------------------------------------------------------------------------------------|
| Separate central and target tests              | Rejected | Does not prove composition.                                                                                                                           |
| Reimplement planning in the target             | Rejected | Creates a correlated second oracle.                                                                                                                   |
| Import coordinator internals from target tests | Rejected | Breaks the stable CLI and version boundary.                                                                                                           |
| Full local GitHub emulator                     | Rejected | No complete qualified OSS oracle was found in the bounded public candidate set audited on 2026-07-30; building one exceeds the protected observables. |
| `act` or `runner.server` as oracle             | Rejected | Both approximate only subsets of provider semantics.                                                                                                  |
| Central CLI plus target-owned profile/corpus   | Accepted | It is the weakest architecture that closes the actual composed-contract gap.                                                                          |

## 12. Revision Conditions

Revisit this design when one of these becomes true:

- a provider publishes a versioned local conformance service covering the
  required observables;
- the target execution kind cannot be represented by the current static-job or
  witness-shard contracts;
- local lane execution becomes a release blocker rather than optional evidence;
- bootstrap or runtime trust-root attestation is required beyond the installed
  distribution and local Git object database;
- the scenario model requires typed fault injection beyond route and gate
  composition.
