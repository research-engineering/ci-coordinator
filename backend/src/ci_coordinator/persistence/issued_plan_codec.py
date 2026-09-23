"""Canonical row codec for durable signed-plan envelopes."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Literal, cast

from ci_coordinator.kernel import canonical_json
from ci_coordinator.persistence.issued_execution_codec import decode_execution
from ci_coordinator.persistence.runtime_state_profile import (
    RuntimeIngressIssuanceStateProfile,
)
from ci_coordinator.plan_issuance import (
    AuthenticatedRunBinding,
    IssuedPlanRecord,
    PlanRequest,
    RepositoryBinding,
    SignedPlanEnvelope,
    SignedPlanPayload,
)


class IssuedPlanCodecError(ValueError):
    pass


def encode_envelope(
    envelope: SignedPlanEnvelope,
    profile: RuntimeIngressIssuanceStateProfile,
) -> bytes:
    if type(envelope) is not SignedPlanEnvelope:
        raise IssuedPlanCodecError("issued plan envelope must be exact")
    encoded = canonical_json(_envelope_mapping(envelope))
    if len(encoded) > profile.signed_envelope_canonical_bytes:
        raise IssuedPlanCodecError("issued plan envelope exceeds the admitted byte bound")
    return encoded


def decode_record(
    row: Mapping[str, object],
    profile: RuntimeIngressIssuanceStateProfile,
) -> IssuedPlanRecord:
    record = _decode_legacy_record(row, profile)
    if _stored_datetime(row.get("expires_at"), "expires at") != record.envelope.expires_at:
        raise IssuedPlanCodecError("stored issued-plan expiry differs from its envelope")
    return record


def project_legacy_expiry(
    row: Mapping[str, object],
    profile: RuntimeIngressIssuanceStateProfile,
) -> datetime:
    """Validate the whole pre-cutover record before its forward-migration backfill."""
    record = _decode_legacy_record(row, profile)
    if record.envelope.expires_at <= record.envelope.issued_at:
        raise IssuedPlanCodecError("legacy issued-plan expiry must follow issuance")
    return record.envelope.expires_at


def _decode_legacy_record(
    row: Mapping[str, object],
    profile: RuntimeIngressIssuanceStateProfile,
) -> IssuedPlanRecord:
    idempotency_key = _text(row.get("idempotency_key"), "idempotency key")
    record_id = _text(row.get("record_id"), "record id")
    request_hash = _digest(row.get("request_hash"), "request hash")
    installation_id = _positive(row.get("installation_id"), "installation id")
    repository_id = _positive(row.get("repository_id"), "repository id")
    issued_at = _stored_datetime(row.get("issued_at"), "issued at")
    _bounded(idempotency_key, profile.issuance_idempotency_key_utf8_bytes, "idempotency key")
    _bounded(record_id, profile.issued_plan_record_id_utf8_bytes, "record id")
    raw_envelope = _bytes(row.get("envelope_canonical_json"), "signed envelope")
    if not 1 <= len(raw_envelope) <= profile.signed_envelope_canonical_bytes:
        raise IssuedPlanCodecError("stored signed envelope violates the byte bound")
    decoded = _strict_json(raw_envelope)
    if canonical_json(decoded) != raw_envelope:
        raise IssuedPlanCodecError("stored signed envelope is not canonical JSON")
    envelope = _envelope(decoded)
    record = IssuedPlanRecord.create(idempotency_key, envelope.payload.request, envelope)
    if record.record_id != record_id or record.request_hash != request_hash:
        raise IssuedPlanCodecError("stored issued-plan identity does not match its envelope")
    repository = envelope.payload.repository
    stored_authority = row.get("production_admission_authority_id")
    if (
        installation_id != repository.installation_id
        or repository_id != repository.repository_id
        or issued_at != envelope.issued_at
        or stored_authority != envelope.payload.production_admission_receipt_id
    ):
        raise IssuedPlanCodecError("stored issued-plan projection does not match its envelope")
    return record


def _envelope_mapping(envelope: SignedPlanEnvelope) -> dict[str, object]:
    return {
        **envelope.unsigned_mapping(),
        "signature": envelope.signature,
    }


def _strict_json(value: bytes) -> object:
    try:
        return json.loads(value, object_pairs_hook=_no_duplicate_object)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError, IssuedPlanCodecError) as error:
        raise IssuedPlanCodecError("stored signed envelope is not valid JSON") from error


def _no_duplicate_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise IssuedPlanCodecError("stored signed envelope has duplicate JSON keys")
        result[key] = value
    return result


def _envelope(value: object) -> SignedPlanEnvelope:
    record = _record(
        value,
        {
            "schemaVersion",
            "keyId",
            "algorithm",
            "issuedAt",
            "expiresAt",
            "payload",
            "signature",
        },
        "signed envelope",
    )
    try:
        return SignedPlanEnvelope(
            schema_version=_envelope_schema_version(record["schemaVersion"]),
            key_id=_text(record["keyId"], "envelope key id"),
            algorithm=_algorithm(record["algorithm"]),
            issued_at=_datetime(record["issuedAt"], "envelope issued at"),
            expires_at=_datetime(record["expiresAt"], "envelope expires at"),
            payload=_payload(record["payload"]),
            signature=_text(record["signature"], "envelope signature"),
        )
    except (TypeError, ValueError) as error:
        raise IssuedPlanCodecError("stored signed envelope is invalid") from error


def _payload(value: object) -> SignedPlanPayload:
    record = _record(
        value,
        {
            "schemaVersion",
            "planId",
            "repository",
            "request",
            "authenticatedRun",
            "verifiedPlanId",
            "productionAdmissionReceiptId",
            "execution",
            "verifierVersion",
            "fallbackReason",
        },
        "signed plan payload",
    )
    try:
        return SignedPlanPayload(
            schema_version=_payload_schema_version(record["schemaVersion"]),
            plan_id=_text(record["planId"], "signed plan id"),
            repository=_repository(record["repository"]),
            request=_request(record["request"]),
            authenticated_run=_authenticated_run(record["authenticatedRun"]),
            verified_plan_id=_optional_text(record["verifiedPlanId"], "verified plan id"),
            production_admission_receipt_id=_optional_text(
                record["productionAdmissionReceiptId"],
                "production admission receipt id",
            ),
            execution=decode_execution(record["execution"]),
            verifier_version=_optional_text(record["verifierVersion"], "verifier version"),
            fallback_reason=_optional_text(record["fallbackReason"], "fallback reason"),
        )
    except (TypeError, ValueError) as error:
        raise IssuedPlanCodecError("stored signed payload is invalid") from error


def _repository(value: object) -> RepositoryBinding:
    record = _record(
        value,
        {"installationId", "repositoryId", "owner", "repository"},
        "repository binding",
    )
    return RepositoryBinding(
        installation_id=_positive(record["installationId"], "repository installation id"),
        repository_id=_positive(record["repositoryId"], "repository id"),
        owner=_text(record["owner"], "repository owner"),
        repository=_text(record["repository"], "repository name"),
    )


def _request(value: object) -> PlanRequest:
    record = _record(
        value,
        {
            "schemaVersion",
            "requestId",
            "installationId",
            "repositoryId",
            "owner",
            "repository",
            "eventName",
            "ref",
            "baseSha",
            "headSha",
            "executionSha",
            "workflowRunId",
            "runAttempt",
            "pullRequestNumber",
            "mergeGroupHeadRef",
        },
        "plan request",
    )
    event = record["eventName"]
    if event not in {"pull_request", "push", "merge_group"}:
        raise IssuedPlanCodecError("stored plan request event is invalid")
    return PlanRequest(
        schema_version=_request_schema_version(record["schemaVersion"]),
        request_id=_text(record["requestId"], "plan request id"),
        installation_id=_positive(record["installationId"], "plan installation id"),
        repository_id=_positive(record["repositoryId"], "plan repository id"),
        owner=_text(record["owner"], "plan owner"),
        repository=_text(record["repository"], "plan repository"),
        event_name=event,
        ref=_text(record["ref"], "plan ref"),
        base_sha=_git_sha(record["baseSha"], "plan base SHA"),
        head_sha=_git_sha(record["headSha"], "plan head SHA"),
        execution_sha=_git_sha(record["executionSha"], "plan execution SHA"),
        workflow_run_id=_positive(record["workflowRunId"], "workflow run id"),
        run_attempt=_positive(record["runAttempt"], "run attempt"),
        pull_request_number=_optional_positive(record["pullRequestNumber"], "pull request number"),
        merge_group_head_ref=_optional_text(record["mergeGroupHeadRef"], "merge group ref"),
    )


def _authenticated_run(value: object) -> AuthenticatedRunBinding:
    record = _record(
        value,
        {
            "issuer",
            "audience",
            "repository",
            "repositoryId",
            "ref",
            "executionSha",
            "runId",
            "runAttempt",
            "eventName",
            "workflowRef",
            "workflowSha",
            "jobWorkflowRef",
            "jobWorkflowSha",
            "checkRunId",
            "verifiedAt",
            "verifierVersion",
            "claimHash",
        },
        "authenticated run",
    )
    return AuthenticatedRunBinding(
        issuer=_text(record["issuer"], "OIDC issuer"),
        audience=_text(record["audience"], "OIDC audience"),
        repository=_text(record["repository"], "OIDC repository"),
        repository_id=_positive(record["repositoryId"], "OIDC repository id"),
        ref=_text(record["ref"], "OIDC ref"),
        execution_sha=_git_sha(record["executionSha"], "OIDC execution SHA"),
        run_id=_positive(record["runId"], "OIDC run id"),
        run_attempt=_positive(record["runAttempt"], "OIDC run attempt"),
        event_name=_text(record["eventName"], "OIDC event"),
        workflow_ref=_optional_text(record["workflowRef"], "workflow ref"),
        workflow_sha=_optional_text(record["workflowSha"], "workflow SHA"),
        job_workflow_ref=_optional_text(record["jobWorkflowRef"], "job workflow ref"),
        job_workflow_sha=_optional_text(record["jobWorkflowSha"], "job workflow SHA"),
        check_run_id=_optional_text(record["checkRunId"], "check run id"),
        verified_at=_datetime(record["verifiedAt"], "OIDC verification time"),
        verifier_version=_text(record["verifierVersion"], "OIDC verifier version"),
        claim_hash=_optional_text(record["claimHash"], "OIDC claim hash"),
    )


def _record(value: object, expected: set[str], context: str) -> dict[str, object]:
    record = _mapping(value, context)
    if set(record) != expected:
        raise IssuedPlanCodecError(f"stored {context} has unexpected or missing fields")
    return record


def _mapping(value: object, context: str) -> dict[str, object]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise IssuedPlanCodecError(f"stored {context} must be an object")
    return cast(dict[str, object], value)


def _text(value: object, context: str) -> str:
    if type(value) is not str or not value:
        raise IssuedPlanCodecError(f"stored {context} must be non-empty text")
    return value


def _optional_text(value: object, context: str) -> str | None:
    if value is None:
        return None
    return _text(value, context)


def _positive(value: object, context: str) -> int:
    if type(value) is not int or value < 1:
        raise IssuedPlanCodecError(f"stored {context} must be a positive integer")
    return value


def _optional_positive(value: object, context: str) -> int | None:
    if value is None:
        return None
    return _positive(value, context)


def _digest(value: object, context: str) -> str:
    text = _text(value, context)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise IssuedPlanCodecError(f"stored {context} must be a lowercase SHA-256 digest")
    return text


def _git_sha(value: object, context: str) -> str:
    text = _text(value, context)
    if not 40 <= len(text) <= 64 or any(character not in "0123456789abcdef" for character in text):
        raise IssuedPlanCodecError(f"stored {context} must be a lowercase Git SHA")
    return text


def _datetime(value: object, context: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(_text(value, context))
    except ValueError as error:
        raise IssuedPlanCodecError(f"stored {context} must be ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise IssuedPlanCodecError(f"stored {context} must be timezone-aware")
    return parsed


def _stored_datetime(value: object, context: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise IssuedPlanCodecError(f"stored {context} must be a timezone-aware datetime")
    return value


def _literal(value: object, expected: str) -> str:
    if value != expected:
        raise IssuedPlanCodecError(f"stored value must equal {expected}")
    return expected


def _envelope_schema_version(value: object) -> Literal["dynamic-ci-signed-plan-envelope/v1"]:
    _literal(value, "dynamic-ci-signed-plan-envelope/v1")
    return "dynamic-ci-signed-plan-envelope/v1"


def _payload_schema_version(value: object) -> Literal["dynamic-ci-signed-plan-payload/v2"]:
    _literal(value, "dynamic-ci-signed-plan-payload/v2")
    return "dynamic-ci-signed-plan-payload/v2"


def _request_schema_version(value: object) -> Literal["dynamic-ci-plan-request/v2"]:
    _literal(value, "dynamic-ci-plan-request/v2")
    return "dynamic-ci-plan-request/v2"


def _algorithm(value: object) -> Literal["Ed25519"]:
    _literal(value, "Ed25519")
    return "Ed25519"


def _bounded(value: str, maximum: int, context: str) -> None:
    if len(value.encode("utf-8")) > maximum:
        raise IssuedPlanCodecError(f"stored {context} exceeds the admitted byte bound")


def _bytes(value: object, context: str) -> bytes:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if type(value) is not bytes:
        raise IssuedPlanCodecError(f"stored {context} must be exact bytes")
    return value
