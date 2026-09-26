"""Bounded structured logging with deterministic secret redaction."""

from __future__ import annotations

import json
import logging
import math
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Final, Literal, TextIO, cast

type LogValue = str | int | float | bool | tuple["LogValue", ...] | dict[str, "LogValue"] | None
type LogSeverity = Literal["INFO", "WARNING", "ERROR"]

_LEVELS: Final = {"INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}

_LOGGER_NAME: Final = "ci_coordinator.events"
_SERVICE_NAME: Final = "ci-coordinator"
_MAX_DEPTH: Final = 8
_MAX_NODES: Final = 256
_MAX_COLLECTION_ITEMS: Final = 64
_MAX_KEY_BYTES: Final = 128
_MAX_SCALAR_BYTES: Final = 4_096
_MAX_RECORD_BYTES: Final = 65_536
_MAX_SAFE_JSON_INTEGER: Final = 9_007_199_254_740_991
_REDACTED: Final = "[REDACTED]"
_UNSUPPORTED: Final = "[UNSUPPORTED]"
_NON_FINITE: Final = "[NON_FINITE_NUMBER]"
_INTEGER_OUT_OF_RANGE: Final = "[INTEGER_OUT_OF_RANGE]"
_CYCLE: Final = "[CYCLE]"
_DEPTH_LIMIT: Final = "[DEPTH_LIMIT]"
_NODE_LIMIT: Final = "[NODE_LIMIT]"
_RECORD_LIMIT: Final = "[RECORD_LIMIT]"
_TRUNCATED_SUFFIX: Final = "[TRUNCATED]"
_RESERVED_FIELDS: Final = frozenset(
    {
        "correlationId",
        "level",
        "logSanitization",
        "observedAt",
        "service",
        "sourceCommit",
        "releaseIdentity",
    }
)
_SECRET_TOKENS: Final = frozenset(
    {
        "authorization",
        "cookie",
        "credential",
        "dsn",
        "key",
        "passphrase",
        "password",
        "secret",
        "session",
        "signature",
        "token",
        "verifier",
    }
)
_SECRET_NAMES: Final = frozenset(
    {
        "accesskey",
        "accesstoken",
        "apikey",
        "authcode",
        "authorizationcode",
        "clientsecret",
        "code",
        "codeverifier",
        "encryptionkey",
        "githubcode",
        "oauthcode",
        "privatekey",
        "refreshtoken",
        "secretkey",
        "sessioncookie",
        "sessiontoken",
        "signingkey",
        "webhooksecret",
    }
)
_AUTHORIZATION_CODE_CONTEXT: Final = frozenset({"auth", "authorization", "github", "oauth"})
_KEY_TOKEN = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")


@dataclass(slots=True)
class _SanitizationState:
    nodes: int = 0
    active_container_ids: set[int] = field(default_factory=set)
    warnings: set[str] = field(default_factory=set)

    def admit_node(self) -> bool:
        if self.nodes >= _MAX_NODES:
            self.warnings.add("node_limit")
            return False
        self.nodes += 1
        return True


class StructuredEventLogger:
    """Emit canonical bounded JSON without becoming authoritative behavior."""

    def __init__(
        self,
        logger: logging.Logger,
        *,
        source_commit: str | None = None,
        release_identity: str | None = None,
    ) -> None:
        if not isinstance(logger, logging.Logger):
            raise TypeError("structured event logger requires a logging.Logger")
        self._logger = logger
        if (source_commit is None) != (release_identity is None):
            raise ValueError("log build identity must be a complete pair")
        if source_commit is not None and (
            re.fullmatch(r"[0-9a-f]{40,64}", source_commit) is None
            or re.fullmatch(r"[0-9a-f]{64}", release_identity or "") is None
        ):
            raise ValueError("log build identity must contain canonical digests")
        self._source_commit = source_commit
        self._release_identity = release_identity
        self._failure_count = 0
        self._failure_lock = Lock()

    @property
    def failure_count(self) -> int:
        """Return instrumentation failures without exposing mutable state."""
        with self._failure_lock:
            return self._failure_count

    def emit(
        self,
        event: Mapping[str, object],
        *,
        correlation_id: str | None = None,
        severity: LogSeverity = "INFO",
    ) -> None:
        """Emit one event; malformed telemetry can never affect its caller."""
        admitted_correlation_id: str | None = None
        try:
            admitted_correlation_id = _admit_correlation_id(correlation_id)
            if type(severity) is not str or severity not in _LEVELS:
                raise ValueError("structured log severity is not admitted")
            redacted, warnings = _sanitize_event(event)
            record: dict[str, LogValue] = {
                **redacted,
                "correlationId": admitted_correlation_id,
                "level": severity,
                "observedAt": datetime.now(UTC).isoformat(),
                "service": _SERVICE_NAME,
                "sourceCommit": self._source_commit,
                "releaseIdentity": self._release_identity,
            }
            if warnings:
                record["logSanitization"] = warnings
            encoded = _encode_bounded_record(record)
            self._logger.log(_LEVELS[severity], encoded.decode("ascii"))
        except Exception:
            self._record_failure()
            if admitted_correlation_id is not None:
                self._emit_fallback(admitted_correlation_id)

    def _record_failure(self) -> None:
        with self._failure_lock:
            self._failure_count += 1

    def _emit_fallback(self, correlation_id: str) -> None:
        try:
            record = {
                "correlationId": correlation_id,
                "event": "structured_log_failure",
                "level": "ERROR",
                "observedAt": datetime.now(UTC).isoformat(),
                "reason": "instrumentation_failure",
                "service": _SERVICE_NAME,
                "sourceCommit": self._source_commit,
                "releaseIdentity": self._release_identity,
            }
            self._logger.error(_encode_record(record).decode("ascii"))
        except Exception:
            return


class _ContainedStreamHandler(logging.StreamHandler[TextIO]):
    def handleError(self, record: logging.LogRecord) -> None:
        # Let the structured boundary observe sink failure without stdlib's raw stderr fallback.
        raise RuntimeError("structured log sink failed") from None


def default_structured_event_logger(
    *, source_commit: str | None = None, release_identity: str | None = None
) -> StructuredEventLogger:
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.filters or logger.disabled:
        raise RuntimeError("structured logging requires the dedicated runtime logger")
    if logger.handlers:
        handler = logger.handlers[0]
        if (
            len(logger.handlers) != 1
            or type(handler) is not _ContainedStreamHandler
            or handler.filters
            or handler.level != logging.NOTSET
            or logger.level != logging.INFO
            or logger.propagate
        ):
            raise RuntimeError("structured logging requires the dedicated runtime logger")
    elif logger.level != logging.NOTSET or not logger.propagate:
        raise RuntimeError("structured logging requires the dedicated runtime logger")
    if not logger.handlers:
        handler = _ContainedStreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return StructuredEventLogger(
        logger, source_commit=source_commit, release_identity=release_identity
    )


def redacted_log_event(event: Mapping[str, object]) -> dict[str, LogValue]:
    """Return the bounded caller-owned portion of one structured event."""
    redacted, _warnings = _sanitize_event(event)
    return redacted


def _sanitize_event(event: Mapping[str, object]) -> tuple[dict[str, LogValue], tuple[str, ...]]:
    if not isinstance(event, Mapping):
        raise TypeError("structured event must be a mapping")
    state = _SanitizationState()
    result = _sanitize_mapping(
        cast("Mapping[object, object]", event),
        state,
        depth=0,
        top_level=True,
    )
    return result, tuple(sorted(state.warnings))


def _sanitize_mapping(
    value: Mapping[object, object],
    state: _SanitizationState,
    *,
    depth: int,
    top_level: bool = False,
) -> dict[str, LogValue]:
    container_id = id(value)
    if container_id in state.active_container_ids:
        state.warnings.add("cycle")
        return {"value": _CYCLE}
    state.active_container_ids.add(container_id)
    result: dict[str, LogValue] = {}
    try:
        for index, (key, nested_value) in enumerate(value.items()):
            if index >= _MAX_COLLECTION_ITEMS:
                state.warnings.add("collection_limit")
                break
            if type(key) is not str:
                state.warnings.add("non_string_key")
                continue
            if top_level and key in _RESERVED_FIELDS:
                state.warnings.add("reserved_field")
                continue
            if len(key[: _MAX_KEY_BYTES + 1].encode("utf-8")) > _MAX_KEY_BYTES:
                state.warnings.add("key_limit")
                continue
            result[key] = _sanitize_value(key, nested_value, state, depth=depth + 1)
    finally:
        state.active_container_ids.remove(container_id)
    return result


def _sanitize_value(
    key: str,
    value: object,
    state: _SanitizationState,
    *,
    depth: int,
) -> LogValue:
    if _is_secret_key(key):
        return _REDACTED
    if not state.admit_node():
        return _NODE_LIMIT
    if depth > _MAX_DEPTH:
        state.warnings.add("depth_limit")
        return _DEPTH_LIMIT
    if value is None or type(value) is bool:
        return value
    if type(value) is str:
        return _bounded_text(value, state)
    if type(value) is int:
        if -_MAX_SAFE_JSON_INTEGER <= value <= _MAX_SAFE_JSON_INTEGER:
            return value
        state.warnings.add("integer_range")
        return _INTEGER_OUT_OF_RANGE
    if type(value) is float:
        if math.isfinite(value):
            return value
        state.warnings.add("non_finite_number")
        return _NON_FINITE
    if type(value) is dict:
        if id(value) in state.active_container_ids:
            state.warnings.add("cycle")
            return _CYCLE
        return _sanitize_mapping(value, state, depth=depth)
    if type(value) in (list, tuple):
        sequence = cast("list[object] | tuple[object, ...]", value)
        return _sanitize_sequence(sequence, state, depth=depth)
    state.warnings.add("unsupported_value")
    return _UNSUPPORTED


def _sanitize_sequence(
    value: list[object] | tuple[object, ...],
    state: _SanitizationState,
    *,
    depth: int,
) -> tuple[LogValue, ...] | str:
    container_id = id(value)
    if container_id in state.active_container_ids:
        state.warnings.add("cycle")
        return _CYCLE
    state.active_container_ids.add(container_id)
    try:
        if len(value) > _MAX_COLLECTION_ITEMS:
            state.warnings.add("collection_limit")
        return tuple(
            _sanitize_value("item", item, state, depth=depth + 1)
            for item in value[:_MAX_COLLECTION_ITEMS]
        )
    finally:
        state.active_container_ids.remove(container_id)


def _bounded_text(value: str, state: _SanitizationState) -> str:
    candidate = value[: _MAX_SCALAR_BYTES + 1]
    encoded = candidate.encode("utf-8")
    if len(value) <= _MAX_SCALAR_BYTES and len(encoded) <= _MAX_SCALAR_BYTES:
        return value
    state.warnings.add("scalar_limit")
    available = _MAX_SCALAR_BYTES - len(_TRUNCATED_SUFFIX)
    prefix = encoded[:available].decode("utf-8", errors="ignore")
    return prefix + _TRUNCATED_SUFFIX


def _is_secret_key(key: str) -> bool:
    tokens = tuple(token.casefold() for token in _KEY_TOKEN.findall(key))
    normalized = "".join(tokens)
    return (
        bool(_SECRET_TOKENS.intersection(tokens))
        or normalized in _SECRET_TOKENS
        or normalized in _SECRET_NAMES
        or ("code" in tokens and bool(_AUTHORIZATION_CODE_CONTEXT.intersection(tokens)))
    )


def _admit_correlation_id(value: str | None) -> str:
    if (
        type(value) is str
        and len(value) == 32
        and all(character in "0123456789abcdef" for character in value)
    ):
        return value
    return secrets.token_hex(16)


def _encode_record(record: Mapping[str, LogValue]) -> bytes:
    return json.dumps(
        record,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _encode_bounded_record(record: dict[str, LogValue]) -> bytes:
    encoded = _encode_record(record)
    if len(encoded) <= _MAX_RECORD_BYTES:
        return encoded

    existing_warnings = record.get("logSanitization", ())
    warnings = cast("tuple[LogValue, ...]", existing_warnings)
    record["logSanitization"] = tuple(
        sorted({cast(str, warning) for warning in warnings} | {"record_limit"})
    )
    caller_fields = sorted(
        (key for key in record if key not in _RESERVED_FIELDS and key != "event"),
        key=lambda key: (-len(_encode_record({"value": record[key]})), key),
    )
    if "event" in record:
        caller_fields.append("event")
    for key in caller_fields:
        record[key] = _RECORD_LIMIT
        encoded = _encode_record(record)
        if len(encoded) <= _MAX_RECORD_BYTES:
            return encoded
    raise ValueError("structured log cannot be projected within the record budget")
