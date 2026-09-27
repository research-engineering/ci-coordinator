from __future__ import annotations

import asyncio
import base64
import json
import subprocess
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, TypedDict, cast
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from package_b_support import (
    BASE_SHA,
    HEAD_SHA,
    make_execution_projection,
    make_input,
    make_native_execution_projection,
    make_policy,
)
from package_b_support import assert_deterministic_plan as _candidate
from production_admission_support import make_production_grant

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.identity_admission import TrustedActionsRun
from ci_coordinator.kernel import FixedClock, canonical_json
from ci_coordinator.plan_issuance import (
    AuthenticatedRunBinding,
    FullCiExecution,
    InMemoryIssuanceStore,
    IssuanceGuardRejected,
    IssuanceRejected,
    IssuanceSaveResult,
    Issued,
    IssuedPlanRecord,
    PlanIssuanceContext,
    PlanRequest,
    RepositoryBinding,
    SelectedExecution,
    SignedNativeProfileExecution,
    SignedPlanEnvelope,
    SignedPlanIssuer,
    SignedPlanPayload,
    SignedPlanSigner,
    SignedProfileExecution,
    parse_plan_request,
    production_guard_binds_record,
    verify_signed_plan,
)
from ci_coordinator.plan_issuance.trusted_identity import bind_trusted_identity
from ci_coordinator.planning_core import plan
from ci_coordinator.production_admission import (
    AuthorizedProductionAdmission,
    ProductionIssuanceGuard,
    project_plan_subject,
)
from ci_coordinator.reconciliation import ReconciliationSubject
from ci_coordinator.repo_context import DiffFileChangeInput
from ci_coordinator.verification_core import VerifiedPlan, verify

NOW = datetime(2026, 7, 14, tzinfo=UTC)
EXECUTION_SHA = "c" * 40
_IDENTITY_MISMATCH_CASES = (
    pytest.param(
        lambda identity: replace(identity, repository="example-org/other-repository"),
        "plan_identity_repository_mismatch",
        id="repository",
    ),
    pytest.param(
        lambda identity: replace(identity, repository_id=201),
        "plan_identity_repository_id_mismatch",
        id="repository_id",
    ),
    pytest.param(
        lambda identity: replace(identity, ref="refs/pull/43/merge"),
        "plan_identity_ref_mismatch",
        id="ref",
    ),
    pytest.param(
        lambda identity: replace(identity, run_id=7002),
        "plan_identity_run_mismatch",
        id="run_id",
    ),
    pytest.param(
        lambda identity: replace(identity, run_attempt=2),
        "plan_identity_run_mismatch",
        id="run_attempt",
    ),
    pytest.param(
        lambda identity: replace(identity, event_name="push"),
        "plan_identity_event_mismatch",
        id="event_name",
    ),
    pytest.param(
        lambda identity: replace(identity, execution_sha="d" * 40),
        "plan_identity_execution_sha_mismatch",
        id="execution_sha",
    ),
)
_TARGET_VALIDATOR = (
    Path(__file__).resolve().parents[2]
    / "src/ci_coordinator/target_artifacts/control_source/validation_envelope.cjs"
)
_VALIDATE_ENVELOPE = """
const { createPublicKey } = require("node:crypto");
const { readFileSync } = require("node:fs");
const { verifyEnvelope } = require(process.argv[1]);
const input = JSON.parse(readFileSync(0, "utf8"));
Date.now = () => input.now;
process.stdout.write(JSON.stringify(verifyEnvelope(
  input.envelope, createPublicKey(input.publicKey), input.keyId
)));
"""


def _target_rejection(
    envelope: SignedPlanEnvelope,
    public_key_pem: bytes,
    *,
    expected_key_id: str,
    now: datetime,
) -> str | None:
    completed = subprocess.run(
        ["node", "-e", _VALIDATE_ENVELOPE, str(_TARGET_VALIDATOR)],
        input=json.dumps(
            {
                "envelope": {**envelope.unsigned_mapping(), "signature": envelope.signature},
                "publicKey": public_key_pem.decode("ascii"),
                "keyId": expected_key_id,
                "now": (now.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC))
                // timedelta(milliseconds=1),
            }
        ),
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    assert completed.returncode == 0, completed.stderr
    reason = json.loads(completed.stdout)
    assert reason is None or isinstance(reason, str)
    return reason


class _EnvelopeVector(TypedDict):
    id: str
    issuedAt: str
    expiresAt: str
    now: str
    keyId: str
    expectedKeyId: str
    mutation: str
    pythonReason: str | None
    jsReason: str | None


class _EnvelopeVectorDocument(TypedDict):
    schemaVersion: str
    canonicalUnsigned: str
    cases: list[_EnvelopeVector]


_VECTOR_DOCUMENT = cast(
    _EnvelopeVectorDocument,
    json.loads(
        (
            Path(__file__).resolve().parents[3]
            / "fixtures/conformance/v1/signed-plan-envelope-admission.v1.json"
        ).read_text(encoding="utf-8")
    ),
)


def _vector_payload() -> SignedPlanPayload:
    return SignedPlanPayload(
        schema_version="dynamic-ci-signed-plan-payload/v2",
        plan_id="fallback-plan",
        repository=RepositoryBinding(100, 200, "example-org", "ci-coordinator"),
        request=_plan_request(),
        authenticated_run=AuthenticatedRunBinding(
            issuer="https://token.actions.githubusercontent.com",
            audience="ci-coordinator",
            repository="example-org/ci-coordinator",
            repository_id=200,
            ref="refs/pull/42/merge",
            execution_sha=EXECUTION_SHA,
            run_id=7001,
            run_attempt=1,
            event_name="pull_request",
            workflow_ref="workflow@ref",
            workflow_sha=None,
            job_workflow_ref=None,
            job_workflow_sha=None,
            check_run_id=None,
            verified_at=NOW,
            verifier_version="fixture-oidc/v1",
            claim_hash=None,
        ),
        verified_plan_id=None,
        production_admission_receipt_id=None,
        execution=FullCiExecution("full-ci", "verified_plan_unavailable"),
        verifier_version=None,
        fallback_reason="verified_plan_unavailable",
    )


def _independently_signed_envelope(
    *,
    issued_at: datetime = NOW,
    expires_at: datetime = NOW + timedelta(seconds=60),
    key_id: str = "vector-key",
) -> tuple[SignedPlanEnvelope, bytes]:
    assert _VECTOR_DOCUMENT["schemaVersion"] == "ci-coordinator-signed-envelope-vectors/v1"
    unsigned = json.loads(_VECTOR_DOCUMENT["canonicalUnsigned"])
    assert (
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        == (_VECTOR_DOCUMENT["canonicalUnsigned"])
    )
    unsigned.update(issuedAt=issued_at.isoformat(), expiresAt=expires_at.isoformat(), keyId=key_id)
    # The literal payload uses integers/nulls; key-ID variants remain scalar strings.
    signing_bytes = json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
    signature = key.sign(signing_bytes)
    key.public_key().verify(signature, signing_bytes)
    envelope = SignedPlanEnvelope(
        schema_version="dynamic-ci-signed-plan-envelope/v1",
        key_id=key_id,
        algorithm="Ed25519",
        issued_at=issued_at,
        expires_at=expires_at,
        payload=_vector_payload(),
        signature=base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii"),
    )
    assert envelope.unsigned_mapping() == unsigned
    assert canonical_json(envelope.unsigned_mapping()) == signing_bytes
    return envelope, key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)


def _signature_variant(signature: str, mutation: str) -> str:
    decoded = base64.urlsafe_b64decode(signature + "==")
    assert len(decoded) == 64
    if mutation == "padded":
        variant = signature + "=="
    elif mutation == "pad-bit-alias":
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        index = alphabet.index(signature[-1])
        assert index % 16 == 0
        variant = signature[:-1] + alphabet[index + 1]
    elif mutation == "standard-alphabet":
        variant = signature.translate(str.maketrans("-_", "+/"))
        assert variant != signature
    elif mutation == "whitespace":
        variant = signature + "\n"
    elif mutation == "non-ascii":
        variant = signature[:-1] + "\u00e9"
    elif mutation == "short":
        return signature[:-1]
    elif mutation == "long":
        return signature + "A"
    elif mutation == "empty":
        return ""
    elif mutation == "changed-byte":
        changed = bytes([decoded[0] ^ 1]) + decoded[1:]
        return base64.urlsafe_b64encode(changed).rstrip(b"=").decode("ascii")
    else:
        raise AssertionError(f"unknown signature vector: {mutation}")
    if mutation in {"padded", "pad-bit-alias", "standard-alphabet", "whitespace"}:
        assert base64.urlsafe_b64decode(variant + "==") == decoded
    return variant


@pytest.mark.parametrize("case", _VECTOR_DOCUMENT["cases"], ids=lambda case: case["id"])
def test_signed_envelope_admission_matches_independent_vectors(case: _EnvelopeVector) -> None:
    envelope, public_key = _independently_signed_envelope(
        issued_at=datetime.fromisoformat(case["issuedAt"]),
        expires_at=datetime.fromisoformat(case["expiresAt"]),
        key_id=case["keyId"],
    )
    now = datetime.fromisoformat(case["now"])
    if case["mutation"] == "wrong-public-key":
        public_key = (
            Ed25519PrivateKey.from_private_bytes(bytes(range(2, 34)))
            .public_key()
            .public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        )
    elif case["mutation"] == "tampered-kid":
        envelope = replace(envelope, key_id="tampered-key")
    elif case["mutation"] != "valid":
        envelope = replace(
            envelope, signature=_signature_variant(envelope.signature, case["mutation"])
        )
    assert (
        verify_signed_plan(
            envelope,
            public_key_pem=public_key,
            expected_key_id=case["expectedKeyId"],
            clock=FixedClock(now),
        )
        == case["pythonReason"]
    )
    assert (
        _target_rejection(envelope, public_key, expected_key_id=case["expectedKeyId"], now=now)
        == case["jsReason"]
    )


def test_signer_preserves_the_independent_canonical_envelope() -> None:
    expected, public_key = _independently_signed_envelope()
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
    signer = SignedPlanSigner(
        key_id="vector-key",
        private_key_pem=key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()),
        ttl_seconds=60,
        clock=FixedClock(NOW),
    )
    actual = signer.sign(_vector_payload())
    assert actual == expected
    assert signer.public_key_pem() == public_key
    assert (
        canonical_json(actual.unsigned_mapping()).decode("utf-8")
        == (_VECTOR_DOCUMENT["canonicalUnsigned"])
    )


@pytest.mark.parametrize("expected_key_id", ["", "bad key", "a" * 129, "\u00e9", None])
def test_python_envelope_requires_an_admitted_independent_key_id(
    expected_key_id: object,
) -> None:
    envelope, public_key = _independently_signed_envelope()
    assert (
        verify_signed_plan(
            envelope,
            public_key_pem=public_key,
            expected_key_id=cast(str, expected_key_id),
            clock=FixedClock(NOW),
        )
        == "signed_plan_key_id_mismatch"
    )


def test_python_envelope_has_no_missing_key_id_compatibility_default() -> None:
    envelope, public_key = _independently_signed_envelope()
    with pytest.raises(TypeError, match="expected_key_id"):
        verify_signed_plan(  # type: ignore[call-arg]
            envelope, public_key_pem=public_key, clock=FixedClock(NOW)
        )


@pytest.mark.parametrize(
    ("key_id", "expected"),
    [
        ("", "signed_plan_key_id_mismatch"),
        ("bad key", "signed_plan_key_id_mismatch"),
        ("bad/key", "signed_plan_key_id_mismatch"),
        ("bad\nkey", "signed_plan_key_id_mismatch"),
        ("a" * 129, "signed_plan_key_id_mismatch"),
        ("\u00e9", "signed_plan_key_id_mismatch"),
        ("a", None),
        ("A9._-" + "a" * 123, None),
    ],
)
def test_python_envelope_key_grammar_is_independent_of_key_equality(
    key_id: str, expected: str | None
) -> None:
    envelope, public_key = _independently_signed_envelope(key_id=key_id)
    assert envelope.key_id == key_id
    assert (
        verify_signed_plan(
            envelope, public_key_pem=public_key, expected_key_id=key_id, clock=FixedClock(NOW)
        )
        == expected
    )
    if expected is None:
        assert _target_rejection(envelope, public_key, expected_key_id=key_id, now=NOW) is None


@pytest.mark.parametrize("field", ["issued_at", "expires_at", "clock"])
def test_python_envelope_admits_no_override_datetime_subclasses(field: str) -> None:
    class PlainInstant(datetime):
        pass

    issued_at = PlainInstant(2026, 7, 14, tzinfo=UTC) if field == "issued_at" else NOW
    expires_at = (
        PlainInstant(2026, 7, 14, 0, 1, tzinfo=UTC)
        if field == "expires_at"
        else NOW + timedelta(seconds=60)
    )
    now = PlainInstant(2026, 7, 14, tzinfo=UTC) if field == "clock" else NOW
    assert (
        type({"issued_at": issued_at, "expires_at": expires_at, "clock": now}[field])
        is PlainInstant
    )
    baseline, baseline_key = _independently_signed_envelope()
    envelope, public_key = _independently_signed_envelope(
        issued_at=issued_at, expires_at=expires_at
    )
    assert envelope.unsigned_mapping() == baseline.unsigned_mapping()
    assert envelope.signature == baseline.signature
    assert public_key == baseline_key
    assert now.isoformat() == NOW.isoformat()
    assert (
        verify_signed_plan(
            envelope, public_key_pem=public_key, expected_key_id="vector-key", clock=FixedClock(now)
        )
        is None
    )
    assert _target_rejection(envelope, public_key, expected_key_id="vector-key", now=now) is None


@pytest.mark.parametrize(
    "issued_at",
    [
        NOW.replace(tzinfo=None),
        NOW.replace(tzinfo=timezone(timedelta(seconds=1))),
        datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=1))),
    ],
)
def test_python_envelope_rejects_unprojectable_time_without_rewriting_it(
    issued_at: datetime,
) -> None:
    envelope, public_key = _independently_signed_envelope(issued_at=issued_at)
    assert (
        verify_signed_plan(
            envelope,
            public_key_pem=public_key,
            expected_key_id="vector-key",
            clock=FixedClock(NOW),
        )
        == "signed_plan_time_invalid"
    )


@pytest.mark.parametrize("field", ["issued_at", "expires_at", "clock", "naive-clock"])
def test_python_envelope_rejects_wrong_time_types(field: str) -> None:
    envelope, public_key = _independently_signed_envelope()
    now = NOW
    if field == "clock":
        now = cast(datetime, None)
    elif field == "naive-clock":
        now = NOW.replace(tzinfo=None)
    elif field == "issued_at":
        envelope = replace(envelope, issued_at=cast(datetime, "invalid"))
    else:
        envelope = replace(envelope, expires_at=cast(datetime, "invalid"))
    assert (
        verify_signed_plan(
            envelope,
            public_key_pem=public_key,
            expected_key_id="vector-key",
            clock=FixedClock(now),
        )
        == "signed_plan_time_invalid"
    )


@pytest.mark.parametrize("fold_countermodel", [False, True])
def test_zoneinfo_admission_preserves_the_original_python_lifetime_conjunct(
    fold_countermodel: bool,
) -> None:
    zone = ZoneInfo("America/Los_Angeles")
    expected: str | None
    if fold_countermodel:
        issued_at = datetime(2020, 11, 1, 1, 59, tzinfo=zone, fold=0)
        expires_at = datetime(2020, 11, 1, 1, 1, tzinfo=zone, fold=1)
        now = datetime(2020, 11, 1, 9, tzinfo=UTC)
        assert expires_at - issued_at == timedelta(minutes=-58)
        assert expires_at.astimezone(UTC) - issued_at.astimezone(UTC) == timedelta(minutes=2)
        expected = "signed_plan_ttl_invalid"
    else:
        issued_at = NOW.astimezone(zone)
        expires_at = (NOW + timedelta(seconds=60)).astimezone(zone)
        now = NOW
        expected = None
    envelope, public_key = _independently_signed_envelope(
        issued_at=issued_at, expires_at=expires_at
    )
    assert (
        verify_signed_plan(
            envelope, public_key_pem=public_key, expected_key_id="vector-key", clock=FixedClock(now)
        )
        == expected
    )
    assert _target_rejection(envelope, public_key, expected_key_id="vector-key", now=now) is None


@pytest.mark.parametrize(
    ("expiry_fold", "utc_ttl_seconds", "expected"),
    [(0, 120, None), (1, 3720, "signed_plan_ttl_invalid")],
)
def test_zoneinfo_utc_ttl_upper_bound_is_independent_of_python_lifetime(
    expiry_fold: Literal[0, 1], utc_ttl_seconds: int, expected: str | None
) -> None:
    zone = ZoneInfo("America/Los_Angeles")
    issued_at = datetime(2020, 11, 1, 1, 0, tzinfo=zone, fold=0)
    expires_at = datetime(2020, 11, 1, 1, 2, tzinfo=zone, fold=expiry_fold)
    now = datetime(2020, 11, 1, 8, 1, tzinfo=UTC)
    assert expires_at - issued_at == timedelta(seconds=120)
    assert expires_at.astimezone(UTC) - issued_at.astimezone(UTC) == timedelta(
        seconds=utc_ttl_seconds
    )
    assert now - issued_at.astimezone(UTC) == timedelta(seconds=60)
    assert expires_at > now
    assert expires_at.astimezone(UTC) > now
    envelope, public_key = _independently_signed_envelope(
        issued_at=issued_at, expires_at=expires_at
    )
    assert (
        verify_signed_plan(
            envelope, public_key_pem=public_key, expected_key_id="vector-key", clock=FixedClock(now)
        )
        == expected
    )
    assert (
        _target_rejection(envelope, public_key, expected_key_id="vector-key", now=now) == expected
    )


def test_python_envelope_samples_its_clock_once() -> None:
    class CountingClock:
        calls = 0

        def now(self) -> datetime:
            self.calls += 1
            return NOW + timedelta(days=self.calls - 1)

    envelope, public_key = _independently_signed_envelope()
    clock = CountingClock()
    assert (
        verify_signed_plan(
            envelope, public_key_pem=public_key, expected_key_id="vector-key", clock=clock
        )
        is None
    )
    assert clock.calls == 1


@pytest.mark.parametrize("field", ["schema", "algorithm"])
def test_python_envelope_preserves_unsupported_version_diagnostics(field: str) -> None:
    envelope, public_key = _independently_signed_envelope()
    unsigned = json.loads(_VECTOR_DOCUMENT["canonicalUnsigned"])
    if field == "schema":
        envelope = replace(
            envelope,
            schema_version=cast(Literal["dynamic-ci-signed-plan-envelope/v1"], "unsupported"),
        )
        unsigned["schemaVersion"] = "unsupported"
        reason = "signed_plan_schema_unsupported"
    else:
        envelope = replace(envelope, algorithm=cast(Literal["Ed25519"], "unsupported"))
        unsigned["algorithm"] = "unsupported"
        reason = "signed_plan_algorithm_unsupported"
    signing_bytes = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
    signature = key.sign(signing_bytes)
    key.public_key().verify(signature, signing_bytes)
    envelope = replace(
        envelope, signature=base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    )
    assert envelope.unsigned_mapping() == unsigned
    assert (
        verify_signed_plan(
            envelope, public_key_pem=public_key, expected_key_id="vector-key", clock=FixedClock(NOW)
        )
        == reason
    )
    assert (
        _target_rejection(envelope, public_key, expected_key_id="vector-key", now=NOW)
        == "signed_plan_envelope_schema_unsupported"
    )


def test_python_envelope_preserves_public_key_type_diagnostic() -> None:
    envelope, _public_key = _independently_signed_envelope()
    public_key = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    )
    assert (
        verify_signed_plan(
            envelope, public_key_pem=public_key, expected_key_id="vector-key", clock=FixedClock(NOW)
        )
        == "signed_plan_public_key_invalid"
    )
    assert (
        verify_signed_plan(
            envelope, public_key_pem=b"not PEM", expected_key_id="vector-key", clock=FixedClock(NOW)
        )
        == "signed_plan_signature_invalid"
    )


@pytest.mark.parametrize("ttl_seconds", [0, 301, 3600, True])
def test_signer_rejects_target_incompatible_ttl(ttl_seconds: int) -> None:
    private_key = Ed25519PrivateKey.generate().private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    )
    with pytest.raises(ValueError, match="target-compatible TTL"):
        SignedPlanSigner(
            key_id="test-key",
            private_key_pem=private_key,
            ttl_seconds=ttl_seconds,
            clock=FixedClock(NOW),
        )


@pytest.mark.parametrize(("change", "reason"), _IDENTITY_MISMATCH_CASES)
def test_trusted_identity_binding_rejects_each_mismatched_operand(
    change: Callable[[TrustedActionsRun], TrustedActionsRun], reason: str
) -> None:
    request = _plan_request()
    identity = _trusted_identity()

    assert bind_trusted_identity(request, identity) is None

    mismatch = change(identity)
    assert mismatch != identity
    assert bind_trusted_identity(request, mismatch) == reason


@pytest.mark.parametrize(("change", "reason"), _IDENTITY_MISMATCH_CASES)
def test_signed_issuer_rejects_identity_mismatch_before_effects(
    change: Callable[[TrustedActionsRun], TrustedActionsRun], reason: str
) -> None:
    request = _plan_request()
    identity = _trusted_identity()
    context = PlanIssuanceContext(
        RepositoryBinding(100, 200, "example-org", "ci-coordinator"), None, None
    )
    signer = SignedPlanSigner(
        key_id="test-key",
        private_key_pem=Ed25519PrivateKey.generate().private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        ),
        ttl_seconds=60,
        clock=FixedClock(NOW),
    )
    store = InMemoryIssuanceStore()
    issuer = SignedPlanIssuer(store=store, signer=signer)

    with (
        patch.object(signer, "sign", wraps=signer.sign) as sign,
        patch.object(store, "save", wraps=store.save) as save,
    ):
        positive = asyncio.run(issuer.issue(request, identity, context))

        assert isinstance(positive, Issued)
        assert positive.duplicate is False
        assert isinstance(positive.record.envelope.payload.execution, FullCiExecution)
        assert positive.record.envelope.payload.fallback_reason == "verified_plan_unavailable"
        assert (
            verify_signed_plan(
                positive.record.envelope,
                public_key_pem=signer.public_key_pem(),
                expected_key_id="test-key",
                clock=FixedClock(NOW),
            )
            is None
        )
        sign.assert_called_once_with(positive.record.envelope.payload, not_after=None)
        save.assert_awaited_once_with(positive.record, None)
        sign.reset_mock()
        save.reset_mock()

        mismatch = change(identity)
        assert mismatch != identity
        rejected = asyncio.run(issuer.issue(request, mismatch, context))

        assert rejected == IssuanceRejected(reason)
        sign.assert_not_called()
        save.assert_not_called()


@pytest.mark.parametrize("ttl_seconds", [1, 60, 300])
def test_signed_issuance_is_idempotent_and_payload_tampering_fails(ttl_seconds: int) -> None:
    input = make_input(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    verified = verify(input, make_policy(), _candidate(plan(input, make_policy())))
    key = Ed25519PrivateKey.generate()
    signer = SignedPlanSigner(
        key_id="test-key",
        private_key_pem=key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()),
        ttl_seconds=ttl_seconds,
        clock=FixedClock(NOW),
    )
    issuer = SignedPlanIssuer(store=InMemoryIssuanceStore(), signer=signer)
    request = PlanRequest(
        "dynamic-ci-plan-request/v2",
        "request-1",
        100,
        200,
        "example-org",
        "ci-coordinator",
        "pull_request",
        "refs/pull/42/merge",
        BASE_SHA,
        HEAD_SHA,
        7001,
        1,
        42,
        execution_sha=EXECUTION_SHA,
    )
    identity = TrustedActionsRun(
        "https://token.actions.githubusercontent.com",
        "ci-coordinator",
        "example-org/ci-coordinator",
        200,
        "refs/pull/42/merge",
        7001,
        1,
        "pull_request",
        "workflow@ref",
        None,
        None,
        None,
        None,
        NOW,
        execution_sha=EXECUTION_SHA,
    )
    context = PlanIssuanceContext(
        RepositoryBinding(100, 200, "example-org", "ci-coordinator"),
        verified,
        _admission(verified, identity),
        execution_projection=make_execution_projection(verified, now=NOW),
    )

    first = asyncio.run(issuer.issue(request, identity, context))
    second = asyncio.run(issuer.issue(request, identity, context))

    assert isinstance(first, Issued) and first.duplicate is False
    assert isinstance(second, Issued) and second.duplicate is True
    assert first.record.envelope.expires_at - first.record.envelope.issued_at == timedelta(
        seconds=ttl_seconds
    )
    assert (
        _target_rejection(
            first.record.envelope, signer.public_key_pem(), expected_key_id="test-key", now=NOW
        )
        is None
    )
    if ttl_seconds == 300:
        unsigned = {
            **first.record.envelope.unsigned_mapping(),
            "expiresAt": (NOW + timedelta(seconds=301)).isoformat(),
        }
        signature = base64.urlsafe_b64encode(key.sign(canonical_json(unsigned))).rstrip(b"=")
        too_long = replace(
            first.record.envelope,
            expires_at=NOW + timedelta(seconds=301),
            signature=signature.decode("ascii"),
        )
        assert (
            _target_rejection(
                too_long, signer.public_key_pem(), expected_key_id="test-key", now=NOW
            )
            == "signed_plan_ttl_invalid"
        )
        assert (
            verify_signed_plan(
                too_long,
                public_key_pem=signer.public_key_pem(),
                expected_key_id="test-key",
                clock=FixedClock(NOW),
            )
            == "signed_plan_ttl_invalid"
        )
    assert (
        verify_signed_plan(
            first.record.envelope,
            public_key_pem=signer.public_key_pem(),
            expected_key_id="test-key",
            clock=FixedClock(NOW),
        )
        is None
    )
    assert first.record.envelope.payload.authenticated_run.workflow_ref == "workflow@ref"
    assert first.record.envelope.payload.request.execution_sha == EXECUTION_SHA
    assert first.record.envelope.payload.authenticated_run.execution_sha == EXECUTION_SHA
    execution = first.record.envelope.payload.execution
    assert isinstance(execution, SelectedExecution)
    assert execution.deterministic_plan_id == verified.source_plan.plan_id
    assert execution.catalog_hash == verified.catalog.catalog_hash
    assert execution.selected_obligation_ids == ("docs-lint", "required-baseline")
    assert execution.selected_witness_ids == (
        "baseline-witness",
        "docs-witness",
        "shared-quality",
    )
    assert len(execution.profiles) == 1
    profile = execution.profiles[0]
    assert isinstance(profile, SignedProfileExecution)
    assert profile.max_parallel == 1
    assert profile.shards[0].test_ids == execution.selected_witness_ids
    tampered_run = replace(
        first.record.envelope.payload.authenticated_run,
        verifier_version="tampered",
    )
    tampered_envelope = replace(
        first.record.envelope,
        payload=replace(first.record.envelope.payload, authenticated_run=tampered_run),
    )
    assert (
        verify_signed_plan(
            tampered_envelope,
            public_key_pem=signer.public_key_pem(),
            expected_key_id="test-key",
            clock=FixedClock(NOW),
        )
        == "signed_plan_signature_invalid"
    )
    assert (
        verify_signed_plan(
            replace(first.record.envelope, signature="invalid"),
            public_key_pem=signer.public_key_pem(),
            expected_key_id="test-key",
            clock=FixedClock(NOW),
        )
        == "signed_plan_signature_invalid"
    )

    assert (
        verify_signed_plan(
            second.record.envelope,
            public_key_pem=signer.public_key_pem(),
            expected_key_id="test-key",
            clock=FixedClock(NOW + timedelta(seconds=ttl_seconds)),
        )
        == "signed_plan_expired"
    )

    mismatched_request = asyncio.run(
        issuer.issue(
            replace(request, head_sha="e" * 40),
            identity,
            context,
        )
    )
    assert isinstance(mismatched_request, Issued)
    assert (
        mismatched_request.record.envelope.payload.fallback_reason
        == "verified_plan_request_mismatch"
    )
    rejected = asyncio.run(issuer.issue(request, replace(identity, run_attempt=2), context))
    assert isinstance(rejected, IssuanceRejected)
    execution_mismatch = asyncio.run(
        issuer.issue(request, replace(identity, execution_sha="d" * 40), context)
    )
    assert isinstance(execution_mismatch, IssuanceRejected)


def test_request_contract_rejects_an_invalid_direct_constructor_call() -> None:
    with pytest.raises(ValueError, match="git SHA"):
        PlanRequest(
            "dynamic-ci-plan-request/v2",
            "request-1",
            100,
            200,
            "example-org",
            "ci-coordinator",
            "pull_request",
            "refs/pull/42/merge",
            "not-a-sha",
            HEAD_SHA,
            7001,
            1,
            execution_sha=EXECUTION_SHA,
        )


def test_request_contract_requires_event_specific_identity() -> None:
    with pytest.raises(ValueError, match="pull request events"):
        PlanRequest(
            "dynamic-ci-plan-request/v2",
            "request-1",
            100,
            200,
            "example-org",
            "ci-coordinator",
            "pull_request",
            "refs/pull/42/merge",
            BASE_SHA,
            HEAD_SHA,
            7001,
            1,
            execution_sha=EXECUTION_SHA,
        )

    with pytest.raises(ValueError, match="cannot carry merge group identity"):
        PlanRequest(
            "dynamic-ci-plan-request/v2",
            "request-1",
            100,
            200,
            "example-org",
            "ci-coordinator",
            "pull_request",
            "refs/pull/42/merge",
            BASE_SHA,
            HEAD_SHA,
            7001,
            1,
            42,
            "refs/heads/gh-readonly-queue/main/pr-42",
            execution_sha=EXECUTION_SHA,
        )

    with pytest.raises(ValueError, match="ref does not match"):
        PlanRequest(
            "dynamic-ci-plan-request/v2",
            "request-1",
            100,
            200,
            "example-org",
            "ci-coordinator",
            "pull_request",
            "refs/pull/42/merge",
            BASE_SHA,
            HEAD_SHA,
            7001,
            1,
            43,
            execution_sha=EXECUTION_SHA,
        )

    with pytest.raises(ValueError, match="cannot carry pull request identity"):
        PlanRequest(
            "dynamic-ci-plan-request/v2",
            "request-1",
            100,
            200,
            "example-org",
            "ci-coordinator",
            "merge_group",
            "refs/heads/gh-readonly-queue/main/pr-42",
            BASE_SHA,
            HEAD_SHA,
            7001,
            1,
            42,
            "refs/heads/gh-readonly-queue/main/pr-42",
            execution_sha=EXECUTION_SHA,
        )


def test_request_parser_rejects_unknown_and_event_incompatible_fields() -> None:
    raw_request = {
        "schemaVersion": "dynamic-ci-plan-request/v2",
        "requestId": "request-1",
        "installationId": 100,
        "repositoryId": 200,
        "owner": "example-org",
        "repository": "ci-coordinator",
        "eventName": "pull_request",
        "ref": "refs/pull/42/merge",
        "baseSha": BASE_SHA,
        "headSha": HEAD_SHA,
        "executionSha": EXECUTION_SHA,
        "workflowRunId": 7001,
        "runAttempt": 1,
        "pullRequestNumber": 42,
    }

    assert isinstance(parse_plan_request(raw_request), PlanRequest)
    assert (
        parse_plan_request({**raw_request, "pullRequestNumber": 43})
        == "pull request ref does not match its pull request number"
    )
    assert parse_plan_request({**raw_request, "unexpected": True}) == "plan_request_unknown_fields"
    assert (
        parse_plan_request(
            {**raw_request, "mergeGroupHeadRef": "refs/heads/gh-readonly-queue/main/pr-42"}
        )
        == "pull request events cannot carry merge group identity"
    )


def test_issuance_binds_a_selected_plan_to_the_request_epoch_and_enforcement_mode() -> None:
    input = make_input(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = make_policy()
    verified = verify(input, policy, _candidate(plan(input, policy)))
    signer = SignedPlanSigner(
        key_id="test-key",
        private_key_pem=Ed25519PrivateKey.generate().private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        ),
        ttl_seconds=60,
        clock=FixedClock(NOW),
    )
    issuer = SignedPlanIssuer(store=InMemoryIssuanceStore(), signer=signer)
    request = PlanRequest(
        "dynamic-ci-plan-request/v2",
        "request-1",
        100,
        200,
        "example-org",
        "ci-coordinator",
        "pull_request",
        "refs/pull/42/merge",
        BASE_SHA,
        HEAD_SHA,
        7001,
        1,
        42,
        execution_sha=EXECUTION_SHA,
    )
    identity = TrustedActionsRun(
        "https://token.actions.githubusercontent.com",
        "ci-coordinator",
        "example-org/ci-coordinator",
        200,
        "refs/pull/42/merge",
        7001,
        1,
        "pull_request",
        "workflow@ref",
        None,
        None,
        None,
        None,
        NOW,
        execution_sha=EXECUTION_SHA,
    )
    repository = RepositoryBinding(100, 200, "example-org", "ci-coordinator")
    selected = asyncio.run(
        issuer.issue(
            request,
            identity,
            PlanIssuanceContext(
                repository,
                verified,
                _admission(verified, identity),
                execution_projection=make_execution_projection(verified, now=NOW),
            ),
        )
    )
    mismatch_issuer = SignedPlanIssuer(store=InMemoryIssuanceStore(), signer=signer)
    mismatched = asyncio.run(
        mismatch_issuer.issue(
            replace(request, head_sha="c" * 40),
            identity,
            PlanIssuanceContext(
                repository,
                verified,
                _admission(verified, identity),
                execution_projection=make_execution_projection(verified, now=NOW),
            ),
        )
    )
    disabled = asyncio.run(
        issuer.issue(request, identity, PlanIssuanceContext(repository, verified, None))
    )
    forced = asyncio.run(
        SignedPlanIssuer(store=InMemoryIssuanceStore(), signer=signer).issue(
            request,
            identity,
            PlanIssuanceContext(
                repository,
                verified,
                _admission(verified, identity),
                forced_fallback_reason="operator_override",
            ),
        )
    )

    assert isinstance(selected, Issued)
    selected_execution = selected.record.envelope.payload.execution
    assert isinstance(selected_execution, SelectedExecution)
    selected_profile = selected_execution.profiles[0]
    assert isinstance(selected_profile, SignedProfileExecution)
    assert selected_profile.max_parallel == 1
    assert isinstance(mismatched, Issued)
    assert mismatched.record.envelope.payload.fallback_reason == "verified_plan_request_mismatch"
    assert isinstance(disabled, Issued)
    assert disabled.record.envelope.payload.fallback_reason == "dynamic_enforcement_disabled"
    assert isinstance(disabled.record.envelope.payload.execution, FullCiExecution)
    assert isinstance(forced, Issued)
    assert forced.record.envelope.payload.fallback_reason == "operator_override"
    with pytest.raises(ValueError, match="merge group events"):
        PlanRequest(
            "dynamic-ci-plan-request/v2",
            "request-1",
            100,
            200,
            "example-org",
            "ci-coordinator",
            "merge_group",
            "refs/heads/gh-readonly-queue/main/pr-42",
            BASE_SHA,
            HEAD_SHA,
            7001,
            1,
            execution_sha=EXECUTION_SHA,
        )


def test_execution_projection_must_bind_the_exact_verified_plan() -> None:
    input = make_input(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = make_policy()
    verified = verify(input, policy, _candidate(plan(input, policy)))
    other_input = make_input(DiffFileChangeInput(path="src/service.py", status="modified"))
    other_verified = verify(
        other_input,
        policy,
        _candidate(plan(other_input, policy)),
    )

    with pytest.raises(ValueError, match="must bind"):
        PlanIssuanceContext(
            RepositoryBinding(100, 200, "example-org", "ci-coordinator"),
            other_verified,
            None,
            execution_projection=make_execution_projection(verified, now=NOW),
        )


def test_native_execution_projects_static_jobs_without_synthetic_shards() -> None:
    input = make_input(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = make_policy()
    verified = verify(input, policy, _candidate(plan(input, policy)))
    projection = make_native_execution_projection(verified)

    execution = SelectedExecution.project(verified, projection)

    assert execution.execution_kind == "native-job-set"
    assert execution.workflow_path == ".github/workflows/full-check.yml"
    assert execution.test_manifest_id is None
    assert all(type(profile) is SignedNativeProfileExecution for profile in execution.profiles)
    assert execution.gate_provider_signal.job_id == "pr-gate"
    with pytest.raises(ValueError, match="gate does not bind"):
        replace(execution, workflow_path=".github/workflows/other.yml")


@pytest.mark.parametrize("ttl_seconds", [60, 300])
def test_production_authority_bounds_expiry_and_revalidation_falls_back(
    ttl_seconds: int,
) -> None:
    input = make_input(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = make_policy()
    verified = verify(input, policy, _candidate(plan(input, policy)))
    execution_projection = make_execution_projection(verified, now=NOW)
    request = _plan_request()
    identity = _trusted_identity()
    grant = make_production_grant(
        verified,
        execution_projection,
        identity,
        RepositoryScope(100, 200),
        now=NOW,
        expires_at=NOW + timedelta(seconds=30),
    )
    authorization = grant.authorize(
        project_plan_subject(
            RepositoryScope(100, 200),
            verified,
            identity,
            execution_projection.target_registry_hash,
            _reconciliation_subject_id(request),
        )
    )
    assert isinstance(authorization, AuthorizedProductionAdmission)
    signer = SignedPlanSigner(
        key_id="test-key",
        private_key_pem=Ed25519PrivateKey.generate().private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        ),
        ttl_seconds=ttl_seconds,
        clock=FixedClock(NOW),
    )
    store = InMemoryIssuanceStore()
    selected = asyncio.run(
        SignedPlanIssuer(store=store, signer=signer).issue(
            request,
            identity,
            PlanIssuanceContext(
                RepositoryBinding(100, 200, "example-org", "ci-coordinator"),
                verified,
                authorization,
                execution_projection=execution_projection,
            ),
        )
    )
    rotated_grant = make_production_grant(
        verified,
        execution_projection,
        identity,
        RepositoryScope(100, 200),
        now=NOW,
        expires_at=NOW + timedelta(seconds=30),
    )
    rotated_authorization = rotated_grant.authorize(
        project_plan_subject(
            RepositoryScope(100, 200),
            verified,
            identity,
            execution_projection.target_registry_hash,
            _reconciliation_subject_id(request),
        )
    )
    assert isinstance(rotated_authorization, AuthorizedProductionAdmission)
    rotated = asyncio.run(
        SignedPlanIssuer(store=store, signer=signer).issue(
            request,
            identity,
            PlanIssuanceContext(
                RepositoryBinding(100, 200, "example-org", "ci-coordinator"),
                verified,
                rotated_authorization,
                execution_projection=execution_projection,
            ),
        )
    )
    rejected = asyncio.run(
        SignedPlanIssuer(store=_RejectingProductionStore(), signer=signer).issue(
            request,
            identity,
            PlanIssuanceContext(
                RepositoryBinding(100, 200, "example-org", "ci-coordinator"),
                verified,
                authorization,
                execution_projection=execution_projection,
            ),
        )
    )

    assert isinstance(selected, Issued)
    assert selected.duplicate is False
    assert selected.record.envelope.expires_at == authorization.not_after
    assert (
        _target_rejection(
            selected.record.envelope, signer.public_key_pem(), expected_key_id="test-key", now=NOW
        )
        is None
    )
    guard = authorization.issuance_guard()
    assert production_guard_binds_record(guard, selected.record)
    payload = selected.record.envelope.payload
    assert isinstance(payload.execution, SelectedExecution)
    guard_mutants = (
        replace(
            payload,
            authenticated_run=replace(payload.authenticated_run, workflow_ref="other@ref"),
        ),
        replace(payload, execution=replace(payload.execution, catalog_hash="e" * 64)),
        replace(payload, request=replace(payload.request, head_sha="e" * 40)),
    )
    assert all(
        not production_guard_binds_record(
            guard,
            replace(
                selected.record,
                envelope=replace(selected.record.envelope, payload=mutant),
            ),
        )
        for mutant in guard_mutants
    )
    assert isinstance(rotated, Issued)
    assert rotated.duplicate is False
    assert rotated.record.idempotency_key != selected.record.idempotency_key
    assert rotated_authorization.authority_id != authorization.authority_id
    assert isinstance(rejected, Issued)
    assert rejected.record.envelope.payload.fallback_reason == "production_revalidation_failed"
    assert rejected.record.envelope.payload.production_admission_receipt_id is None


class _RejectingProductionStore:
    async def save(
        self,
        attempted: IssuedPlanRecord,
        guard: ProductionIssuanceGuard | None = None,
    ) -> IssuanceSaveResult:
        if guard is not None:
            return IssuanceGuardRejected("config_epoch_changed")
        return None


def _plan_request() -> PlanRequest:
    return PlanRequest(
        "dynamic-ci-plan-request/v2",
        "request-1",
        100,
        200,
        "example-org",
        "ci-coordinator",
        "pull_request",
        "refs/pull/42/merge",
        BASE_SHA,
        HEAD_SHA,
        7001,
        1,
        42,
        execution_sha=EXECUTION_SHA,
    )


def _trusted_identity() -> TrustedActionsRun:
    return TrustedActionsRun(
        "https://token.actions.githubusercontent.com",
        "ci-coordinator",
        "example-org/ci-coordinator",
        200,
        "refs/pull/42/merge",
        7001,
        1,
        "pull_request",
        "workflow@ref",
        None,
        None,
        None,
        None,
        NOW,
        execution_sha=EXECUTION_SHA,
    )


def _admission(
    verified: VerifiedPlan,
    identity: TrustedActionsRun,
) -> AuthorizedProductionAdmission:
    grant = make_production_grant(
        verified,
        make_execution_projection(verified, now=NOW),
        identity,
        RepositoryScope(100, 200),
        now=NOW,
    )
    authorization = grant.authorize(
        project_plan_subject(
            RepositoryScope(100, 200),
            verified,
            identity,
            make_execution_projection(verified, now=NOW).target_registry_hash,
            _reconciliation_subject_id(_plan_request()),
        )
    )
    assert isinstance(authorization, AuthorizedProductionAdmission)
    return authorization


def _reconciliation_subject_id(request: PlanRequest) -> str:
    return ReconciliationSubject.create(
        installation_id=request.installation_id,
        repository_id=request.repository_id,
        event_name=request.event_name,
        ref=request.ref,
        base_sha=request.base_sha,
        head_sha=request.head_sha,
        workflow_run_id=request.workflow_run_id,
        run_attempt=request.run_attempt,
    ).subject_id
