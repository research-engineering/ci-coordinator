"""Pure admission rules for finite mutation-suite manifests."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Final, NotRequired, TypedDict, cast

REQUIRED_MUTANT_KEYS: Final = frozenset(
    {
        "command",
        "file",
        "id",
        "operator",
        "original",
        "replacement",
        "requirementIds",
        "witnessId",
    }
)
OPTIONAL_MUTANT_KEYS: Final = frozenset({"environment"})
ALLOWED_MUTANT_ENVIRONMENT_KEYS: Final = frozenset(
    {
        "PROOFKIT_BASE_REF",
        "PROOFKIT_HEAD_REF",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONPATH",
    }
)


class MutationCommand(TypedDict):
    command: list[str]


class Mutant(MutationCommand):
    id: str
    file: str
    operator: str
    original: str
    replacement: str
    witnessId: str
    requirementIds: list[str]
    environment: NotRequired[dict[str, str]]


class MutationManifest(TypedDict):
    expectedKilled: int
    expectedMutantIds: list[str]
    mutants: list[Mutant]
    outerTimeoutMs: int
    timeoutMs: int


class MutationTargetFailure(TypedDict):
    file: str
    id: str
    occurrenceCount: int


def decode_mutation_manifest(manifest_bytes: bytes) -> MutationManifest:
    value = cast(object, json.loads(manifest_bytes.decode("utf-8")))
    if not isinstance(value, dict):
        raise TypeError("mutation manifest must be a JSON object")
    manifest = cast(MutationManifest, value)
    assert_manifest_execution_contract(manifest)
    return manifest


def assert_manifest_execution_contract(manifest: MutationManifest) -> None:
    expected_killed = cast(object, manifest.get("expectedKilled"))
    expected_mutant_ids = cast(object, manifest.get("expectedMutantIds"))
    mutants_value = cast(object, manifest.get("mutants"))
    outer_timeout_ms = cast(object, manifest.get("outerTimeoutMs"))
    timeout_ms = cast(object, manifest.get("timeoutMs"))
    if (
        type(expected_killed) is not int
        or not isinstance(expected_mutant_ids, list)
        or not expected_mutant_ids
        or not all(_is_nonempty_text(value) for value in expected_mutant_ids)
        or not isinstance(mutants_value, list)
        or not mutants_value
        or type(outer_timeout_ms) is not int
        or outer_timeout_ms <= 0
        or type(timeout_ms) is not int
        or timeout_ms <= 0
    ):
        raise RuntimeError("mutation manifest execution contract is invalid")

    mutants = cast(list[Mutant], mutants_value)
    for mutant in mutants:
        if not isinstance(mutant, dict):
            raise TypeError("mutation manifest execution contract is invalid")
        keys = set(mutant)
        command = cast(object, mutant.get("command"))
        requirement_ids = cast(object, mutant.get("requirementIds"))
        environment = cast(object, mutant.get("environment", {}))
        if (
            not REQUIRED_MUTANT_KEYS <= keys <= REQUIRED_MUTANT_KEYS | OPTIONAL_MUTANT_KEYS
            or not all(
                _is_nonempty_text(mutant.get(field))
                for field in ("file", "id", "operator", "original", "witnessId")
            )
            or not isinstance(mutant.get("replacement"), str)
            or "\0" in mutant["replacement"]
            or mutant["replacement"] == mutant["original"]
            or not isinstance(command, list)
            or not command
            or not all(_is_nonempty_text(argument) for argument in command)
            or not isinstance(requirement_ids, list)
            or not requirement_ids
            or not all(_is_nonempty_text(requirement_id) for requirement_id in requirement_ids)
            or len(set(cast(list[str], requirement_ids))) != len(requirement_ids)
            or not isinstance(environment, dict)
            or not set(environment) <= ALLOWED_MUTANT_ENVIRONMENT_KEYS
            or not all(
                _is_environment_key(key) and isinstance(value, str) and "\0" not in value
                for key, value in environment.items()
            )
            or environment.get("PYTHONDONTWRITEBYTECODE", "1") != "1"
        ):
            raise RuntimeError("mutation manifest execution contract is invalid")

    mutant_ids = [mutant["id"] for mutant in mutants]
    mutation_tuples = [
        _compact_json(
            [
                mutant["file"],
                mutant["operator"],
                mutant["original"],
                mutant["replacement"],
                mutant["witnessId"],
            ]
        )
        for mutant in mutants
    ]
    execution_budget_ms = len(mutants) * 2 * timeout_ms + 60_000
    if expected_killed != len(mutants) or execution_budget_ms > outer_timeout_ms:
        raise RuntimeError("mutation manifest execution budget exceeds its outer timeout")
    if (
        len(set(mutant_ids)) != len(mutant_ids)
        or len(set(mutation_tuples)) != len(mutation_tuples)
        or len(expected_mutant_ids) != len(mutant_ids)
        or any(
            expected_id != mutant_id
            for expected_id, mutant_id in zip(
                cast(list[str], expected_mutant_ids),
                mutant_ids,
                strict=True,
            )
        )
    ):
        raise RuntimeError("mutation manifest ids and mutation tuples must be unique and canonical")


def assert_manifest_fits_execution_envelope(
    manifest: MutationManifest,
    *,
    timeout_ms: int,
) -> None:
    if type(timeout_ms) is not int or timeout_ms <= 0:
        raise RuntimeError("mutation suite execution envelope is invalid")
    if manifest["outerTimeoutMs"] > timeout_ms:
        raise RuntimeError("mutation suite outer timeout exceeds its command envelope")


def collect_manifest_applicability_failures(
    manifest: MutationManifest,
    *,
    source_root: Path,
) -> tuple[MutationTargetFailure, ...]:
    source_by_path: dict[str, str] = {}
    failures: list[MutationTargetFailure] = []
    for mutant in manifest["mutants"]:
        relative_path = mutant["file"]
        source = source_by_path.get(relative_path)
        if source is None:
            source = _read_regular_source(source_root, relative_path)
            source_by_path[relative_path] = source
        occurrence_count = _count_occurrences(source, mutant["original"])
        if occurrence_count != 1:
            failures.append(
                {
                    "file": relative_path,
                    "id": mutant["id"],
                    "occurrenceCount": occurrence_count,
                }
            )
    return tuple(failures)


def assert_manifest_applicable(manifest: MutationManifest, *, source_root: Path) -> None:
    failures = collect_manifest_applicability_failures(manifest, source_root=source_root)
    if failures:
        encoded = _compact_json(list(failures))
        raise RuntimeError(f"mutation manifest applicability preflight failed: {encoded}")


def _read_regular_source(source_root: Path, relative_path: str) -> str:
    root = source_root.resolve(strict=True)
    relative = PurePosixPath(relative_path)
    if (
        not relative_path
        or "\\" in relative_path
        or relative.is_absolute()
        or relative.as_posix() != relative_path
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise RuntimeError(f"mutation target path is not canonical: {relative_path!r}")

    candidate = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise RuntimeError(f"mutation target path contains a symlink: {relative_path}")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise RuntimeError(f"mutation target is unavailable: {relative_path}") from error
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise RuntimeError(f"mutation target is not a regular repository file: {relative_path}")
    return resolved.read_text(encoding="utf-8")


def _count_occurrences(source: str, target: str) -> int:
    if not target:
        return 0
    count = 0
    offset = 0
    while True:
        index = source.find(target, offset)
        if index < 0:
            return count
        count += 1
        offset = index + 1


def _is_nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value) and "\0" not in value


def _is_environment_key(value: object) -> bool:
    return isinstance(value, str) and bool(value) and "\0" not in value and "=" not in value


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
