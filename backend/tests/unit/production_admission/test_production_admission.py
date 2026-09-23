from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from jsonschema import Draft202012Validator, FormatChecker
from production_admission_support import synthetic_relation_binding

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import FixedClock, canonical_json
from ci_coordinator.production_admission import (
    MAX_PRODUCTION_ADMISSION_BYTES,
    PRODUCTION_ADMISSION_ALGORITHM,
    PRODUCTION_ADMISSION_ENVELOPE_SCHEMA,
    AuthorizedProductionAdmission,
    ProductionAdmissionFileError,
    ProductionAdmissionGrant,
    ProductionAdmissionReceipt,
    ProductionAdmissionRegistration,
    ProductionAdmissionRejection,
    ProductionCandidateSubject,
    ProductionEvidenceAttestation,
    ProductionEvidenceSet,
    ProductionIssuanceGuard,
    ProductionPlanSubject,
    ProductionScopeGrant,
    ProductionScopeSubject,
    ShadowEvidenceAttestation,
    admit_production_admission,
    production_admission_subject_digest,
    read_production_admission_file,
)

NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)
ARTIFACT = "sha256:" + "a" * 64
RELEASE = "b" * 64
SOURCE_COMMIT = "c" * 40
ROLLOUT_PROFILE = "d" * 64
SCOPE = RepositoryScope(101, 201)
WORKFLOW_REF = "example/repository/.github/workflows/ci.yml@refs/heads/master"


def test_verified_receipt_mints_only_exact_subject_authority() -> None:
    key = Ed25519PrivateKey.generate()
    result = _admit(_envelope(_receipt(), key), key)

    assert isinstance(result, ProductionAdmissionGrant)
    candidate = _candidate()
    plan_subject = ProductionPlanSubject(candidate, "5" * 64, "6" * 64)
    assert result.preauthorizes(candidate)
    authorization = result.authorize(plan_subject)
    assert isinstance(authorization, AuthorizedProductionAdmission)
    assert authorization.authority_id.startswith("production_admission_")
    assert authorization.binds(plan_subject)
    assert not authorization.binds(replace(plan_subject, target_registry_hash="6" * 64))
    assert result.authorize(replace(plan_subject, target_registry_hash="6" * 64)) is None


def test_verified_receipt_admits_a_dynamic_ref_only_through_its_exact_workflow_path() -> None:
    key = Ed25519PrivateKey.generate()
    workflow_path = "example/repository/.github/workflows/ci.yml"
    subject = replace(
        _scope_subject(),
        workflow_refs=(),
        workflow_paths=(workflow_path,),
    )
    result = _admit(_envelope(_receipt(subject=subject), key), key)
    candidate = replace(
        _candidate(),
        workflow_ref=f"{workflow_path}@refs/pull/42/merge",
    )

    assert isinstance(result, ProductionAdmissionGrant)
    assert result.preauthorizes(candidate)
    assert not result.preauthorizes(
        replace(
            candidate,
            workflow_ref=("example/repository/.github/workflows/other.yml@refs/pull/42/merge"),
        )
    )


def test_production_subject_rejects_a_non_repository_workflow_path() -> None:
    with pytest.raises(ValueError, match="exact repository workflow paths"):
        replace(
            _scope_subject(),
            workflow_refs=(),
            workflow_paths=("repository/.github/workflows/ci.yml",),
        )


def test_signed_receipt_satisfies_the_packaged_normative_schema() -> None:
    schema_name = "production-admission-envelope.schema.v2.json"
    packaged = files("ci_coordinator.production_admission.resources").joinpath(schema_name)
    normative = Path(__file__).parents[4] / "docs/specs/ci-coordinator-runtime" / schema_name
    key = Ed25519PrivateKey.generate()
    schema = json.loads(packaged.read_bytes())

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(
        json.loads(_envelope(_receipt(), key))
    )
    assert packaged.read_bytes() == normative.read_bytes()


@pytest.mark.parametrize("case", ["member-order", "workflow-order"])
def test_structural_schema_does_not_replace_the_runtime_wire_profile(case: str) -> None:
    schema_name = "production-admission-envelope.schema.v2.json"
    schema = json.loads(
        files("ci_coordinator.production_admission.resources").joinpath(schema_name).read_bytes()
    )
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    key = Ed25519PrivateKey.generate()
    canonical = _envelope(_receipt(), key)
    root = json.loads(canonical)

    if case == "member-order":
        content = (
            json.dumps(
                dict(reversed(tuple(root.items()))),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )
        assert content != canonical
    else:
        root["receipt"]["scopeGrants"][0]["workflowRefs"] = [
            f"{WORKFLOW_REF}/z",
            WORKFLOW_REF,
        ]
        content = _resign(root, key)

    validator.validate(json.loads(content))
    assert _admit(content, key) == ProductionAdmissionRejection("production_admission_invalid")


@pytest.mark.parametrize("case", ["key-id", "signature", "timestamp"])
def test_structural_schema_rejects_noncanonical_scalar_shapes(case: str) -> None:
    schema_name = "production-admission-envelope.schema.v2.json"
    schema = json.loads(
        files("ci_coordinator.production_admission.resources").joinpath(schema_name).read_bytes()
    )
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    key = Ed25519PrivateKey.generate()
    root = json.loads(_envelope(_receipt(), key))

    if case == "key-id":
        root["keyId"] = "production-key-\u00e9"
    elif case == "signature":
        root["signature"] = f"{root['signature'][:-1]}B"
    else:
        root["receipt"]["issuedAt"] = "2026-07-17T11:59:00.000+00:00"

    assert tuple(validator.iter_errors(root))


def test_authority_capabilities_cannot_be_constructed_directly() -> None:
    with pytest.raises(TypeError, match="cannot be constructed directly"):
        ProductionAdmissionGrant(
            object(),
            authority_id="production_admission_" + "a" * 32,
            receipt=_receipt(),
            clock=FixedClock(NOW),
            minimum_remaining_seconds=0,
        )
    with pytest.raises(TypeError, match="cannot be constructed directly"):
        AuthorizedProductionAdmission(
            object(),
            authority_id="production_admission_" + "a" * 32,
            not_after=NOW + timedelta(days=1),
            subject=ProductionPlanSubject(_candidate(), "5" * 64, "6" * 64),
        )
    with pytest.raises(TypeError, match="cannot be constructed directly"):
        ProductionIssuanceGuard(
            object(),
            admission_subject_digest="1" * 64,
            authority_id="production_admission_" + "a" * 32,
            not_after=NOW + timedelta(days=1),
            subject=ProductionPlanSubject(_candidate(), "5" * 64, "6" * 64),
        )
    with pytest.raises(TypeError, match="cannot be constructed directly"):
        ProductionAdmissionRegistration(
            object(),
            authority_id="production_admission_" + "a" * 32,
            key_id="key",
            public_key_spki_der=b"x" * 44,
            envelope_canonical_json=b"{}\n",
            issued_at=NOW,
            expires_at=NOW + timedelta(days=1),
            scope_bindings=(),
        )


def test_scope_grant_binds_policy_registry_and_workflow_identity() -> None:
    key = Ed25519PrivateKey.generate()
    result = _admit(_envelope(_receipt(), key), key)
    assert isinstance(result, ProductionAdmissionGrant)

    assert not result.preauthorizes(replace(_candidate(), config_epoch_id="6" * 64))
    assert not result.preauthorizes(replace(_candidate(), compiled_policy_hash="6" * 64))
    assert not result.preauthorizes(replace(_candidate(), policy_hash="6" * 64))
    assert not result.preauthorizes(replace(_candidate(), catalog_hash="6" * 64))
    assert not result.preauthorizes(replace(_candidate(), workflow_ref="untrusted@ref"))
    assert not result.preauthorizes(replace(_candidate(), scope=RepositoryScope(102, 202)))


def test_scope_grant_requires_each_configured_workflow_identity() -> None:
    requester_ref = "example/platform/.github/workflows/request-plan.yml@" + "1" * 40
    subject = replace(_scope_subject(), job_workflow_refs=(requester_ref,))
    key = Ed25519PrivateKey.generate()
    result = _admit(_envelope(_receipt(subject=subject), key), key)
    candidate = replace(_candidate(), job_workflow_ref=requester_ref)

    assert isinstance(result, ProductionAdmissionGrant)
    assert result.preauthorizes(candidate)
    assert not result.preauthorizes(
        replace(
            candidate,
            workflow_ref="example/repository/.github/workflows/other.yml@refs/heads/master",
        )
    )
    assert not result.preauthorizes(
        replace(
            candidate,
            job_workflow_ref=("example/platform/.github/workflows/other-requester.yml@" + "1" * 40),
        )
    )


@pytest.mark.parametrize(
    ("issued_at", "expires_at", "evidence_observed_at", "expected_code"),
    [
        (
            NOW + timedelta(minutes=6),
            NOW + timedelta(days=1),
            NOW - timedelta(hours=1),
            "production_admission_not_yet_valid",
        ),
        (
            NOW - timedelta(minutes=1),
            NOW + timedelta(seconds=80),
            NOW - timedelta(hours=1),
            "production_admission_expired",
        ),
        (
            NOW - timedelta(minutes=1),
            NOW + timedelta(days=8),
            NOW - timedelta(hours=1),
            "production_admission_invalid",
        ),
        (
            NOW - timedelta(minutes=1),
            NOW + timedelta(days=1),
            NOW - timedelta(days=31),
            "production_admission_invalid",
        ),
    ],
)
def test_temporally_invalid_receipts_fail_closed(
    issued_at: datetime,
    expires_at: datetime,
    evidence_observed_at: datetime,
    expected_code: str,
) -> None:
    key = Ed25519PrivateKey.generate()
    receipt = _receipt(
        issued_at=issued_at,
        expires_at=expires_at,
        evidence_observed_at=evidence_observed_at,
    )

    result = _admit(_envelope(receipt, key), key)

    assert result == ProductionAdmissionRejection(expected_code)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "mismatched_field",
    ["artifact", "release", "source", "environment", "rollout", "scopes"],
)
def test_receipt_must_match_every_deployment_binding(mismatched_field: str) -> None:
    key = Ed25519PrivateKey.generate()

    result = _admit(
        _envelope(_receipt(), key),
        key,
        expected_artifact_digest=(
            "sha256:" + "e" * 64 if mismatched_field == "artifact" else ARTIFACT
        ),
        expected_release_identity="e" * 64 if mismatched_field == "release" else RELEASE,
        expected_source_commit="e" * 40 if mismatched_field == "source" else SOURCE_COMMIT,
        expected_environment_id="staging" if mismatched_field == "environment" else "production",
        expected_rollout_profile_id=(
            "e" * 64 if mismatched_field == "rollout" else ROLLOUT_PROFILE
        ),
        expected_repository_scopes=(
            (RepositoryScope(102, 202),) if mismatched_field == "scopes" else (SCOPE,)
        ),
    )

    assert result == ProductionAdmissionRejection("production_admission_binding_mismatch")


def test_key_identity_signature_and_canonical_bytes_are_independent_gates() -> None:
    key = Ed25519PrivateKey.generate()
    content = _envelope(_receipt(), key)

    wrong_key_id = _admit(content, key, expected_key_id="replacement")
    wrong_key = _admit(content, Ed25519PrivateKey.generate())
    noncanonical = _admit(content[:-1] + b" \n", key)
    tampered = _admit(content.replace(b'"production"', b'"staging"'), key)

    assert wrong_key_id == ProductionAdmissionRejection("production_admission_key_mismatch")
    assert wrong_key == ProductionAdmissionRejection("production_admission_signature_invalid")
    assert noncanonical == ProductionAdmissionRejection("production_admission_invalid")
    assert tampered == ProductionAdmissionRejection("production_admission_signature_invalid")


@pytest.mark.parametrize("noncanonical_bits", range(1, 16))
def test_noncanonical_base64url_signature_aliases_are_rejected(
    noncanonical_bits: int,
) -> None:
    key = Ed25519PrivateKey.generate()
    root = json.loads(_envelope(_receipt(), key))
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    signature = root["signature"]
    assert isinstance(signature, str)
    canonical_index = alphabet.index(signature[-1])
    assert canonical_index & 0b1111 == 0
    root["signature"] = signature[:-1] + alphabet[canonical_index | noncanonical_bits]

    result = _admit(canonical_json(root) + b"\n", key)

    assert result == ProductionAdmissionRejection("production_admission_invalid")


def test_scope_evidence_cannot_be_reused_for_another_subject() -> None:
    subject = _scope_subject()
    subject_digest = _admission_subject_digest(subject)
    evidence = _evidence_set(subject_digest)
    grant = ProductionScopeGrant(subject, subject_digest, evidence, synthetic_relation_binding())

    with pytest.raises(ValueError, match="exact release subject"):
        replace(
            _receipt(),
            artifact_digest="sha256:" + "e" * 64,
            scope_grants=(grant,),
        )
    with pytest.raises(ValueError, match="exact release subject"):
        replace(
            _receipt(),
            scope_grants=(
                ProductionScopeGrant(
                    replace(subject, policy_hash="6" * 64),
                    subject_digest,
                    evidence,
                    synthetic_relation_binding(),
                ),
            ),
        )
    with pytest.raises(ValueError, match="zero unsafe omissions"):
        replace(evidence.shadow, unsafe_omission_count=1)
    with pytest.raises(ValueError, match="duration"):
        replace(evidence.shadow, observation_seconds=0)


@pytest.mark.parametrize(
    "coordinate",
    [
        "generation",
        "evidenceBundleDigest",
        "relationSubjectDigest",
        "relationEpochDigest",
        "relationClosureDigest",
        "workflowManifestDigest",
        "sourceBindingDigest",
        "providerAuthorityDigest",
        "ownerEpochDigest",
    ],
)
def test_valid_signature_does_not_admit_a_rebound_relation(coordinate: str) -> None:
    key = Ed25519PrivateKey.generate()
    original = _envelope(_receipt(), key)
    assert isinstance(_admit(original, key), ProductionAdmissionGrant)
    root = json.loads(original)
    relation = root["receipt"]["scopeGrants"][0]["relation"]
    if coordinate == "generation":
        relation.update(generation=2, predecessorGeneration=1)
    else:
        relation[coordinate] = "f" * 64

    assert _admit(_resign(root, key), key) == ProductionAdmissionRejection(
        "production_admission_invalid"
    )


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "extra",
        "schema",
        "boolean",
        "predecessor",
        "digest",
        "old-envelope",
        "old-receipt",
    ],
)
def test_successor_relation_rejects_closed_shape_and_generation_violations(case: str) -> None:
    key = Ed25519PrivateKey.generate()
    original = _envelope(_receipt(), key)
    assert isinstance(_admit(original, key), ProductionAdmissionGrant)
    root = json.loads(original)
    grant = root["receipt"]["scopeGrants"][0]
    relation = grant["relation"]
    if case == "missing":
        del grant["relation"]
    elif case == "extra":
        relation["unverified"] = True
    elif case == "schema":
        relation["schemaVersion"] = "ci-coordinator.production-relation-binding/v2"
    elif case == "boolean":
        relation["generation"] = True
    elif case == "predecessor":
        relation["predecessorGeneration"] = 1
    elif case == "digest":
        relation["relationClosureDigest"] = "g" * 64
    elif case == "old-envelope":
        root["schemaVersion"] = "ci-coordinator-production-admission-envelope/v1"
    else:
        root["receipt"]["schemaVersion"] = "ci-coordinator-production-admission/v1"

    assert _admit(_resign(root, key), key) == ProductionAdmissionRejection(
        "production_admission_invalid"
    )


def test_receipt_file_reader_accepts_stable_regular_file(tmp_path: Path) -> None:
    content = b'{"receipt":"bounded"}\n'
    path = tmp_path / "receipt.json"
    path.write_bytes(content)

    assert read_production_admission_file(path) == content


def test_receipt_file_reader_rejects_fifo_without_waiting_for_a_writer(tmp_path: Path) -> None:
    path = tmp_path / "receipt.fifo"
    os.mkfifo(path)
    program = """
import sys
from pathlib import Path
from ci_coordinator.production_admission import (
    ProductionAdmissionFileError,
    read_production_admission_file,
)

try:
    read_production_admission_file(Path(sys.argv[1]))
except ProductionAdmissionFileError as error:
    assert str(error) == "production admission file is outside its bound"
else:
    raise AssertionError("non-regular receipt was admitted")
"""

    subprocess.run(
        [sys.executable, "-c", program, str(path)],
        check=True,
        timeout=10,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env={"PYTHONPATH": str(Path(__file__).parents[3] / "src")},
    )


def test_receipt_file_reader_rejects_symlink_and_oversized_file(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_bytes(b"{}\n")
    symlink = tmp_path / "receipt.json"
    os.symlink(target, symlink)
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (MAX_PRODUCTION_ADMISSION_BYTES + 1))

    with pytest.raises(ProductionAdmissionFileError, match="unavailable"):
        read_production_admission_file(symlink)
    with pytest.raises(ProductionAdmissionFileError, match="outside its bound"):
        read_production_admission_file(oversized)


def _candidate() -> ProductionCandidateSubject:
    return ProductionCandidateSubject(
        scope=SCOPE,
        config_epoch_id="1" * 64,
        compiled_policy_hash="2" * 64,
        policy_hash="3" * 64,
        catalog_hash="4" * 64,
        execution_plan_id="verified_plan_test",
        workflow_ref=WORKFLOW_REF,
        job_workflow_ref=None,
    )


def _scope_subject() -> ProductionScopeSubject:
    return ProductionScopeSubject(
        scope=SCOPE,
        config_epoch_id="1" * 64,
        compiled_policy_hash="2" * 64,
        policy_hash="3" * 64,
        catalog_hash="4" * 64,
        target_registry_hash="5" * 64,
        workflow_refs=(WORKFLOW_REF,),
        job_workflow_refs=(),
    )


def _receipt(
    *,
    subject: ProductionScopeSubject | None = None,
    issued_at: datetime = NOW - timedelta(minutes=1),
    expires_at: datetime = NOW + timedelta(days=1),
    evidence_observed_at: datetime = NOW - timedelta(hours=1),
) -> ProductionAdmissionReceipt:
    effective_subject = _scope_subject() if subject is None else subject
    subject_digest = _admission_subject_digest(effective_subject)
    return ProductionAdmissionReceipt(
        artifact_digest=ARTIFACT,
        release_identity=RELEASE,
        source_commit=SOURCE_COMMIT,
        environment_id="production",
        rollout_profile_id=ROLLOUT_PROFILE,
        issued_at=issued_at,
        expires_at=expires_at,
        scope_grants=(
            ProductionScopeGrant(
                effective_subject,
                subject_digest,
                _evidence_set(subject_digest, observed_at=evidence_observed_at),
                synthetic_relation_binding(),
            ),
        ),
    )


def _admission_subject_digest(subject: ProductionScopeSubject) -> str:
    return production_admission_subject_digest(
        artifact_digest=ARTIFACT,
        release_identity=RELEASE,
        source_commit=SOURCE_COMMIT,
        environment_id="production",
        rollout_profile_id=ROLLOUT_PROFILE,
        scope_subject=subject,
        relation=synthetic_relation_binding(),
    )


def _evidence_set(
    subject_digest: str,
    *,
    observed_at: datetime = NOW - timedelta(hours=1),
) -> ProductionEvidenceSet:
    def ordinary(index: int) -> ProductionEvidenceAttestation:
        return ProductionEvidenceAttestation(
            evidence_digest=f"{index:x}" * 64,
            subject_digest=subject_digest,
            observed_at=observed_at,
            sample_count=1,
        )

    return ProductionEvidenceSet(
        deployment=ordinary(1),
        full_ci_fallback=ordinary(2),
        owner_approval=ordinary(3),
        provider=ordinary(4),
        rollback=ordinary(5),
        shadow=ShadowEvidenceAttestation(
            evidence_digest="6" * 64,
            subject_digest=subject_digest,
            observed_at=observed_at,
            sample_count=100,
            observation_seconds=86_400,
            unsafe_omission_count=0,
        ),
        stable_gate=ordinary(7),
    )


def _envelope(receipt: ProductionAdmissionReceipt, key: Ed25519PrivateKey) -> bytes:
    unsigned = {
        "schemaVersion": PRODUCTION_ADMISSION_ENVELOPE_SCHEMA,
        "keyId": "production-key-2026-07",
        "algorithm": PRODUCTION_ADMISSION_ALGORITHM,
        "receipt": receipt.to_mapping(),
    }
    signature = base64.urlsafe_b64encode(key.sign(canonical_json(unsigned))).rstrip(b"=")
    return canonical_json({**unsigned, "signature": signature.decode("ascii")}) + b"\n"


def _resign(root: dict[str, object], key: Ed25519PrivateKey) -> bytes:
    unsigned = {name: value for name, value in root.items() if name != "signature"}
    signature = base64.urlsafe_b64encode(key.sign(canonical_json(unsigned))).rstrip(b"=")
    return canonical_json({**unsigned, "signature": signature.decode("ascii")}) + b"\n"


def _admit(
    content: bytes,
    key: Ed25519PrivateKey,
    *,
    expected_key_id: str = "production-key-2026-07",
    expected_artifact_digest: str = ARTIFACT,
    expected_release_identity: str = RELEASE,
    expected_source_commit: str = SOURCE_COMMIT,
    expected_environment_id: str = "production",
    expected_rollout_profile_id: str = ROLLOUT_PROFILE,
    expected_repository_scopes: tuple[RepositoryScope, ...] = (SCOPE,),
) -> ProductionAdmissionGrant | ProductionAdmissionRejection:
    return admit_production_admission(
        content,
        public_key_pem=key.public_key().public_bytes(
            Encoding.PEM,
            PublicFormat.SubjectPublicKeyInfo,
        ),
        expected_key_id=expected_key_id,
        expected_artifact_digest=expected_artifact_digest,
        expected_release_identity=expected_release_identity,
        expected_source_commit=expected_source_commit,
        expected_environment_id=expected_environment_id,
        expected_rollout_profile_id=expected_rollout_profile_id,
        expected_repository_scopes=expected_repository_scopes,
        minimum_remaining_seconds=80,
        clock=FixedClock(NOW),
    )
