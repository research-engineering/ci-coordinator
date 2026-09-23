from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, cast

from jsonschema import Draft7Validator, Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

MAX_PREDICATE_BYTES: Final = 16 * 1024 * 1024
ADMITTED_SPDX_SCHEMA_SHA256: Final = (
    "23b238cde51ad35021a61eb79639814c91a436b1d62061a1122aba6107b1c927"
)
_RFC3339_TIMESTAMP: Final = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt]"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?"
    r"(?:[Zz]|[+-][0-9]{2}:[0-9]{2})"
)
_RFC3339_FORMAT_CHECKER: Final = FormatChecker(formats=())


class PredicateAdmissionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PredicateAdmission:
    provenance_sha256: str
    sbom_sha256: str


def admit_release_predicates(
    *,
    provenance_path: Path,
    sbom_path: Path,
    schema_directory: Path,
) -> PredicateAdmission:
    provenance_raw, provenance = _load_strict_json(
        provenance_path,
        label="BuildKit provenance",
        maximum=MAX_PREDICATE_BYTES,
    )
    sbom_raw, sbom = _load_strict_json(
        sbom_path,
        label="SPDX SBOM",
        maximum=MAX_PREDICATE_BYTES,
    )
    spdx_schema_raw, spdx_schema = _load_strict_json(
        schema_directory / "spdx-2.3.schema.json",
        label="SPDX 2.3 schema",
        maximum=MAX_PREDICATE_BYTES,
    )
    _, provenance_schema = _load_strict_json(
        schema_directory / "buildkit-provenance-predicate.schema.v1.json",
        label="BuildKit provenance admission schema",
        maximum=MAX_PREDICATE_BYTES,
    )
    _, spdx_profile = _load_strict_json(
        schema_directory / "spdx-buildkit-profile.schema.v1.json",
        label="BuildKit SPDX admission profile",
        maximum=MAX_PREDICATE_BYTES,
    )
    if hashlib.sha256(spdx_schema_raw).hexdigest() != ADMITTED_SPDX_SCHEMA_SHA256:
        raise PredicateAdmissionError("SPDX 2.3 schema does not match its admitted upstream bytes")

    _validate(
        provenance,
        provenance_schema,
        Draft202012Validator,
        label="BuildKit provenance",
    )
    _validate(sbom, spdx_schema, Draft7Validator, label="SPDX 2.3 SBOM")
    _validate(sbom, spdx_profile, Draft202012Validator, label="BuildKit SPDX profile")
    _admit_timestamp_order(provenance)
    _admit_unique_package_ids(sbom)

    return PredicateAdmission(
        provenance_sha256=hashlib.sha256(provenance_raw).hexdigest(),
        sbom_sha256=hashlib.sha256(sbom_raw).hexdigest(),
    )


def _validate(
    instance: object,
    schema: object,
    validator_type: type[Draft7Validator | Draft202012Validator],
    *,
    label: str,
) -> None:
    admitted_schema = _object(schema, f"{label} schema")
    try:
        validator_type.check_schema(admitted_schema)
        validator = validator_type(
            admitted_schema,
            format_checker=_RFC3339_FORMAT_CHECKER,
        )
        failure = next(iter(validator.iter_errors(instance)), None)
    except SchemaError as error:
        raise PredicateAdmissionError(f"{label} schema is invalid") from error
    if failure is not None:
        raise PredicateAdmissionError(_validation_message(label, failure))


def _validation_message(label: str, failure: ValidationError) -> str:
    location = "$"
    for component in failure.absolute_path:
        location += f"[{component}]" if isinstance(component, int) else f".{component}"
    return f"{label} is outside its admission profile at {location}: {failure.message}"


def _admit_timestamp_order(provenance: object) -> None:
    root = _object(provenance, "BuildKit provenance")
    run_details = _object(root.get("runDetails"), "BuildKit provenance runDetails")
    metadata = _object(run_details.get("metadata"), "BuildKit provenance metadata")
    started = _timestamp(metadata.get("startedOn"), "BuildKit provenance startedOn")
    finished = _timestamp(metadata.get("finishedOn"), "BuildKit provenance finishedOn")
    if finished < started:
        raise PredicateAdmissionError("BuildKit provenance finishes before it starts")


def _admit_unique_package_ids(sbom: object) -> None:
    root = _object(sbom, "SPDX SBOM")
    packages = root.get("packages")
    if type(packages) is not list:
        raise PredicateAdmissionError("SPDX SBOM packages must be an array")
    identifiers = [
        _object(package, "SPDX package").get("SPDXID") for package in cast(list[object], packages)
    ]
    if len(identifiers) != len(set(identifiers)):
        raise PredicateAdmissionError("SPDX SBOM package identifiers must be unique")


def _timestamp(value: object, label: str) -> datetime:
    if type(value) is not str:
        raise PredicateAdmissionError(f"{label} must be a timestamp")
    if _RFC3339_TIMESTAMP.fullmatch(value) is None:
        raise PredicateAdmissionError(f"{label} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.upper())
    except ValueError as error:
        raise PredicateAdmissionError(f"{label} must be an RFC 3339 timestamp") from error
    if parsed.tzinfo is None:
        raise PredicateAdmissionError(f"{label} must include an RFC 3339 offset")
    return parsed


@_RFC3339_FORMAT_CHECKER.checks("date-time")
def _is_rfc3339_timestamp(value: object) -> bool:
    try:
        _timestamp(value, "date-time value")
    except PredicateAdmissionError:
        return False
    return True


def _load_strict_json(path: Path, *, label: str, maximum: int) -> tuple[bytes, object]:
    content = _read_stable_regular_file(path, label=label, maximum=maximum)
    try:
        value = json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, PredicateAdmissionError) as error:
        raise PredicateAdmissionError(f"{label} is not strict JSON") from error
    return content, value


def _read_stable_regular_file(path: Path, *, label: str, maximum: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PredicateAdmissionError(f"{label} is unavailable") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
            raise PredicateAdmissionError(f"{label} size is outside its bound")
        content = _read_bounded(descriptor, maximum=maximum)
        after = os.fstat(descriptor)
    except OSError as error:
        raise PredicateAdmissionError(f"{label} could not be read") from error
    finally:
        os.close(descriptor)
    if _stable_identity(before) != _stable_identity(after) or len(content) != before.st_size:
        raise PredicateAdmissionError(f"{label} changed while being read")
    return content


def _read_bounded(descriptor: int, *, maximum: int) -> bytes:
    chunks: list[bytes] = []
    remaining = maximum + 1
    while remaining > 0:
        chunk = os.read(descriptor, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    content = b"".join(chunks)
    if len(content) > maximum:
        raise PredicateAdmissionError("predicate evidence size is outside its bound")
    return content


def _stable_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _object(value: object, label: str) -> dict[str, object]:
    if type(value) is not dict:
        raise PredicateAdmissionError(f"{label} must be an object")
    return cast(dict[str, object], value)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PredicateAdmissionError(f"duplicate object key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise PredicateAdmissionError(f"non-finite numeric constant: {value}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--schema-directory", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        admission = admit_release_predicates(
            provenance_path=arguments.provenance,
            sbom_path=arguments.sbom,
            schema_directory=arguments.schema_directory,
        )
    except PredicateAdmissionError as error:
        parser.exit(1, f"release predicate admission failed: {error}\n")
    print(f"provenance_sha256={admission.provenance_sha256}")
    print(f"sbom_sha256={admission.sbom_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
