# Proofkit Adoption Specification

Status: cross-cutting specification
Last verified: 2026-09-04

## 1. Owned Invariant

The [admitted Proofkit release](../../decisions/proofkit-0-14-12-consumer-admission.md)
owns reusable proof mechanics; its exact installed version is pinned in the
[backend lock](../../../backend/uv.lock).
CI Coordinator owns product meaning, native witness execution, evidence
freshness, provider policy, rollout, and production readiness.

```text
ProofkitOwns =
  structural JSON admission
  + requirement and binding shape
  + changed-path classification
  + selective witness planning
  + bounded guidance

ProductOwns =
  semantic truth
  + native command implementation
  + runtime authority
  + credential and provider policy
  + merge and rollout decisions
```

The sets MUST remain disjoint. Otherwise a generic governance tool would become
an unevidenced product or deployment authority.

## 2. Adoption Layers

| Layer                          | Contract                                                                                                                   |
|--------------------------------|----------------------------------------------------------------------------------------------------------------------------|
| Library/CLI adoption           | Works locally and through existing repository commands without requiring a workflow change.                                |
| Repository proof configuration | An `init` or `adopt` operation produces a reviewable proposal for requirements, bindings, profile, and witness plan.       |
| Provider enforcement           | A separate optional administrator step configures required checks, base/head metadata, credentials, and provider policy.   |
| Degraded mode                  | Without provider wiring, Proofkit reports `local proof only`; it neither blocks development nor claims provider readiness. |

These layers follow least authority: a developer can establish local proof
without repository-administrator permissions, while provider enforcement cannot
be silently inferred from local files.

## 3. Repository Flow

```text
requirement source
  -> requirement binding
  -> selective witness plan
  -> native Python witness
  -> revision-bound evidence
  -> optional provider enforcement
```

Each arrow is independently falsifiable. Structural admission cannot substitute
for witness execution, and local execution cannot substitute for provider or
production evidence.

The [bounded text-input decision](../../decisions/proofkit-text-input-composition.md)
owns the pinned text-policy composition rationale. Its consumer aggregate MUST
preserve the complete included inventory, disclose prior exclusions, admit every
bounded native report and conserve the source under one shared deadline before
PASS. Composition changes capacity, not lexical policy, argument admission or
execution authority; static route closure does not establish native parity.

## 4. Governed Change Protocol

```text
AdmissibleChange(C) iff
  OneSpecOwnedBehaviorSurface(C)
  and EveryProofLikePathIsBound(C)
  and EveryBindingRoutesExecutableWitnesses(C)
  and NoUnknownSelectivePlanEdges(C)
  and NativeWitnessesPassAtReviewedRevision(C)
  and NonClaimsExcludeUnimplementedAuthority(C)
```

A design amendment is required before implementation when a change introduces
a behavior surface, trust boundary, persistence boundary, public API,
dependency class, or proof owner. A local falsifier, wording correction,
binding repair, or mechanical fixture update modifies its nearest existing
owner instead of creating a new planning document.

Before implementation, identify the exact requirement ids, semantic owner,
proof-like paths, native commands, dangerous counterexamples, import
boundaries, and required non-claims. After implementation, admit the exact
changed paths, run native witnesses, commit the reviewed patch, and execute the
branch-head gate against explicit full base and head commit ids.

## 5. Change Modes

| Mode         | Purpose                                                                                                       |
|--------------|---------------------------------------------------------------------------------------------------------------|
| `fresh`      | Introduce a new requirement, owner path, and witness together.                                                |
| `change-set` | Route the smallest sound witness set for changed paths.                                                       |
| `adopt`      | Convert existing behavior into an explicit reviewable requirement proposal.                                   |
| `retire`     | Remove an obsolete owner only through a reviewable base/head transition that preserves requirement ownership. |

`adopt` and `retire` describe required consumer ergonomics. Where the pinned CLI
does not implement them directly, repository scripts MUST preserve the same
fail-closed semantics and record the gap as upstream feedback.

## 6. Version Admission

The pinned release is admissible only when the registry identity, supported
wheel set, consumer contract surface, and repository-owned witnesses agree.
This specification owns the current Proofkit release identity and supported
wheel snapshot. It supersedes only those version-specific facts in the accepted
Python tooling decision; that decision retains the Python-only tooling,
distribution-channel, platform-class, and admission-rationale authority.

```text
AdmittedProofkitVersion(v) iff
  ExactPyPIRelease(v)
  and CompleteSupportedWheelSet(v)
  and ConsumerInputsAdmitted(v)
  and ConsumerOutputAssumptionsPreserved(v)
  and RepositoryProofRoutesPass(v)
```

For `0.6.0`, the current capability disposition is:

| Surface                                                         | Disposition               | Reason                                                                                                                                                                                               |
|-----------------------------------------------------------------|---------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Existing admission and planning commands                        | Adopt                     | Their current input contract identities remain at v1 and the complete CI Coordinator input corpus is admitted.                                                                                       |
| Bounded diagnostics and Unicode-scalar JSON                     | Adopt                     | Repository wrappers reject duplicate keys, non-finite numbers, and unpaired surrogates before admitting a Proofkit document; they do not depend on raw forwarded diagnostics or byte-identical JSON. |
| `native-evidence-guidance`                                      | Advisory input            | Its bounded questions can challenge repository witnesses, but only repository-owned commands and oracles produce evidence.                                                                           |
| `change-workflow-plan`                                          | Do not make authoritative | Its fixed generic stage profile would duplicate the repository and engineering-workflow owners without removing an existing owner or proof obligation.                                               |
| Compact proof-route declarations and source sets                | Active                    | Version 0.6.0 admits the current v2 route-source files; repository normalization projects their complete relation into the public v1 binding and selective-planning contracts.                       |
| Requirement-source codec selection and agent-route brief mode   | Inactive                  | Version 0.6.0 adds these surfaces, but CI Coordinator consumes neither the private codec experiment nor `agent-route`; their breaking changes do not alter the seven-command consumer boundary.      |
| Coverage, browser, and requirement-authoring contract revisions | Inactive                  | CI Coordinator does not consume those public surfaces, so their migrations remain outside the current dependency boundary.                                                                           |

The source release contract and the installed Python carrier remain distinct
evidence classes. A successful local compatibility run does not replace the
provider test matrix or prove future command compatibility.

Selective-plan route admission is independently derived from the exact
caller-owned input. Per-path requirement routes and the aggregate executable
command set must both match exactly; row order is intentionally non-semantic,
while missing, additional, conflicting, or duplicate routes fail closed.

The normalized v1 projection remains large because it materializes every exact
requirement, path, scenario, witness, command-set, and environment-set route.
JSON whitespace is not the semantic owner of that growth. The tracked v2
source-set declarations are the compact authority; the v1 graph is an ephemeral
compatibility projection submitted to Proofkit. Version 0.6.0 still cannot
infer the consumer-owned positive/falsification pairing.

```text
V2RouteAuthorityAdmitted iff
  V2SourceSetAdmitted
  and ExactRouteIdentityParity
  and RequiredTupleClosurePreserved
  and SelectivePlanParity
  and NativeWitnessVocabularyPreserved
  and EveryUnmappedV1FactExplicitlyRejected
```

The tracked v2 files own route authority. Their normalized v1 projection is
ephemeral compatibility data, not a second semantic owner. Byte reduction alone
would not have authorized this cutover; exact route parity and fail-closed
selection are the required predicates.

## 7. Acceptance Laws

```text
ExecutableClaim(c) => Requirement(c) and NativeWitness(c)
ChangedGovernedPath(p) => RoutedCommand(p) or ExplicitNonClaim(p)
ProviderReadiness => ProviderEvidence
not ProviderEvidence => Status == local_proof_only
DeletedOwner(p) => NoLiveBinding(p) and AuditableRetirement(p)
```

For an explicit range `(B, H)`, auditable retirement is:

```text
AuditableRetirement(p, B, H) iff
  p in ProofOwnerUniverse(B) union ProofOwnerUniverse(H)
  and p is named by exactly one committed retirement transition
  and DeclaredBindingState(p) equals Bound(p, B) or ExplicitlyUnbound(p, B)
  and Digest(AllBindings(p, B)) equals the transition digest
  and Disposition(p) = explicit obsolete
  and not Exists(p, H)
  and not Bound(p, H)
  and every affected active requirement, or its explicit one-to-one replacement,
      has a live witness at H
  and no base non-contract witness class degrades to contract-only at H
```

An unrelated witness for the same requirement is insufficient without the
explicit disposition: continued requirement coverage and retirement intent are
different predicates. Narrowing the head repo profile cannot erase baseline
owners because range classification uses the union of both owner universes.

## 8. Non-Claims

This specification does not claim that Proofkit authors true requirements,
executes native CI checks, proves a selected omission safe, grants credentials,
configures GitHub rules, or authorizes deployment.
