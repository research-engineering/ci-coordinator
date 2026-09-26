from __future__ import annotations

import logging
from typing import Final, Literal

from ci_coordinator.observability import StructuredEventLogger, default_structured_event_logger
from ci_coordinator.runtime_settings import load_bundled_build_identity

_SERVER_SEVERITIES: Final[dict[int, Literal["INFO", "WARNING", "ERROR"]]] = {
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
}
# Exact Uvicorn 0.53 templates; arguments and unmatched text never enter events.
_SERVER_REASONS: Final = {
    (logging.INFO, "Started server process [%d]"): "process_started",
    (logging.INFO, "Finished server process [%d]"): "process_finished",
    (logging.INFO, "Waiting for application startup."): "startup_waiting",
    (logging.INFO, "Application startup complete."): "startup_complete",
    (logging.ERROR, "Application startup failed. Exiting."): "startup_failed",
    (logging.INFO, "Shutting down"): "shutdown_started",
    (logging.INFO, "Waiting for application shutdown."): "shutdown_waiting",
    (logging.INFO, "Application shutdown complete."): "shutdown_complete",
    (logging.ERROR, "Application shutdown failed. Exiting."): "shutdown_failed",
    (logging.INFO, "ASGI 'lifespan' protocol appears unsupported."): "lifespan_unsupported",
    (logging.ERROR, "Exception in 'lifespan' protocol\n"): "lifespan_exception",
    (logging.ERROR, "Exception in ASGI application\n"): "asgi_exception",
    (logging.ERROR, "ASGI callable should return None, but returned '%s'."): "asgi_invalid_return",
    (logging.ERROR, "ASGI callable returned without starting response."): "asgi_response_absent",
    (
        logging.ERROR,
        "ASGI callable returned without completing response.",
    ): "asgi_response_incomplete",
    (logging.WARNING, "Invalid HTTP request received."): "invalid_http_request",
    (logging.WARNING, "Exceeded concurrency limit."): "concurrency_limit",
    (logging.WARNING, "Unsupported upgrade request."): "unsupported_upgrade",
    (logging.INFO, "Uvicorn running on %s://%s:%d (Press CTRL+C to quit)"): "listener_started",
    (logging.INFO, "Uvicorn running on %s://[%s]:%d (Press CTRL+C to quit)"): "listener_started",
    (logging.INFO, "Uvicorn running on socket %s (Press CTRL+C to quit)"): "listener_started",
    (logging.INFO, "Uvicorn running on unix socket %s (Press CTRL+C to quit)"): "listener_started",
    (
        logging.INFO,
        "Waiting for connections to close. (CTRL+C to force quit)",
    ): "connections_draining",
    (
        logging.INFO,
        "Waiting for background tasks to complete. (CTRL+C to force quit)",
    ): "tasks_draining",
    (
        logging.ERROR,
        "Cancel %s running task(s), timeout graceful shutdown exceeded",
    ): "drain_timeout",
    (logging.INFO, "Maximum request limit of %d exceeded. Terminating process."): "request_limit",
}


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


class _ServerLogHandler(logging.Handler):
    def __init__(self, events: StructuredEventLogger) -> None:
        super().__init__()
        self._events = events

    def emit(self, record: logging.LogRecord) -> None:
        try:
            event, severity = _project_server_record(record)
        except Exception:
            event = {"event": "server_log", "reason": "diagnostic_contract"}
            severity = "ERROR"
        self._events.emit(event, severity=severity)


def _project_server_record(
    record: logging.LogRecord,
) -> tuple[dict[str, object], Literal["INFO", "WARNING", "ERROR"]]:
    if type(record.levelno) is not int or record.levelno not in _SERVER_SEVERITIES:
        raise ValueError("server log severity is not admitted")
    reason = "unclassified_server_event"
    if type(record.msg) is str and len(record.msg) <= 256:
        reason = _SERVER_REASONS.get((record.levelno, record.msg), reason)
    event: dict[str, object] = {"event": "server_log", "reason": reason}
    exception = record.exc_info
    if exception is not None:
        if type(exception) is not tuple or len(exception) != 3:
            raise ValueError("server exception information is not admitted")
        if exception[1] is not None:
            if not isinstance(exception[1], BaseException):
                raise ValueError("server exception value is not admitted")
            event["exceptionType"] = type(exception[1]).__name__
    return event, _SERVER_SEVERITIES[record.levelno]


def configure_runtime_server_logging() -> None:
    """Install the dedicated-process profile without altering foreign logging owners."""
    parent, errors, access = (
        logging.getLogger(name) for name in ("uvicorn", "uvicorn.error", "uvicorn.access")
    )
    if (
        any(logger.filters or logger.disabled for logger in (parent, errors, access))
        or errors.handlers
        or access.handlers
    ):
        raise RuntimeError("server logging requires the dedicated runtime profile")
    if parent.handlers:
        if (
            len(parent.handlers) != 1
            or type(parent.handlers[0]) is not _ServerLogHandler
            or parent.handlers[0].filters
            or parent.handlers[0].formatter is not None
            or parent.handlers[0].level != logging.NOTSET
            or any(logger.level != logging.INFO for logger in (parent, errors, access))
            or parent.propagate
            or not errors.propagate
            or access.propagate
        ):
            raise RuntimeError("server logging requires the dedicated runtime profile")
        return
    if any(
        logger.level != logging.NOTSET or not logger.propagate
        for logger in (parent, errors, access)
    ):
        raise RuntimeError("server logging requires an unconfigured dedicated process")
    handler = _ServerLogHandler(runtime_event_logger())
    parent.addHandler(handler)
    for logger in (parent, errors, access):
        logger.setLevel(logging.INFO)
    parent.propagate = False
    access.propagate = False
