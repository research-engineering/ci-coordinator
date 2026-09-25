# Release Artifact Publication

Status: repository implementation present; publication from this repository
and deployment pending

Date: 2026-07-28

Normative requirement: `REQ-CI-RELEASE-001`

## 1. Decision

The repository owns one manual workflow that publishes a production-eligible
OCI image from the exact current `master` commit only after an exact successful
`Full Check` manual run for that commit. The registry-returned manifest digest is
the artifact identity. A unique run tag is discovery metadata only.

```text
LocalWorkflowConformance does not imply ProviderPublication
ProviderPublication does not imply Deployment
Deployment does not imply ProductionAdmission
```

This owner closes artifact production only. Deployment, GitHub App exercises,
FullCI fallback chaos, rollback, stable required checks, shadow evidence,
owner approval, and a signed production-admission receipt remain external
conjuncts.

Former private provider publication receipts are not exported or inherited.
The new public repository requires its own exact source, gate, publisher,
registry and signature evidence. Neither this design nor a former successful
run publishes the current source or establishes deployment or production admission.

## 2. Ownership

| Fact                                   | Owner                                                        |
|----------------------------------------|--------------------------------------------------------------|
| Release source and gate admission      | `ci-coordinator.release`                                     |
| Release identity derivation            | `scripts/release_artifact_identity.py`                       |
| Predicate byte admission               | `scripts/release_predicate_admission.py` and release schemas |
| Packaged build-identity shape          | `runtime_settings/build_identity.py`                         |
| OCI publication and attestation wiring | `.github/workflows/release-artifact.yml`                     |
| Registry manifest digest               | GHCR after successful push                                   |
| Production authority                   | `production-admission.md` and its external signer            |

The release workflow consumes the runtime-owned build-identity renderer but
does not redefine its schema. Production admission consumes the resulting
artifact coordinates but does not make publication true.

## 3. Admission Predicate

Let:

- `R` be the independently verified immutable repository ID for the admitted
  publisher and its asserted repository name;
- `S` be the exact lowercase 40-character commit at the admitted protected ref;
- `F` be the exact admitted release workflow ref for that publisher;
- `O` be one bounded provider snapshot of completed successful source-gate runs;
- `G` be one deterministically selected run from `O`, after every observed run
  satisfies the same repository, source commit and protected-ref contract;
- `W` be one admitted manual release-workflow run at `S`;
- `I` be the derived release identity;
- `D` be the digest returned by the registry push; and
- `A` be the image addressed by the admitted publisher and registry digest.

The intended source owner is `research-engineering/ci-coordinator`. Its numeric
repository/workflow IDs, protected ref, registry authority and successful runs
are not inferred from the former private repository. New exact-subject evidence
must satisfy the executable release owner.
`admitted_full_check_workflow_id` is the operator-configured
`CI_COORDINATOR_FULL_CHECK_WORKFLOW_ID`, not an ID inferred from a candidate run.

```text
ExactGate(g,R,S) iff
  g.repositoryId = R.id
  and g.repositoryName = R.name
  and g.headRepositoryId = R.id
  and g.headRepositoryName = R.name
  and g.workflowId = admitted_full_check_workflow_id
  and g.workflowName = Full Check
  and g.workflowPath = .github/workflows/python-persistence.yml
  and g.event = workflow_dispatch
  and g.status = completed
  and g.conclusion = success
  and g.headBranch = master
  and g.headSha = S
  and PositiveInteger(g.id)
  and PositiveInteger(g.attempt)

ReleaseSourceAdmitted(R,S,O,G,W) iff
  W.event = workflow_dispatch
  and W.repositoryId = R.id
  and W.repositoryName = R.name
  and W.ref = refs/heads/master
  and W.sha = S
  and W.workflowRef = F
  and W.workflowSha = S
  and CheckoutHead = S
  and 1 <= |O| <= 10
  and PositiveInteger(response.totalCount)
  and response.totalCount >= |O|
  and every g in O satisfies ExactGate(g,R,S)
  and DistinctRunIds(O)
  and G = argmax(g.id for g in O)
```

These checks preserve exact-subject admission. Removing repository identity permits a renamed or
cross-repository source. Removing the ref or checkout equality permits a
different commit. Removing the event and gate fields permits a wrong-event,
neutral, stale, or unrelated same-SHA run. Validating every observed candidate
prevents a valid selected record from masking foreign or malformed evidence.
The maximum run ID is only a deterministic selection key within `O`: it is not
a claim about creation time, the globally newest success, complete history, or
provider state after the read. Distinct valid executions do not create
ambiguity; repeated run IDs, including conflicting attempts, do.

The workflow requests only page 1 with `per_page=10`; the stable JSON read is
capped at 1 MiB. A larger `total_count` is permitted because one exact success
is sufficient, not because unseen records were validated. The
[workflow-runs API](https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs-for-a-workflow)
supplies the filters and pagination, but response records still require local
authority validation. Fetching every page adds no proof to this existence claim;
selecting the first result would depend on provider ordering.

The manual event is deliberate. A `workflow_run` handler can observe a passed
run for `S1` while its own `github.sha` names a later default-branch commit
`S2`. Signing an artifact built from `S1` under source identity `S2` is a valid
counterexample. Exact manual dispatch plus exact gate lookup keeps workflow,
source, and signer SHA equal without publishing every merged commit.

## 4. Release Identity

The release identity is:

```text
I = SHA256(ACJ({
  schemaVersion,
  repository,
  repositoryId,
  sourceCommit,
  sourceRef,
  gateWorkflowId,
  gateRunId,
  gateRunAttempt,
  releaseEvent,
  releaseWorkflowPath,
  releaseRunId,
  releaseRunAttempt
}))
```

`ACJ` is ASCII JSON with lexically sorted keys and no insignificant
whitespace. Every scalar is admitted before hashing. Therefore equal admitted
coordinates produce equal identities and changing any coordinate changes the
hash except with SHA-256 collision probability.

`I` is an immutable release-attempt identity, not a content digest. The image
manifest digest `D` remains the content authority because `I` must be embedded
before `D` exists.

## 5. Authority Separation

The workflow uses three jobs:

| Job         | Repository code                                      | Authority                                       |
|-------------|------------------------------------------------------|-------------------------------------------------|
| `preflight` | checks out and executes the identity validator       | `actions: read`, `contents: read`               |
| `build`     | executes the build and validates registry predicates | `contents: read`, `packages: write`             |
| `attest`    | no checkout and no image execution                   | OIDC, attestations, artifact metadata, packages |

```text
ChecksOutBuildContext(attest) = false
ExecutesPublishedImage(attest) = false
CanMintOIDC(build) = false
```

The build job cannot mint a signing identity. After the registry push, it
extracts predicates from `A`, validates their exact bytes with Python and
`jsonschema` provisioned independently from the exact repository lock, and
emits only their SHA-256 digests. The subject image is never used as its own
validator runtime. Therefore a compromised image cannot fabricate successful
validator output merely by controlling its executable contents.

The signing job receives no checked-out repository program and does not execute
the image. It pulls `A` by `D`, creates a stopped container, copies the packaged
build identity, compares its exact canonical bytes, independently re-extracts
the predicates, and checks their hashes against the validated bytes. Therefore
the build result cannot substitute different predicate bytes between validation
and signing without producing a hash mismatch.

This is the minimum split that removes OIDC from repository build execution and
keeps semantic predicate validation outside the signing boundary. A fourth job
would not remove the attestor's unavoidable OCI extraction or SBOM consumption,
but would add another output-transfer edge. It is therefore rejected until a
stronger provider-native verified-evidence transfer exists.

## 6. Artifact And Attestation Contract

The build:

- checks out exactly `S` without persisted Git credentials;
- uses digest-pinned GitHub Actions, BuildKit `0.33.0`, and Buildx `0.37.1`;
- selects the SBOM generator by the immutable image-index digest in the
  workflow, independently of the BuildKit image pin;
- pulls every Dockerfile base by its existing immutable digest;
- embeds `I`, `S`, and `productionEligible=true`;
- publishes one `linux/amd64` image;
- explicitly requests maximum BuildKit SLSA v1 provenance and an SPDX 2.3 SBOM;
- requires exact final-image vulnerability admission and admitted runtime
  repair evidence before the signing job can run;
- validates provenance with the repository-owned BuildKit/SLSA v1 profile and
  an independently provisioned lock-resolved `jsonschema 4.26.0` runtime whose
  repository-owned RFC 3339 checker rejects unknown or malformed date-time
  formats;
- validates the SBOM with the immutable official SPDX 2.3 schema plus the
  repository-owned non-vacuity and RFC 3339 creation-time profile; and
- returns `D` from `docker/build-push-action`, not from a local daemon or tag.

BuildKit `v0.33.0` otherwise selects
[`docker/buildkit-syft-scanner:stable-1`](https://github.com/moby/buildkit/blob/v0.33.0/frontend/attestations/parse.go).
The explicit [`generator` option](https://docs.docker.com/build/metadata/attestations/sbom/#sbom-generator)
removes that mutable resolution from the release definition. The pin fixes
generator bytes; it does not prove scanner correctness or SBOM completeness.

The attest job:

1. rejects a noncanonical SHA-256 manifest digest;
2. pulls `A` by `D`;
3. validates exact build-identity bytes without running `A`;
4. re-extracts both predicates and matches their exact SHA-256 hashes to the
   bytes admitted in the non-OIDC build job;
5. creates GitHub provenance and SBOM attestations for `A`;
6. publishes those attestations to GitHub and GHCR; and
7. verifies both GitHub-API and OCI-registry copies against the exact signer
   workflow, signer SHA, source SHA, source ref, hosted-runner class, and
   predicate type.

```text
Published(A) :=
  PushReturned(D)
  and PullByDigest(A,D)
  and EmbeddedIdentity(A) = (I,S,true)
  and FinalImageVulnerabilityAdmissionPassed(A,D,S)
  and RuntimeRepairEvidenceAdmitted(A,D,S)
  and BuildKitSlsaV1ProfileValid(A)
  and OfficialSpdx23SchemaValid(A)
  and BuildKitSpdxProfileValid(A)
  and AttestorPredicateHashesMatch(A)
  and GitHubApiProvenanceVerified(A,D,S)
  and GitHubApiSbomVerified(A,D,S)
  and OciProvenanceVerified(A,D,S)
  and OciSbomVerified(A,D,S)
```

A provider run may claim `Published(A)` only when every step completes. A local
workflow parser or linter cannot make this predicate true.

`productionEligible=true` identifies a non-development build with bound source
and release coordinates. It is a necessary input to the separately signed
production-admission predicate, not a vulnerability verdict, successful release
receipt, deployment authorization, or permission to omit CI. Registry presence
and a `run-*` tag are not substitutes for the complete publication evidence.

## 7. Failure Semantics

Every ambiguity fails the workflow:

- wrong repository, ref, event, or checkout SHA;
- no observed exact successful Full Check run, an invalid observed candidate,
  duplicate run IDs, an inconsistent count, or an over-budget snapshot;
- malformed, duplicate-key, unstable, empty, or oversized provider JSON;
- mutable or malformed digest;
- failed pull, absent canonical build identity, or mismatched coordinates;
- failed, missing, stale, or unproved vulnerability or runtime repair evidence;
- absent, malformed, structurally vacuous, schema-invalid, or substituted
  provenance or SBOM;
- unavailable attestation service; or
- signer, source, ref, runner, or predicate mismatch.

No failed run publishes a production-admission receipt or grants deployment or
omission authority. A build may have reached GHCR before vulnerability scanning,
repair admission, predicate validation, attestation, or final verification
fails. Such a digest remains an incomplete publication attempt; registry upload
and its packaged identity do not make it admissible. Qualification requires the
complete exact-subject publication evidence, not merely a later signature.
This workflow does not quarantine or delete failed uploads. Registry visibility
and retention are separate policies, not evidence that deployment was admitted.

## 8. Alternatives

| Alternative                                | Rejection reason                                                                                                                |
|--------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------|
| Publish from every push                    | Consumes release resources without an admitted release decision.                                                                |
| Trigger through `workflow_run`             | Signer SHA can differ from the passed run's source SHA.                                                                         |
| Trust the mutable tag                      | A tag is a replaceable locator, not content identity.                                                                           |
| Require only one success in provider history | Repeated exact valid executions add no authority ambiguity once selection is deterministic within the bounded snapshot.       |
| Read a local Docker digest                 | Local cache and daemon state are lower authority than the registry push result.                                                 |
| Build and sign in one job                  | Repository-controlled build execution receives OIDC signing authority.                                                          |
| Execute the image in the attest job        | Unreviewed artifact code would run inside the signing trust boundary.                                                           |
| Execute the image as its own validator     | A compromised subject could fabricate successful validator output and cross the signing boundary through trusted hashes.        |
| Validate only with shallow `jq` predicates | Structurally empty materials or packages satisfy presence checks, and the v0.2 field shape rejects BuildKit's pinned v1 output. |
| Download schemas during signing            | Network retrieval adds mutable availability and content authority inside the OIDC boundary.                                     |
| Add a custom release manifest now          | Duplicates the planned release-evidence and production-admission owners before their complete subject exists.                   |

## 9. Non-Claims And Revision Conditions

This contract does not prove multi-platform support, reproducible byte-identical
rebuilds, vulnerability absence, deployment health, database migration,
rollback, live GitHub App or OIDC behavior in target repositories, fallback,
stable provider gates, shadow safety, owner approval, or production authority.

Revisit the design when a trusted organization-wide builder can provide the
same or stronger signer/source/digest binding with less repository code, when
production requires another platform, or when the provider exposes a stronger
atomic build-and-attest primitive without giving repository build code signing
authority.
