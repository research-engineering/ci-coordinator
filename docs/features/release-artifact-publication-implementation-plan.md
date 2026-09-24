# Release Artifact Publication Implementation Plan

Status: implementation present; exact-subject GitHub verification and provider witness required

Date: 2026-07-28

Owner requirement: `REQ-CI-RELEASE-001`

Design authority:
[Release Artifact Publication](../architecture/cross-cutting/release-artifact-publication.md)

## 1. Scope

Produce and verify one digest-addressed production-eligible OCI image from an
exact Full Check-admitted `master` commit.

Excluded: deployment, production-admission signing, GitHub App exercises,
fallback chaos, rollback, stable-gate administration, shadow pilots, and CI
omission.

## 2. Dependency Order

```text
requirement and owner contract
  -> deterministic source/gate admission
  -> workflow authority separation
  -> exact predicate schema admission and hash binding
  -> static falsifiers and workflow lint
  -> merge
  -> provider publication witness
```

The publication witness follows merge because this owner admits only the exact
`master` source and signer ref. Local static validation cannot substitute for
that provider witness.

## 3. Implementation Units

| Unit                                                     | Responsibility                                                                                 | Acceptance                                                                                                                                                               |
|----------------------------------------------------------|------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `scripts/release_artifact_identity.py`                   | Bounded gate snapshot admission, deterministic selection, and release identity                 | Every observed run satisfies exact authority; selection is order-independent and remains bound to one run and attempt.                                                  |
| `.github/workflows/release-artifact.yml`                 | Exact-source build, registry push, independent predicate validation, attestation, verification | Actionlint and zizmor pass; permissions are job-local, the subject image is not its own validator, and image code never runs in the attestor.                            |
| `scripts/release_predicate_admission.py`                 | Strict BuildKit SLSA v1 and SPDX 2.3 predicate admission                                       | Official schema substitution, duplicate JSON, vacuous collections, wrong versions, wrong build mode, malformed RFC 3339 values, and inconsistent timestamps fail closed. |
| Release predicate schemas                                | Pin official SPDX bytes and the exact BuildKit profiles                                        | Generic standards claims remain separate from repository-specific non-vacuity constraints.                                                                               |
| `scripts/tests/test_release_artifact_identity.py`        | Parser, selection, and identity falsifiers                                                      | Repeated distinct successes pass; empty, duplicate-ID, substituted, malformed, inconsistent-count, and over-budget evidence fails.                                      |
| `scripts/tests/test_release_predicate_admission.py`      | Predicate schema and semantic falsifiers                                                       | Every admitted profile dimension and exact byte hash has a negative oracle.                                                                                              |
| `scripts/tests/test_release_artifact_shell_execution.py` | Executable shell failure falsifiers                                                            | Every workflow shell step is inventoried; every authority predicate and external command has an exact success or failure oracle.                                         |
| `scripts/tests/test_release_artifact_workflow.py`        | Semantic workflow authority oracle                                                             | Wrong event, source, digest owner, scanner digest, permissions, action identity, qualification dependency, or provider verification fails the test.                     |
| Proofkit records                                         | Requirement-to-witness routing                                                                 | Requirement admission and required tuples close.                                                                                                                         |
| Deployment documentation                                 | Digest-first operator path                                                                     | No mutable tag is presented as deployment authority.                                                                                                                     |

## 4. Verification

Run behavioral witnesses and aggregate gates only through the repository-owned
GitHub workflow:

```text
backend/.venv/bin/python -m pytest -q \
  scripts/tests/test_release_artifact_identity.py \
  scripts/tests/test_release_predicate_admission.py \
  scripts/tests/test_release_artifact_shell_execution.py \
  scripts/tests/test_release_artifact_workflow.py
backend/.venv/bin/python -m scripts.python_witness lint
backend/.venv/bin/python -m scripts.python_witness typecheck
backend/.venv/bin/python -m scripts.proofkit_requirements
backend/.venv/bin/python -m scripts.proofkit_admission verify
backend/.venv/bin/python -m scripts.proofkit_plan_check
backend/.venv/bin/python -m scripts.workflow_lint
git diff --check
```

The branch-head gate and provider Full Check then bind those results to the
reviewed commit.

Local validation is limited to static lint, format, type, AST, documentation,
and whitespace checks. It does not execute the behavioral suites above.

Selection witnesses permute distinct valid run IDs and attempts, retain counts
larger than the bounded snapshot, and independently corrupt selected and
nonselected authority fields. Shell witnesses bind the chosen run and attempt
to emitted release coordinates. The SBOM witness fixes the generator index
digest independently from the BuildKit pin. Qualification witnesses retain the
successful-build dependency of the no-checkout attestor; registry upload is
not a successful release or production-admission receipt.

## 5. Post-Merge Provider Witness

1. Dispatch `Release Artifact` on exact `master`.
2. Record release run id, run attempt, admitted Full Check run id and attempt,
   release identity, source commit, GHCR manifest digest, and attestation URLs.
3. Pull the exact digest from a clean provider context.
4. Re-run `gh attestation verify` with the exact repository, signer workflow,
   signer/source digest, source ref, hosted-runner restriction, provenance
   predicate, and SPDX predicate.
5. Mark only immutable artifact publication proved.

If any step fails, retain the digest only as a failed publication attempt. Do
not deploy it or include it in a production-admission receipt.

## 6. Exit Condition

```text
ImplementedLocally
and ExactHeadFullCheckPassed
and ProviderPublicationRunPassed
and PullByDigestPassed
and ProvenanceAndSbomVerified
```

Only this conjunction closes the artifact-publication roadmap node.
