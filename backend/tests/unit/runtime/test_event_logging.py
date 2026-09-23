from __future__ import annotations

import json
import logging
from io import StringIO

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
