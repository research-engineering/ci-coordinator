from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, NoReturn, cast

from ci_coordinator.audit_replay.checkpoint import (
    AUDIT_CHECKPOINT_STATEMENT_SCHEMA,
    AuditChainTipStatement,
)
from ci_coordinator.kernel import (
    Clock,
    JsonResourceLimits,
    admit_ed25519_public_key,
    canonical_json,
    load_strict_json,
    sha256_hex,
    verify_canonical_ed25519_signature,
)

AUDIT_CHECKPOINT_ENVELOPE_SCHEMA = "ci-audit-checkpoint-envelope/v1"
AUDIT_CHECKPOINT_ALGORITHM = "Ed25519"
MAX_AUDIT_CHECKPOINT_BYTES = 65_536
MAX_AUDIT_CHECKPOINT_LIFETIME = timedelta(days=7)
MAX_AUDIT_CHECKPOINT_SIGNING_DELAY = timedelta(days=1)
MAX_AUDIT_CHECKPOINT_CLOCK_SKEW = timedelta(minutes=5)
_JSON_LIMITS = JsonResourceLimits(max_depth=4, max_nodes=64)


@dataclass(frozen=True, slots=True)
class AuditCheckpointRejection:
    code: Literal[
        "audit_checkpoint_expired",
        "audit_checkpoint_foreign",
        "audit_checkpoint_invalid",
        "audit_checkpoint_key_mismatch",
        "audit_checkpoint_missing",
        "audit_checkpoint_not_yet_valid",
        "audit_checkpoint_signature_invalid",
    ]
    authenticated: Literal[False] = False


_AUTHENTICATED_TOKEN = object()


class AuthenticatedAuditCheckpoint:
    __envelope_digest: str
    __expires_at: datetime
    __key_id: str
    __statement: AuditChainTipStatement
    __valid_from: datetime

    __slots__ = (
        "__envelope_digest",
        "__expires_at",
        "__key_id",
        "__statement",
        "__valid_from",
    )

    def __init__(
        self,
        token: object,
        *,
        envelope_digest: str,
        key_id: str,
        statement: AuditChainTipStatement,
        valid_from: datetime,
        expires_at: datetime,
    ) -> None:
        if token is not _AUTHENTICATED_TOKEN:
            raise TypeError("AuthenticatedAuditCheckpoint cannot be constructed directly")
        object.__setattr__(self, "_AuthenticatedAuditCheckpoint__envelope_digest", envelope_digest)
        object.__setattr__(self, "_AuthenticatedAuditCheckpoint__key_id", key_id)
        object.__setattr__(self, "_AuthenticatedAuditCheckpoint__statement", statement)
        object.__setattr__(self, "_AuthenticatedAuditCheckpoint__valid_from", valid_from)
        object.__setattr__(self, "_AuthenticatedAuditCheckpoint__expires_at", expires_at)

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        raise TypeError("AuthenticatedAuditCheckpoint is immutable")

    def __reduce__(self) -> NoReturn:
        raise TypeError("AuthenticatedAuditCheckpoint cannot be serialized")

    @property
    def authenticated(self) -> Literal[True]:
        return True

    @property
    def envelope_digest(self) -> str:
        return self.__envelope_digest

    @property
    def key_id(self) -> str:
        return self.__key_id

    @property
    def statement(self) -> AuditChainTipStatement:
        return self.__statement

    def is_valid_at(self, value: datetime) -> bool:
        return (
            type(value) is datetime
            and value.tzinfo is not None
            and value.utcoffset() is not None
            and self.__valid_from <= value.astimezone(UTC) < self.__expires_at
        )


type AuditCheckpointAdmissionResult = AuthenticatedAuditCheckpoint | AuditCheckpointRejection


@dataclass(frozen=True, slots=True)
class _DecodedCheckpointEnvelope:
    key_id: str
    signed_at: datetime
    expires_at: datetime
    statement: AuditChainTipStatement
    signature: str

    def unsigned_mapping(self) -> dict[str, object]:
        return _unsigned_mapping(
            key_id=self.key_id,
            signed_at=self.signed_at,
            expires_at=self.expires_at,
            statement=self.statement,
        )


def audit_checkpoint_signature_payload(
    statement: AuditChainTipStatement,
    *,
    key_id: str,
    signed_at: datetime,
    expires_at: datetime,
) -> bytes:
    _validate_envelope_fields(key_id, signed_at, expires_at, statement)
    return canonical_json(
        _unsigned_mapping(
            key_id=key_id,
            signed_at=signed_at,
            expires_at=expires_at,
            statement=statement,
        ),
        resource_limits=_JSON_LIMITS,
    )


def encode_audit_checkpoint_envelope(
    statement: AuditChainTipStatement,
    *,
    key_id: str,
    signed_at: datetime,
    expires_at: datetime,
    signature: str,
) -> bytes:
    _validate_envelope_fields(key_id, signed_at, expires_at, statement)
    _require_signature(signature)
    return (
        canonical_json(
            {
                **_unsigned_mapping(
                    key_id=key_id,
                    signed_at=signed_at,
                    expires_at=expires_at,
                    statement=statement,
                ),
                "signature": signature,
            },
            resource_limits=_JSON_LIMITS,
        )
        + b"\n"
    )


def admit_audit_checkpoint(
    content: bytes | None,
    *,
    public_key_pem: bytes,
    expected_key_id: str,
    expected_database_identity_digest: str,
    expected_artifact_digest: str,
    clock: Clock,
) -> AuditCheckpointAdmissionResult:
    if content is None:
        return AuditCheckpointRejection("audit_checkpoint_missing")
    try:
        envelope = _decode_envelope(content)
    except (TypeError, ValueError):
        return AuditCheckpointRejection("audit_checkpoint_invalid")
    if envelope.key_id != expected_key_id:
        return AuditCheckpointRejection("audit_checkpoint_key_mismatch")
    public_key = admit_ed25519_public_key(public_key_pem)
    if public_key is None or not verify_canonical_ed25519_signature(
        public_key,
        signature=envelope.signature,
        payload=canonical_json(envelope.unsigned_mapping(), resource_limits=_JSON_LIMITS),
    ):
        return AuditCheckpointRejection("audit_checkpoint_signature_invalid")
    now = clock.now()
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        return AuditCheckpointRejection("audit_checkpoint_invalid")
    now = now.astimezone(UTC)
    if envelope.signed_at > now + MAX_AUDIT_CHECKPOINT_CLOCK_SKEW:
        return AuditCheckpointRejection("audit_checkpoint_not_yet_valid")
    if now >= envelope.expires_at:
        return AuditCheckpointRejection("audit_checkpoint_expired")
    if (
        envelope.expires_at - envelope.signed_at > MAX_AUDIT_CHECKPOINT_LIFETIME
        or not envelope.statement.observed_at <= envelope.signed_at
        or envelope.signed_at - envelope.statement.observed_at > MAX_AUDIT_CHECKPOINT_SIGNING_DELAY
    ):
        return AuditCheckpointRejection("audit_checkpoint_invalid")
    if (
        envelope.statement.database_identity_digest != expected_database_identity_digest
        or envelope.statement.artifact_digest != expected_artifact_digest
    ):
        return AuditCheckpointRejection("audit_checkpoint_foreign")
    return AuthenticatedAuditCheckpoint(
        _AUTHENTICATED_TOKEN,
        envelope_digest=sha256_hex(content),
        key_id=envelope.key_id,
        statement=envelope.statement,
        valid_from=envelope.signed_at - MAX_AUDIT_CHECKPOINT_CLOCK_SKEW,
        expires_at=envelope.expires_at,
    )


def _decode_envelope(content: bytes) -> _DecodedCheckpointEnvelope:
    root = _exact_object(
        load_strict_json(
            content,
            max_bytes=MAX_AUDIT_CHECKPOINT_BYTES,
            resource_limits=_JSON_LIMITS,
        ),
        {
            "algorithm",
            "expiresAt",
            "keyId",
            "schemaVersion",
            "signature",
            "signedAt",
            "statement",
        },
    )
    if canonical_json(root, resource_limits=_JSON_LIMITS) + b"\n" != content:
        raise ValueError("audit checkpoint envelope is not canonical")
    if (
        root["schemaVersion"] != AUDIT_CHECKPOINT_ENVELOPE_SCHEMA
        or root["algorithm"] != AUDIT_CHECKPOINT_ALGORITHM
    ):
        raise ValueError("audit checkpoint envelope identity is unsupported")
    statement = _statement(root["statement"])
    key_id = _text(root["keyId"], "audit checkpoint key id")
    signed_at = _timestamp(root["signedAt"])
    expires_at = _timestamp(root["expiresAt"])
    signature = _text(root["signature"], "audit checkpoint signature")
    _validate_envelope_fields(key_id, signed_at, expires_at, statement)
    _require_signature(signature)
    return _DecodedCheckpointEnvelope(
        key_id=key_id,
        signed_at=signed_at,
        expires_at=expires_at,
        statement=statement,
        signature=signature,
    )


def _statement(value: object) -> AuditChainTipStatement:
    record = _exact_object(
        value,
        {
            "artifactDigest",
            "databaseIdentityDigest",
            "lastEventHash",
            "lastEventId",
            "ledgerSchema",
            "observedAt",
            "retainedSequence",
            "schemaVersion",
            "statementDigest",
        },
    )
    if (
        record["schemaVersion"] != AUDIT_CHECKPOINT_STATEMENT_SCHEMA
        or record["ledgerSchema"] != "ci-audit-event/v1"
    ):
        raise ValueError("audit checkpoint statement identity is unsupported")
    statement = AuditChainTipStatement(
        database_identity_digest=_text(
            record["databaseIdentityDigest"], "database identity digest"
        ),
        artifact_digest=_text(record["artifactDigest"], "artifact digest"),
        retained_sequence=_integer(record["retainedSequence"], "retained sequence"),
        last_event_id=_text(record["lastEventId"], "last event id"),
        last_event_hash=_text(record["lastEventHash"], "last event hash"),
        observed_at=_timestamp(record["observedAt"]),
    )
    if record["statementDigest"] != statement.statement_digest:
        raise ValueError("audit checkpoint statement digest does not match")
    return statement


def _unsigned_mapping(
    *,
    key_id: str,
    signed_at: datetime,
    expires_at: datetime,
    statement: AuditChainTipStatement,
) -> dict[str, object]:
    return {
        "algorithm": AUDIT_CHECKPOINT_ALGORITHM,
        "expiresAt": _format_timestamp(expires_at),
        "keyId": key_id,
        "schemaVersion": AUDIT_CHECKPOINT_ENVELOPE_SCHEMA,
        "signedAt": _format_timestamp(signed_at),
        "statement": statement.to_mapping(),
    }


def _validate_envelope_fields(
    key_id: object,
    signed_at: object,
    expires_at: object,
    statement: object,
) -> None:
    if (
        type(key_id) is not str
        or not 1 <= len(key_id) <= 128
        or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
            for character in key_id
        )
    ):
        raise ValueError("audit checkpoint key id is invalid")
    if type(statement) is not AuditChainTipStatement:
        raise TypeError("audit checkpoint statement must be exact")
    for name, value in (("signedAt", signed_at), ("expiresAt", expires_at)):
        if (
            type(value) is not datetime
            or value.tzinfo is None
            or value.utcoffset() is None
            or value.astimezone(UTC).utcoffset() != value.utcoffset()
            or value.microsecond % 1_000 != 0
        ):
            raise ValueError(f"audit checkpoint {name} is invalid")
    if cast(datetime, signed_at) >= cast(datetime, expires_at):
        raise ValueError("audit checkpoint validity interval is invalid")


def _require_signature(value: object) -> None:
    if type(value) is not str or len(value) != 86:
        raise ValueError("audit checkpoint signature is invalid")


def _exact_object(value: object, keys: set[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError("audit checkpoint object shape is invalid")
    return cast(dict[str, object], value)


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > 512:
        raise ValueError(f"{name} is invalid")
    return value


def _integer(value: object, name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} is invalid")
    return value


def _timestamp(value: object) -> datetime:
    text = _text(value, "audit checkpoint timestamp")
    try:
        parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise ValueError("audit checkpoint timestamp is invalid") from error
    if _format_timestamp(parsed) != text:
        raise ValueError("audit checkpoint timestamp is noncanonical")
    return parsed


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
