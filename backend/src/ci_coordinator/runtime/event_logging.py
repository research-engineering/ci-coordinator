from __future__ import annotations

from ci_coordinator.observability import StructuredEventLogger, default_structured_event_logger
from ci_coordinator.runtime_settings import load_bundled_build_identity


def runtime_event_logger() -> StructuredEventLogger:
    try:
        identity = load_bundled_build_identity()
    except (OSError, TypeError, ValueError):
        logger = default_structured_event_logger()
        logger.emit({"event": "build_identity_unavailable"})
        return logger
    if not identity.source_commit.strip("0") or not identity.release_identity.strip("0"):
        return default_structured_event_logger()
    return default_structured_event_logger(
        source_commit=identity.source_commit, release_identity=identity.release_identity
    )
