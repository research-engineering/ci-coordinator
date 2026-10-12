"""Private bounded diagnostics that never alter authoritative outcomes."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass
from pathlib import PurePath
from types import TracebackType
from typing import Final, Literal

from ci_coordinator.observability.logging import StructuredEventLogger

type RuntimeDiagnosticStage = Literal[
    "activity_cleanup",
    "active_config_epoch",
    "browser_oauth_callback",
    "candidate_context",
    "capacity_inputs",
    "capacity_projection",
    "ci_economics_collection",
    "ci_economics_expiry",
    "ci_economics_observation_cleanup",
    "ci_economics_tombstone_purge",
    "ci_observation_discovery",
    "ci_observation_gap_cleanup",
    "ci_history_collection",
    "ci_history_delivery",
    "ci_history_detail_cleanup",
    "http_request",
    "metrics_exposition",
    "planning_override",
    "production_authorization",
    "production_preauthorization",
    "reconciliation_loop",
    "reconciliation_registration",
    "reconciliation_round",
    "readiness_database",
    "readiness_dependency",
    "runner_snapshot",
]

_STAGES: Final = frozenset(
    {
        "activity_cleanup",
        "active_config_epoch",
        "browser_oauth_callback",
        "candidate_context",
        "capacity_inputs",
        "capacity_projection",
        "ci_economics_collection",
        "ci_economics_expiry",
        "ci_economics_observation_cleanup",
        "ci_economics_tombstone_purge",
        "ci_observation_discovery",
        "ci_observation_gap_cleanup",
        "ci_history_collection",
        "ci_history_delivery",
        "ci_history_detail_cleanup",
        "http_request",
        "metrics_exposition",
        "planning_override",
        "production_authorization",
        "production_preauthorization",
        "reconciliation_loop",
        "reconciliation_registration",
        "reconciliation_round",
        "readiness_database",
        "readiness_dependency",
        "runner_snapshot",
    }
)

_FIRST_PARTY_PACKAGE: Final = "ci_coordinator"
_FIRST_PARTY_SOURCE_ROOT: Final = PurePath(__file__).parent.parent
_FINGERPRINT_FRAMES: Final = 8
_FINGERPRINT_HEX_CHARACTERS: Final = 16
_MAX_COORDINATE_CHARACTERS: Final = 256


@dataclass(frozen=True, slots=True)
class _SourceCoordinate:
    module: str
    qualname: str
    line: int | None


class RuntimeDiagnosticObserver:
    """Project exception classes and first-party code coordinates, never failure data."""

    def __init__(self, logger: StructuredEventLogger) -> None:
        if type(logger) is not StructuredEventLogger:
            raise TypeError("runtime diagnostics require an exact structured logger")
        self._logger = logger

    def unexpected_failure(
        self,
        stage: RuntimeDiagnosticStage,
        error: BaseException,
        *,
        correlation_id: str | None = None,
    ) -> None:
        admitted_stage = stage if stage in _STAGES else "diagnostic_contract"
        exception_type: str | None = None
        coordinates: tuple[_SourceCoordinate, ...] = ()
        fingerprint: str | None = None
        try:
            exception_type = type(error).__name__
            coordinates = _first_party_coordinates(error)
            fingerprint = _failure_fingerprint(admitted_stage, error, coordinates)
        except Exception:
            # A hostile exception object degrades to the class-only record; it never escapes.
            coordinates = ()
            self._logger.record_instrumentation_failure()
        origin = coordinates[-1] if coordinates else None
        # Truncate emitted text only; the fingerprint above hashed the complete coordinates.
        self._logger.emit(
            {
                "event": "unexpected_failure",
                "exceptionType": exception_type,
                "failureFingerprint": fingerprint,
                "reason": "unexpected_exception",
                "sourceLine": None if origin is None else origin.line,
                "sourceModule": None if origin is None else _bounded(origin.module),
                "sourceQualname": None if origin is None else _bounded(origin.qualname),
                "stage": admitted_stage,
            },
            correlation_id=correlation_id,
            severity="ERROR",
        )


def _bounded(coordinate: str) -> str:
    return coordinate[:_MAX_COORDINATE_CHARACTERS]


def _first_party_coordinates(error: BaseException) -> tuple[_SourceCoordinate, ...]:
    # Read only code filename, qualname and line: never locals, globals or source text.
    retained: deque[_SourceCoordinate] = deque(maxlen=_FINGERPRINT_FRAMES)
    # Interpreter tracebacks are acyclic and carry real code objects; a substitute could loop
    # or inject exception-controlled text, so it fails the projection instead.
    traceback: object = error.__traceback__
    while traceback is not None:
        if type(traceback) is not TracebackType:
            raise TypeError("diagnostic projection requires an interpreter traceback")
        code = traceback.tb_frame.f_code
        module = _first_party_module(code.co_filename)
        if module is not None:
            retained.append(
                _SourceCoordinate(
                    module=module, qualname=code.co_qualname, line=traceback.tb_lineno
                )
            )
        traceback = traceback.tb_next
    return tuple(retained)


def _first_party_module(filename: str) -> str | None:
    # Pure text: compare normalized path parts; unresolved `..` stays a non-identifier part.
    path = PurePath(filename)
    if path.suffix != ".py" or not path.is_relative_to(_FIRST_PARTY_SOURCE_ROOT):
        return None
    parts = [*path.relative_to(_FIRST_PARTY_SOURCE_ROOT).parent.parts, path.stem]
    if not all(part.isidentifier() for part in parts):
        return None
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join((_FIRST_PARTY_PACKAGE, *parts))


def _failure_fingerprint(
    stage: str,
    error: BaseException,
    coordinates: tuple[_SourceCoordinate, ...],
) -> str:
    error_type = type(error)
    type_module: object = error_type.__module__
    identity = json.dumps(
        [
            stage,
            type_module if type(type_module) is str else None,
            error_type.__qualname__,
            [[item.module, item.qualname, item.line] for item in coordinates],
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("ascii")).hexdigest()
    return digest[:_FINGERPRINT_HEX_CHARACTERS]
