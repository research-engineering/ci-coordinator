from __future__ import annotations

import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from scripts.bounded_git import git_stdout_bytes, run_git
from scripts.proofkit_common import JsonObject, as_array, as_object, parse_json_object
from scripts.repository_source_admission import (
    admit_repository_regular_file,
    read_bounded_repository_bytes,
    read_bounded_repository_text,
)

REPO_ROOT: Final = Path(__file__).resolve().parent.parent
PROFILE_PATH: Final = Path(
    "docs/specs/ci-coordinator-core/architecture-traceability-profile.v1.json"
)
PREMISE_AUTHORITY_PATH: Final = Path("docs/architecture/00-system-axioms.md")
LAW_AUTHORITY_PATH: Final = Path("docs/architecture/01-meta-specification.md")
_PREMISE_ID = re.compile(r"(?:PA|GA|TF|CA)-[1-9][0-9]*")
_LAW_ID = re.compile(r"MS-[1-9][0-9]*")
_REQUIREMENT_ID = re.compile(r"REQ-CI-[A-Z]+-[0-9]{3}")
_TRACE_ID = re.compile(r"[a-z][a-z0-9.-]*")
_MAX_AUTHORITY_BYTES: Final = 512 * 1024
_MAX_PROFILE_BYTES: Final = 1024 * 1024
_MAX_REQUIREMENT_SOURCE_BYTES: Final = 4 * 1024 * 1024
_MAX_REQUIREMENT_SOURCE_COUNT: Final = 32
_MAX_REQUIREMENT_SOURCE_TOTAL_BYTES: Final = 16 * 1024 * 1024
_MAX_REQUIREMENT_ID_COUNT: Final = 4096
_MAX_REQUIREMENT_INVENTORY_BYTES: Final = 64 * 1024
_MAX_PREMISE_ROUTE_COUNT: Final = 128
_MAX_TRACE_GROUP_COUNT: Final = 1024
_MAX_REFERENCE_EDGE_COUNT: Final = 16384
_PREMISE_ROUTE_RELATIONS: Final = frozenset(
    {"external-constraint", "feasibility-evidence", "selection-context"}
)


@dataclass(frozen=True, slots=True)
class LawRow:
    premise_ids: tuple[str, ...]
    requirement_floor_ids: tuple[str, ...]


def validate_architecture_traceability(
    *,
    repo_root: Path = REPO_ROOT,
    profile: Mapping[str, object] | None = None,
) -> JsonObject:
    raw = (
        _bounded_repo_json_object(
            repo_root,
            PROFILE_PATH,
            maximum_bytes=_MAX_PROFILE_BYTES,
        )
        if profile is None
        else dict(profile)
    )
    _exact_keys(
        raw,
        (
            "lawAuthorityPath",
            "nonClaims",
            "premiseRoutes",
            "premiseAuthorityPath",
            "profileId",
            "requirementSourcePaths",
            "schemaVersion",
            "traceGroups",
        ),
        "architecture traceability profile",
    )
    if raw.get("schemaVersion") != 1:
        raise ValueError("architecture traceability schemaVersion must equal 1")
    if raw.get("profileId") != "ci-coordinator.architecture-traceability/v1":
        raise ValueError("architecture traceability profileId is unsupported")
    _expect_path(raw.get("premiseAuthorityPath"), PREMISE_AUTHORITY_PATH)
    _expect_path(raw.get("lawAuthorityPath"), LAW_AUTHORITY_PATH)

    premise_ids = _premise_ids(repo_root, PREMISE_AUTHORITY_PATH)
    law_rows = _law_rows(repo_root, LAW_AUTHORITY_PATH, premise_ids=premise_ids)
    requirement_paths = _requirement_source_paths(repo_root)
    declared_paths = _ordered_strings(raw.get("requirementSourcePaths"), "requirement source paths")
    if declared_paths != requirement_paths:
        raise ValueError("requirement source paths must equal the complete sorted inventory")
    requirement_ids = _requirement_ids(repo_root, requirement_paths)
    unknown_floor_ids = sorted(
        {
            requirement_id
            for row in law_rows.values()
            for requirement_id in row.requirement_floor_ids
        }
        - requirement_ids
    )
    if unknown_floor_ids:
        raise ValueError(
            "system-law requirement floor references unknown requirements: "
            + ", ".join(unknown_floor_ids)
        )

    (
        routed_premise_ids,
        premise_contract_paths,
        premise_route_count,
        premise_reference_edge_count,
    ) = _premise_routes(
        raw.get("premiseRoutes"),
        premise_ids=premise_ids,
        repo_root=repo_root,
    )
    required_premise_ids = {
        premise_id for row in law_rows.values() for premise_id in row.premise_ids
    }
    unused_premise_ids = sorted(premise_ids - required_premise_ids - routed_premise_ids)
    if unused_premise_ids:
        raise ValueError(
            "architecture premises lack a law or context route: " + ", ".join(unused_premise_ids)
        )

    group_values = as_array(raw.get("traceGroups"), "architecture trace groups")
    if len(group_values) > _MAX_TRACE_GROUP_COUNT:
        raise ValueError(f"architecture trace groups exceed {_MAX_TRACE_GROUP_COUNT} entries")
    groups = tuple(as_object(value, "architecture trace group") for value in group_values)
    if not groups:
        raise ValueError("architecture trace groups must be non-empty")
    trace_ids: list[str] = []
    traced_requirements: list[str] = []
    traced_laws_by_requirement: dict[str, frozenset[str]] = {}
    used_laws: set[str] = set()
    contract_paths = set(premise_contract_paths)
    reference_edge_count = premise_reference_edge_count + sum(
        len(row.premise_ids) for row in law_rows.values()
    )
    for group in groups:
        _exact_keys(
            group,
            ("contractPaths", "lawIds", "requirementIds", "traceId"),
            "architecture trace group",
        )
        trace_id = _required_string(group.get("traceId"), "architecture trace id")
        if _TRACE_ID.fullmatch(trace_id) is None:
            raise ValueError(f"invalid architecture trace id: {trace_id}")
        trace_ids.append(trace_id)
        group_requirements = _ordered_strings(
            group.get("requirementIds"), f"{trace_id} requirement ids"
        )
        if not group_requirements:
            raise ValueError(f"{trace_id} requirement ids must be non-empty")
        if any(_REQUIREMENT_ID.fullmatch(value) is None for value in group_requirements):
            raise ValueError(f"{trace_id} contains an invalid requirement id")
        traced_requirements.extend(group_requirements)
        group_laws = _ordered_strings(group.get("lawIds"), f"{trace_id} law ids")
        if not group_laws:
            raise ValueError(f"{trace_id} law ids must be non-empty")
        unknown_laws = sorted(set(group_laws) - law_rows.keys())
        if unknown_laws:
            raise ValueError(f"{trace_id} references unknown laws: {', '.join(unknown_laws)}")
        used_laws.update(group_laws)
        reference_edge_count += len(group_requirements) + len(group_laws)
        traced_laws_by_requirement.update(
            (requirement_id, frozenset(group_laws)) for requirement_id in group_requirements
        )
        paths = _ordered_strings(group.get("contractPaths"), f"{trace_id} contract paths")
        if not paths:
            raise ValueError(f"{trace_id} contract paths must be non-empty")
        for path in paths:
            _admit_contract_path(path, repo_root=repo_root)
            contract_paths.add(path)
        reference_edge_count += len(paths)
        if reference_edge_count > _MAX_REFERENCE_EDGE_COUNT:
            raise ValueError(
                f"architecture reference edges exceed {_MAX_REFERENCE_EDGE_COUNT} entries"
            )

    if trace_ids != sorted(trace_ids) or len(trace_ids) != len(set(trace_ids)):
        raise ValueError("architecture trace ids must be unique and sorted")
    duplicates = _duplicates(traced_requirements)
    if duplicates:
        raise ValueError("requirements have multiple architecture traces: " + ", ".join(duplicates))
    missing = sorted(requirement_ids - set(traced_requirements))
    extra = sorted(set(traced_requirements) - requirement_ids)
    if missing or extra:
        raise ValueError(
            "architecture requirement trace is not closed"
            f"; missing={','.join(missing) or '-'}; extra={','.join(extra) or '-'}"
        )
    unused_laws = sorted(law_rows.keys() - used_laws)
    if unused_laws:
        raise ValueError("system laws lack requirement traces: " + ", ".join(unused_laws))
    unbound_floor = sorted(
        f"{law_id}:{requirement_id}"
        for law_id, row in law_rows.items()
        for requirement_id in row.requirement_floor_ids
        if law_id not in traced_laws_by_requirement.get(requirement_id, frozenset())
    )
    if unbound_floor:
        raise ValueError(
            "system-law requirement floor lacks an exact trace: " + ", ".join(unbound_floor)
        )

    non_claims = _ordered_strings(raw.get("nonClaims"), "architecture trace non-claims")
    if not non_claims:
        raise ValueError("architecture trace nonClaims must be non-empty")
    return {
        "schemaVersion": 1,
        "reportId": "ci-coordinator.architecture-traceability",
        "reportKind": "ci-coordinator.architecture-traceability",
        "state": "passed",
        "summary": {
            "contractPathCount": len(contract_paths),
            "lawCount": len(law_rows),
            "premiseCount": len(premise_ids),
            "premiseRouteCount": premise_route_count,
            "requirementCount": len(requirement_ids),
            "traceGroupCount": len(groups),
        },
        "nonClaims": list(non_claims),
    }


def _premise_ids(repo_root: Path, path: Path) -> frozenset[str]:
    source = read_bounded_repository_text(
        repo_root,
        PurePosixPath(path.as_posix()),
        maximum_bytes=_MAX_AUTHORITY_BYTES,
    )
    ids = [
        match.group(1)
        for line in source.splitlines()
        if (match := re.match(r"^\| ((?:PA|GA|TF|CA)-[1-9][0-9]*) \|", line))
    ]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("premise authority must define unique premise ids")
    return frozenset(ids)


def _law_rows(
    repo_root: Path,
    path: Path,
    *,
    premise_ids: frozenset[str],
) -> dict[str, LawRow]:
    rows: dict[str, LawRow] = {}
    source = read_bounded_repository_text(
        repo_root,
        PurePosixPath(path.as_posix()),
        maximum_bytes=_MAX_AUTHORITY_BYTES,
    )
    for line in source.splitlines():
        cells = tuple(cell.strip() for cell in line.strip().strip("|").split("|"))
        if len(cells) != 5 or _LAW_ID.fullmatch(cells[0]) is None:
            continue
        law_id = cells[0]
        if law_id in rows:
            raise ValueError(f"duplicate system law: {law_id}")
        dependencies = tuple(sorted(set(_PREMISE_ID.findall(cells[2]))))
        if not dependencies:
            raise ValueError(f"system law {law_id} must cite premises")
        unknown = sorted(set(dependencies) - premise_ids)
        if unknown:
            raise ValueError(f"system law {law_id} cites unknown premises: {', '.join(unknown)}")
        requirement_floor = tuple(sorted(set(_REQUIREMENT_ID.findall(cells[3]))))
        if not requirement_floor:
            raise ValueError(f"system law {law_id} must define a direct requirement floor")
        rows[law_id] = LawRow(
            premise_ids=dependencies,
            requirement_floor_ids=requirement_floor,
        )
    if not rows:
        raise ValueError("law authority must define system laws")
    return rows


def _requirement_ids(repo_root: Path, paths: Sequence[str]) -> frozenset[str]:
    if len(paths) > _MAX_REQUIREMENT_SOURCE_COUNT:
        raise ValueError(f"requirement sources exceed {_MAX_REQUIREMENT_SOURCE_COUNT} entries")
    ids: list[str] = []
    total_bytes = 0
    for path in paths:
        relative_path = PurePosixPath(path)
        payload = read_bounded_repository_bytes(
            repo_root,
            relative_path,
            maximum_bytes=_MAX_REQUIREMENT_SOURCE_BYTES,
        )
        total_bytes += len(payload)
        if total_bytes > _MAX_REQUIREMENT_SOURCE_TOTAL_BYTES:
            raise ValueError(
                f"requirement sources exceed {_MAX_REQUIREMENT_SOURCE_TOTAL_BYTES} aggregate bytes"
            )
        try:
            source_text = payload.decode("utf-8", errors="strict")
        except UnicodeError as error:
            raise ValueError(f"requirement source is not strict UTF-8: {path}") from error
        source = parse_json_object(source_text, path)
        for value in as_array(source.get("requirements"), f"requirements in {path}"):
            row = as_object(value, f"requirement in {path}")
            requirement_id = _required_string(row.get("requirementId"), "requirement id")
            ids.append(requirement_id)
            if len(ids) > _MAX_REQUIREMENT_ID_COUNT:
                raise ValueError(f"requirement ids exceed {_MAX_REQUIREMENT_ID_COUNT} entries")
    duplicates = _duplicates(ids)
    if duplicates:
        raise ValueError("requirement ids must be globally unique: " + ", ".join(duplicates))
    return frozenset(ids)


def _admit_contract_path(value: str, *, repo_root: Path) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.suffix != ".md":
        raise ValueError(f"invalid architecture contract path: {value}")
    if not value.startswith("docs/"):
        raise ValueError(f"architecture contract path is not a regular docs file: {value}")
    try:
        admit_repository_regular_file(repo_root, path)
    except ValueError as error:
        raise ValueError(
            f"architecture contract path is not a regular docs file: {value}"
        ) from error


def _premise_routes(
    value: object,
    *,
    premise_ids: frozenset[str],
    repo_root: Path,
) -> tuple[set[str], set[str], int, int]:
    route_values = as_array(value, "architecture premise routes")
    if len(route_values) > _MAX_PREMISE_ROUTE_COUNT:
        raise ValueError(f"architecture premise routes exceed {_MAX_PREMISE_ROUTE_COUNT} entries")
    route_ids: list[str] = []
    routed_premise_ids: set[str] = set()
    contract_paths: set[str] = set()
    reference_edge_count = 0
    for value_item in route_values:
        route = as_object(value_item, "architecture premise route")
        _exact_keys(
            route,
            ("contractPaths", "premiseIds", "relation", "routeId"),
            "architecture premise route",
        )
        route_id = _required_string(route.get("routeId"), "architecture premise route id")
        if _TRACE_ID.fullmatch(route_id) is None:
            raise ValueError(f"invalid architecture premise route id: {route_id}")
        route_ids.append(route_id)
        relation = _required_string(route.get("relation"), f"{route_id} relation")
        if relation not in _PREMISE_ROUTE_RELATIONS:
            raise ValueError(f"{route_id} has an unsupported premise relation: {relation}")
        route_premises = _ordered_strings(route.get("premiseIds"), f"{route_id} premise ids")
        if not route_premises:
            raise ValueError(f"{route_id} premise ids must be non-empty")
        unknown = sorted(set(route_premises) - premise_ids)
        if unknown:
            raise ValueError(f"{route_id} references unknown premises: {', '.join(unknown)}")
        routed_premise_ids.update(route_premises)
        paths = _ordered_strings(route.get("contractPaths"), f"{route_id} contract paths")
        if not paths:
            raise ValueError(f"{route_id} contract paths must be non-empty")
        for path in paths:
            _admit_contract_path(path, repo_root=repo_root)
            contract_paths.add(path)
        reference_edge_count += len(route_premises) + len(paths)
    if route_ids != sorted(route_ids) or len(route_ids) != len(set(route_ids)):
        raise ValueError("architecture premise route ids must be unique and sorted")
    return (
        routed_premise_ids,
        contract_paths,
        len(route_values),
        reference_edge_count,
    )


def _requirement_source_paths(repo_root: Path) -> tuple[str, ...]:
    result = run_git(
        repo_root,
        (
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            ":(glob)docs/specs/*/requirements.v1.json",
        ),
        decode_errors="surrogateescape",
        max_buffer=_MAX_REQUIREMENT_INVENTORY_BYTES,
    )
    if result.stderr:
        raise ValueError("requirement source inventory wrote to stderr")
    payload = git_stdout_bytes(result)
    if payload and not payload.endswith(b"\0"):
        raise ValueError("requirement source inventory is not NUL-terminated")
    try:
        paths = [raw.decode("utf-8", errors="strict") for raw in payload.split(b"\0") if raw]
    except UnicodeError as error:
        raise ValueError("requirement source inventory is not strict UTF-8") from error
    if len(paths) > _MAX_REQUIREMENT_SOURCE_COUNT:
        raise ValueError(f"requirement sources exceed {_MAX_REQUIREMENT_SOURCE_COUNT} entries")
    return tuple(sorted(paths))


def _bounded_repo_json_object(
    repo_root: Path,
    path: Path,
    *,
    maximum_bytes: int,
) -> JsonObject:
    return parse_json_object(
        read_bounded_repository_text(
            repo_root,
            PurePosixPath(path.as_posix()),
            maximum_bytes=maximum_bytes,
        ),
        path.as_posix(),
    )


def _expect_path(value: object, expected: Path) -> None:
    if value != expected.as_posix():
        raise ValueError(f"architecture authority path must equal {expected.as_posix()}")


def _ordered_strings(value: object, label: str) -> tuple[str, ...]:
    values = tuple(_required_string(item, label) for item in as_array(value, label))
    if values != tuple(sorted(values)) or len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique and sorted")
    return values


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _exact_keys(value: Mapping[str, object], keys: Sequence[str], label: str) -> None:
    if set(value) != set(keys):
        raise ValueError(f"{label} must contain exactly: {', '.join(keys)}")


def _duplicates(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def main() -> int:
    try:
        report = validate_architecture_traceability()
    except (OSError, UnicodeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    summary = as_object(report["summary"], "architecture trace summary")
    print(
        "architecture traceability admitted "
        f"{summary['requirementCount']} requirements through "
        f"{summary['traceGroupCount']} groups"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
