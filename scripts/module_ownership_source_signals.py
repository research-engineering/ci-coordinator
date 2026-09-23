from __future__ import annotations

from pathlib import Path

from scripts.module_ownership_ecmascript_signals import (
    ECMASCRIPT_SUFFIXES,
    ecmascript_signals,
)
from scripts.module_ownership_python_signals import python_signals

_PYTHON_SUFFIX = ".py"
_SOURCE_SUFFIXES = ECMASCRIPT_SUFFIXES | {_PYTHON_SUFFIX}


def metric_values(path: str, payload: bytes) -> dict[str, int | None]:
    values: dict[str, int | None] = {
        "physical-lines": _physical_lines(payload),
    }
    suffix = Path(path).suffix
    if suffix not in _SOURCE_SUFFIXES:
        return values
    try:
        source = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        values["recognized-public-declarations"] = None
        values["first-party-import-contexts"] = None
        return values
    signals = (
        python_signals(path, source)
        if suffix == _PYTHON_SUFFIX
        else ecmascript_signals(path, source)
    )
    values["recognized-public-declarations"] = signals.recognized_public_declarations
    values["first-party-import-contexts"] = signals.first_party_import_contexts
    return values


def _physical_lines(payload: bytes) -> int:
    if not payload:
        return 0
    terminators = payload.count(b"\r") + payload.count(b"\n") - payload.count(b"\r\n")
    return terminators + int(payload[-1] not in {0x0A, 0x0D})
