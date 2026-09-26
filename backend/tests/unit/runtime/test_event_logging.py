from __future__ import annotations

import json
import logging
from io import StringIO
from typing import Any, cast

import pytest

from ci_coordinator.observability import StructuredEventLogger
from ci_coordinator.runtime import event_logging
from ci_coordinator.runtime_settings import BuildIdentity


@pytest.mark.parametrize("shape", ["ordinary", "oversized", "failure"])
def test_build_fields_survive_spoofing_truncation_and_fallback(shape: str) -> None:
    output = StringIO()
    sink = logging.Logger("build-identity-test")
    sink.addHandler(logging.StreamHandler(output))
    logger = StructuredEventLogger(sink, source_commit="a" * 40, release_identity="b" * 64)
    payload: dict[str, object] = {
        "event": "sample",
        "sourceCommit": "forged",
        "releaseIdentity": "forged",
        "password": "private-value",
    }
    if shape == "oversized":
        payload.update({f"payload{i}": "x" * 4096 for i in range(50)})
    elif shape == "failure":
        payload["malformed"] = "\ud800"
    logger.emit(payload)
    text = output.getvalue()
    record = json.loads(text)
    assert record["sourceCommit"] == "a" * 40
    assert record["releaseIdentity"] == "b" * 64
    assert "forged" not in text
    assert "private-value" not in text
    assert len(text.encode()) <= 65537
    assert logger.failure_count == (1 if shape == "failure" else 0)


@pytest.mark.parametrize(
    "source,release", [("a" * 40, None), (None, "b" * 64), ("invalid", "b" * 64)]
)
def test_log_identity_is_a_valid_complete_pair(source: str | None, release: str | None) -> None:
    with pytest.raises(ValueError):
        StructuredEventLogger(
            logging.Logger("probe"), source_commit=source, release_identity=release
        )


@pytest.mark.parametrize("identity_kind", ["release", "development", "unavailable"])
def test_runtime_loads_identity_without_making_diagnostics_an_authority(
    monkeypatch: pytest.MonkeyPatch, identity_kind: str
) -> None:
    output = StringIO()
    sink = logging.Logger("runtime-build-test")
    sink.addHandler(logging.StreamHandler(output))

    def load() -> BuildIdentity:
        if identity_kind == "unavailable":
            raise ValueError("private parse message")
        return (
            BuildIdentity("b" * 64, "a" * 40, True)
            if identity_kind == "release"
            else BuildIdentity("0" * 64, "0" * 40, False)
        )

    def factory(
        *, source_commit: str | None = None, release_identity: str | None = None
    ) -> StructuredEventLogger:
        return StructuredEventLogger(
            sink, source_commit=source_commit, release_identity=release_identity
        )

    monkeypatch.setattr(event_logging, "load_bundled_build_identity", load)
    monkeypatch.setattr(event_logging, "default_structured_event_logger", factory)
    logger = event_logging.runtime_event_logger()
    logger.emit({"event": "ready"})
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert rows[-1]["sourceCommit"] == ("a" * 40 if identity_kind == "release" else None)
    assert rows[-1]["releaseIdentity"] == ("b" * 64 if identity_kind == "release" else None)
    assert len(rows) == (2 if identity_kind == "unavailable" else 1)
    assert "private parse message" not in output.getvalue()


@pytest.fixture
def server_output(monkeypatch: pytest.MonkeyPatch) -> StringIO:
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        for attribute, value in {
            "handlers": [],
            "filters": [],
            "disabled": False,
            "level": logging.NOTSET,
            "propagate": True,
            "_cache": {},
        }.items():
            monkeypatch.setattr(logger, attribute, value)
    output = StringIO()
    sink = logging.Logger("server-projection-test", logging.INFO)
    sink.addHandler(logging.StreamHandler(output))
    events = StructuredEventLogger(sink, source_commit="a" * 40, release_identity="b" * 64)
    monkeypatch.setattr(event_logging, "runtime_event_logger", lambda: events)
    return output


@pytest.mark.parametrize(
    "level,message,reason",
    [
        (logging.INFO, "Waiting for application startup.", "startup_waiting"),
        (logging.INFO, "Application startup complete.", "startup_complete"),
        (logging.ERROR, "Application startup failed. Exiting.", "startup_failed"),
        (logging.INFO, "Application shutdown complete.", "shutdown_complete"),
        (logging.ERROR, "Application shutdown failed. Exiting.", "shutdown_failed"),
        (logging.WARNING, "Exceeded concurrency limit.", "concurrency_limit"),
        (logging.WARNING, "Invalid HTTP request received.", "invalid_http_request"),
        (logging.ERROR, "Application startup complete.", "unclassified_server_event"),
        (
            logging.ERROR,
            "Traceback\nprivate diagnostic\nRuntimeError: private-value",
            "unclassified_server_event",
        ),
        (logging.INFO, "unknown future private-value", "unclassified_server_event"),
        (logging.ERROR, "private-value" * 100_000, "unclassified_server_event"),
    ],
)
def test_pinned_templates_and_message_only_records_are_safely_projected(
    server_output: StringIO, level: int, message: str, reason: str
) -> None:
    event_logging.configure_runtime_server_logging()
    logging.getLogger("uvicorn.error").log(level, message)
    row = json.loads(server_output.getvalue())
    assert row["event"] == "server_log"
    assert row["reason"] == reason
    assert row["level"] == {20: "INFO", 30: "WARNING", 40: "ERROR"}[level]
    assert row["sourceCommit"] == "a" * 40
    assert row["releaseIdentity"] == "b" * 64
    assert "private" not in server_output.getvalue()
    assert "exceptionType" not in row


def test_server_record_never_renders_exception_args_extras_or_cached_text(
    server_output: StringIO,
) -> None:
    class Hostile:
        def __str__(self) -> str:
            raise AssertionError("private str rendered")

        def __repr__(self) -> str:
            raise AssertionError("private repr rendered")

    class PrivateError(Exception):
        def __str__(self) -> str:
            raise AssertionError("private exception rendered")

    error = PrivateError(Hostile())
    error.__cause__ = ValueError("private cause")
    error.add_note("private note")
    record = logging.LogRecord(
        "uvicorn.error",
        logging.ERROR,
        "/private/path",
        123,
        "Exception in ASGI application\n",
        (Hostile(),),
        (type(error), error, None),
        func="private_function",
        sinfo="private stack",
    )
    record.exc_text = "private cached traceback"
    record.extra_payload = Hostile()
    record.levelname = "private level name"
    event_logging.configure_runtime_server_logging()
    logging.getLogger("uvicorn.error").handle(record)
    row = json.loads(server_output.getvalue())
    assert row["reason"] == "asgi_exception"
    assert row["exceptionType"] == "PrivateError"
    assert row["level"] == "ERROR"
    assert "private" not in server_output.getvalue()
    server_output.truncate(0)
    server_output.seek(0)
    record.msg = Hostile()
    record.exc_info = None
    logging.getLogger("uvicorn.error").handle(record)
    assert json.loads(server_output.getvalue())["reason"] == "unclassified_server_event"


@pytest.mark.parametrize(
    "field,value", [("levelno", 99), ("levelno", "private"), ("exc_info", ("private",))]
)
def test_malformed_server_record_has_fixed_error_event(
    server_output: StringIO, field: str, value: object
) -> None:
    event_logging.configure_runtime_server_logging()
    record = logging.LogRecord("uvicorn.error", logging.ERROR, "", 0, "private", (), None)
    setattr(record, field, value)
    logging.getLogger("uvicorn").handlers[0].handle(record)
    row = json.loads(server_output.getvalue())
    assert (row["reason"], row["level"]) == ("diagnostic_contract", "ERROR")
    assert "private" not in server_output.getvalue()


def test_projector_failure_is_an_event_not_raw_fallback(
    server_output: StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(_record: logging.LogRecord) -> Any:
        raise RuntimeError("private projector diagnostic")

    monkeypatch.setattr(event_logging, "_project_server_record", fail)
    event_logging.configure_runtime_server_logging()
    logging.getLogger("uvicorn.error").error("private")
    assert json.loads(server_output.getvalue())["reason"] == "diagnostic_contract"
    assert "private" not in server_output.getvalue()


def test_installer_is_idempotent_and_retained_by_pinned_config(server_output: StringIO) -> None:
    import uvicorn

    root_handlers = list(logging.getLogger().handlers)
    event_logging.configure_runtime_server_logging()
    handler = logging.getLogger("uvicorn").handlers[0]
    event_logging.configure_runtime_server_logging()
    uvicorn.Config(cast(Any, object()), log_config=None, access_log=False)
    logging.getLogger("uvicorn.error").info("Application startup complete.")
    logging.getLogger("uvicorn.error").error("Application shutdown failed. Exiting.")
    assert logging.getLogger("uvicorn").handlers == [handler]
    assert logging.getLogger().handlers == root_handlers
    assert logging.getLogger("uvicorn.access").handlers == []
    assert logging.getLogger("uvicorn.access").propagate is False
    assert [json.loads(line)["level"] for line in server_output.getvalue().splitlines()] == [
        "INFO",
        "ERROR",
    ]


@pytest.mark.parametrize("name", ["uvicorn", "uvicorn.error", "uvicorn.access"])
def test_installer_refuses_foreign_handlers_without_replacing_them(
    server_output: StringIO, name: str
) -> None:
    logger = logging.getLogger(name)
    foreign = logging.NullHandler()
    logger.addHandler(foreign)
    with pytest.raises(RuntimeError, match="dedicated runtime profile"):
        event_logging.configure_runtime_server_logging()
    assert logger.handlers == [foreign]
    assert server_output.getvalue() == ""
