# Production Admission Specification

Status: cross-cutting specification

Last verified: 2026-08-31

Normative requirements: `REQ-CI-RUNTIME-017` and `REQ-CI-RUNTIME-030`

## 1. Decision

Production authority is a signed, scope-bounded, time-bounded capability. It is
not a runtime flag, the presence of credentials, a local test result, or a
deployment label.

```text
LocalComplete does not imply DeployedArtifact
DeployedArtifact does not imply ProviderExecution
ProviderExecution does not imply ProductionAuthority
ProductionAuthority(scope A) does not imply ProductionAuthority(scope B)
```

When authority is absent or uncertain:

- `enforcing` startup fails closed before provider clients are allocated;
- `non_enforcing` remains runnable but can issue only FullCI-safe plans; and
- request-time mismatch, expiry, or safety control yields FullCI or typed
  unavailability, never selected execution.

This separation is necessary. If a mode string were sufficient, changing one
ambient value could authorize omission without proving artifact, evidence,
scope, or rollback identity.

## 2. External Evidence Predicate

Let:

- `A` be one immutable artifact digest, release identity, and source commit;
- `D` be deployment evidence for `A` under one environment;
- `P` be live GitHub App, OIDC, JWKS, bootstrap, and required-check evidence;
- `F` be a bounded coordinator-outage exercise proving automatic FullCI;
- `S` be a non-vacuous shadow window over the admitted scope and rollout profile;
- `R` be an executed rollback compatible with the retained database state;
- `G` be stable aggregator and merge-queue gate identity;
- `O` be explicit platform and repository-owner approval.

```text
ExternalProductionEvidence(A,D,P,F,S,R,G,O) iff
  SameArtifact(A, D, P, F, S, R, G)
  and DeploymentHealthy(D)
  and ProviderContractsHold(P)
  and FullCIFallbackIsBounded(F)
  and ShadowEvidenceIsNonVacuous(S)
  and UnsafeOmissionCount(S) = 0
  and RollbackSucceeded(R)
  and StableGateIdentityHolds(G)
  and OwnerApprovalIsExplicit(O)
```

Every conjunct is necessary. Removing any one admits a counterexample in which
the artifact, provider, fallback, omission safety, rollback, stable gate, or
human authority is unknown.

Repository-local tests cannot prove this external predicate. The configured
production-admission signer attests it; the runtime authenticates and confines
that assertion but does not make it true.

These evidence classes originated in the v1 contract. The current runtime
accepts the v2 envelope and additionally requires the target-authority
relation and generation fence owned by `REQ-CI-RUNTIME-030`. Presence of the
implementation does not prove that an external signer produced truthful
evidence or that an environment activated the authority.

### 2.1 Current relation and generation predicate

Let `Q` be the independently produced target-authority relation and `C` the
scope-bound generation cutover, including required predecessor drain:

```text
CurrentExternalProductionEvidence(A,D,P,F,S,R,G,Q,C,O) iff
  ExternalProductionEvidence(A,D,P,F,S,R,G,O)
  and TargetAuthorityRelationClosed(Q)
  and GenerationCutoverClosed(C, Q)
  and SameTargetSubject(Q, P, S, G)
```

Registration and staging do not activate a generation. Selected issuance
requires the exact active scope generation, current workflow-source binding
to the admitted stable manifest and a bounded provider-governance observation
equal to the receipt. Unknown currentness preserves FullCI. Provider changes
must first latch and drain selective authority.

The [authority transition contract](authority-transition-safety.md) owns the
relation. The [generation-fenced authority design](../../features/generation-fenced-production-authority.md)
owns stage, drain and activation semantics. Follow the
[production authority procedure](../../how-to/stage-and-activate-production-authority.md)
only when its independent qualification and authorization prerequisites hold;
a code-delivery receipt is not external activation evidence.

## 3. Canonical Receipt

The transfer object is one duplicate-free canonical JSON envelope with one
trailing LF and at most 262,144 bytes:

```text
Envelope := {
  schemaVersion = "ci-coordinator-production-admission-envelope/v2",
  keyId,
  algorithm = "Ed25519",
  receipt,
  signature
}

Receipt := {
  schemaVersion = "ci-coordinator-production-admission/v2",
  artifactDigest,
  releaseIdentity,
  sourceCommit,
  environmentId,
  rolloutProfileId,
  issuedAt,
  expiresAt,
  scopeGrants[]
}

ScopeGrant := {
  installationId,
  repositoryId,
  configEpochId,
  compiledPolicyHash,
  policyHash,
  catalogHash,
  targetRegistryHash,
  workflowRefs[],
  jobWorkflowRefs[],
  workflowPaths[],
  jobWorkflowPaths[],
  admissionSubjectDigest,
  relation = {
    schemaVersion = "ci-coordinator.production-relation-binding/v1",
    generation,
    predecessorGeneration,
    evidenceBundleDigest,
    relationSubjectDigest,
    relationEpochDigest,
    relationClosureDigest,
    workflowManifestDigest,
    sourceBindingDigest,
    providerAuthorityDigest,
    ownerEpochDigest
  },
  evidence = {
    deployment,
    fullCiFallback,
    ownerApproval,
    provider,
    rollback,
    shadow,
    stableGate
  }
}
```

`generation` is the exact bounded successor of `predecessorGeneration`.
The relation digests bind the full evidence bundle and exact target subject;
v1 envelope bytes are not accepted as a v2 receipt.

Every evidence attestation binds the exact `admissionSubjectDigest`, has an
observation time and positive sample count, and carries its own content digest.
Shadow evidence additionally has a positive observation duration and exactly
zero unsafe omissions. Scope grants are unique and canonically ordered.

Workflow identity is admitted by exactly one of four non-interchangeable
namespaces:

```text
ExactWorkflowRef(candidate.workflowRef, grant.workflowRefs)
or ExactJobWorkflowRef(candidate.jobWorkflowRef, grant.jobWorkflowRefs)
or ExactRepositoryWorkflowPath(candidate.workflowRef, grant.workflowPaths)
or ExactRepositoryWorkflowPath(
     candidate.jobWorkflowRef,
     grant.jobWorkflowPaths
   )
```

A path identity has the exact
`owner/repository/.github/workflows/{file}.yml` shape. It does not contain a
mutable ref and therefore cannot authorize selected execution alone. OIDC must
separately prove the matching claim and immutable workflow SHA, while the
production subject binds the exact target-registry hash admitted for that
repository and workflow path.

The signature covers the exact unsigned envelope projection. The envelope
schema is mirrored byte-for-byte between the installed package and
the [canonical v2 schema](../../specs/ci-coordinator-runtime/production-admission-envelope.schema.v2.json).

The JSON Schema owns structural shape, cardinalities, and scalar lexical
bounds. It cannot prove duplicate-free source bytes, exact member order,
canonical array order, one trailing LF, cross-field subject digests, or a valid
signature. Those properties are owned by the strict parser, `kernel.canonical_json`,
the production-admission decoder, and Ed25519 verification:

```text
RuntimeAdmitted(E) => SchemaValid(Parse(E))
SchemaValid(Parse(E)) does not imply RuntimeAdmitted(E)
```

The wire profile requires object members in the kernel's Unicode-scalar order,
unique workflow-reference arrays in lexical order, scope grants ordered by
`(installationId, repositoryId)`, no insignificant whitespace, and exactly one
trailing LF. Executable schema/runtime differential tests prove that a
structurally valid envelope with reordered members or workflow references is
still rejected.

## 4. Startup Admission

Let `T = requestTimeout + planTTL`. Startup admits an envelope only when:

```text
CryptographicAdmission(E, B, C, now) iff
  CanonicalBoundedEnvelope(E)
  and Ed25519SignatureValid(E, C.keyId, C.publicKey)
  and B.productionEligible
  and E.receipt.releaseIdentity = B.releaseIdentity
  and E.receipt.sourceCommit = B.sourceCommit
  and E.receipt.artifactDigest = C.deployedArtifactDigest
  and E.receipt.environmentId = C.environmentId
  and E.receipt.rolloutProfileId = C.shadowRolloutProfileId
  and E.receipt.scopes = C.enforcementScopeAllowlist
  and E.issuedAt <= now + MaxClockSkew
  and now + T < E.expiresAt
  and E.expiresAt - E.issuedAt <= 7 days
  and EveryEvidenceObservationIsBoundAndAtMost30DaysOld(E)
```

The build identity is an immutable packaged resource generated for the image;
it is not caller-supplied process configuration. Development sentinel identity
cannot be marked production eligible.

The receipt reader rejects a symlink final component, non-regular file,
unstable read, empty file, and byte overflow. This is a final-component
filesystem guarantee, not a claim that every parent directory is
descriptor-walked or owned by the process.

## 5. Durable Authority Registration

Cryptographic admission creates one immutable registration:

```text
authorityId = "production_admission_" +
              first32hex(SHA256(CanonicalEnvelopeObject))

Registration := {
  authorityId,
  keyId,
  publicKeySpkiDer,
  exactEnvelopeBytes,
  issuedAt,
  expiresAt,
  canonicalScopeBindings[]
}
```

After the PostgreSQL engine is created and before GitHub or JWKS clients are
allocated, composition registers this value in:

```text
production_admission_authorities
production_admission_scope_bindings
```

Registration uses the database clock, rejects an already expired authority,
inserts idempotently, and reads the complete retained authority and scope set
back in the same transaction. Exact replay succeeds; any same-identity byte or
scope conflict rolls back and rejects startup.

The runtime principal has only `SELECT` and `INSERT` on these relations. It has
no update, delete, truncate, trigger, reference, maintain, ownership, or DDL
authority. Registrations are therefore append-only from the service's point of
view.

This durable step is necessary because selected plan records outlive one
process and one mounted receipt file. An in-memory grant alone cannot prove
which authority justified a retained plan after restart, receipt replacement,
or key rotation.

## 6. Request And Issuance Admission

The in-memory grant first matches a candidate against the exact scope grant:

```text
CandidateAuthorized(c, g, now) iff
  g is cryptographically admitted
  and now + T < g.expiresAt
  and c.scope = g.scope
  and c.configEpochId = g.configEpochId
  and c.compiledPolicyHash = g.compiledPolicyHash
  and c.policyHash = g.policyHash
  and c.catalogHash = g.catalogHash
  and c.workflowIdentity is admitted by g
  and c.targetRegistryHash = g.targetRegistryHash
```

Authorization yields an opaque issuance guard. Direct construction, subclassing,
or serialization cannot mint one. Selected-plan persistence then locks the
repository scope and evaluates, in the same READ COMMITTED transaction:

```text
SelectedIssuanceAllowed(guard, dbNow) iff
  ExactAuthorityAndScopeBindingRegistered(guard)
  and dbNow < guard.expiresAt
  and dbNow < guard.signedPlanExpiresAt
  and CanonicallyDecodedActiveConfigEpoch(guard.scope) = guard.configEpochId
  and not ActiveForceFullCI(guard.reconciliationSubjectId, dbNow)
  and LatestRepositoryOmissionControl(guard.scope) != disable_omission
```

Only after this predicate succeeds may the transaction insert the selected
envelope. The composite foreign key
`(production_admission_authority_id, installation_id, repository_id)` binds it
to the exact registered scope; a foreign key to the authority row alone would
permit cross-scope reuse. FullCI envelopes carry null authority. Database
constraints also require an exact Ed25519 SPKI prefix and length, an ASCII key
identity, and an authority lifetime no longer than seven days. The codec
independently checks that retained identities and canonical bytes equal the
signed authority.

The temporal terms are repeated in the selected `INSERT ... SELECT` predicate
using `clock_timestamp()`. A pre-insert check alone is insufficient because a
lock wait can consume the remaining lifetime. Therefore no selected row is
inserted when either the plan or authority has expired at statement execution,
even if both were live when the transaction acquired its first facts.

Enforcing composition additionally requires a reconciliation registrar. It
durably registers the exact projected subject and contract before issuance is
invoked:

```text
SelectedEnvelopeInserted
=> ReconciliationRegistrationCommittedBeforeIssuance
and ExactSubjectIdentityBoundByOpaqueGuard
```

Registration and issuance need not share one unit of work. If registration
fails, issuance is not called. If registration commits and issuance later
fails, the resulting unissued reconciliation subject grants no authority and
can only be observed or retired. A second cross-aggregate transaction would
therefore increase coupling without eliminating a safety counterexample. The
database owns the authority-and-scope foreign key; exact reconciliation
subject construction and pre-issuance registration remain application
composition invariants.

Using the database clock and repository-scope lock is necessary: a process
clock check alone cannot linearize expiry, epoch activation, override changes,
and plan insertion across replicas or concurrent requests.

## 7. Safety Controls

Two controls dominate production authority:

```text
ActiveForceFullCI(scope, subject, now) => FullCI(subject)
LatchedDisableOmission(scope) => FullCI(scope)
```

`force_full_ci` is subject-specific and expiry-bound. `disable_omission` is a
repository-level latch with no expiry. Re-enabling omission requires a separate
audited `enable_omission` command that names the exact retained disable override;
an arbitrary clear, elapsed time, restart, or receipt renewal cannot release it.

## 8. Failure Semantics

```text
InvalidEnvelopeAtEnforcingStartup -> production_admission_unavailable
RegistrationUnavailable          -> runtime_dependencies_unavailable
RequestAuthorityMismatch         -> FullCI
TransactionalGuardFailure        -> FullCI or typed issuance rejection
NonEnforcingMode                 -> no production authority can be constructed
```

Failure projections are redacted. They expose neither receipt content, keys,
credentials, DSNs, nor provider diagnostics.

## 9. Required Falsifiers

- wrong key id, public key, signature, canonical bytes, artifact, build,
  environment, rollout profile, scope set, or workflow identity is admitted;
- stale evidence, excessive lifetime, insufficient remaining TTL, or an
  unaware clock creates authority;
- direct construction creates a grant, authorization, or issuance guard;
- an enforcing runtime allocates provider clients before receipt admission;
- an enforcing runtime becomes ready without exact durable registration;
- exact registration replay conflicts, or conflicting replay partially writes;
- an expired, absent, wrong-scope, wrong-subject, wrong-epoch, or wrong-registry
  authority inserts a selected plan;
- epoch activation or a safety control can race selected insertion outside the
  repository lock and transaction;
- a plan expires while waiting for the repository lock but is inserted;
- a refresh or lock delay lets either expiry pass between the temporal read and
  the selected insert;
- an enforcing planning service is constructible without a reconciliation
  registrar, or invokes issuance after registration failure;
- selected issuance trusts denormalized config columns or override rows without
  owner codec and transition admission;
- a release override names a missing, later, wrong-scope, or non-disable
  predecessor and still enables omission;
- a selected envelope stores null or a different authority foreign key;
- a selected envelope reuses a valid authority in a scope absent from its
  registered scope bindings;
- the runtime principal can update or delete an authority; or
- a receipt, process restart, or elapsed time clears a latched omission disable.

## 10. Non-Claims

This specification does not select a deployment platform, admission signer,
secret provider, replica count, availability target, backup policy, release
workflow, or target-repository required-check policy. Signature verification
does not prove that the signer followed its organizational procedure. Local
receipt fixtures and PostgreSQL tests do not constitute production admission.
