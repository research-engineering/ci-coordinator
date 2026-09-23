from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from scripts.proofkit_common import (
    JsonObject,
    as_array,
    as_object,
    js_json_dumps,
    safe_repo_path,
)
from scripts.proofkit_common import exact_keys as _exact_keys
from scripts.proofkit_common import nonempty_string_array as _string_array
from scripts.proofkit_common import trimmed_text as _strict_text

ROUTE_SOURCE_CONTRACT_ID = "ci-coordinator.requirement-binding-route-source.v2"
ROUTE_SOURCE_BINDING_COLUMNS = (
    "requirementIdSuffix",
    "scenarioIdSuffix",
    "witnessIdSuffix",
    "witnessKind",
    "witnessPath",
    "commandIds",
    "environmentClasses",
)
SCENARIO_ID_PREFIX = "ci-coordinator.binding.scenario."
WITNESS_ID_PREFIX = "ci-coordinator.binding.witness."

_REQUIREMENT_PREFIX = re.compile(r"REQ-CI-[A-Z0-9]+(?:-[A-Z0-9]+)*-")
_LEGACY_ROOT_NON_CLAIMS = (
    "Requirement proof bindings do not execute native witnesses.",
    "Requirement proof bindings do not prove command freshness or receipt authenticity.",
    "Requirement proof bindings do not approve dynamic omission, merge, rollout, or deployment.",
)
_ROUTE_SOURCE_KEYS = {
    "schemaVersion",
    "contractId",
    "sourceId",
    "requirementIdPrefix",
    "scenarioIdPrefix",
    "witnessIdPrefix",
    "bindingColumns",
    "bindings",
    "nonClaims",
}


@dataclass(frozen=True, slots=True)
class RouteParity:
    binding_count: int
    canonicalized_requirement_non_claim_count: int
    command_count: int
    extra_binding_count: int
    requirement_count: int


def legacy_binding_projection(
    route_sources: Sequence[tuple[str, str, Mapping[str, object]]],
    requirement_sources: Sequence[tuple[str, Mapping[str, object]]],
    witness_plan: Mapping[str, object],
) -> JsonObject:
    requirements, claim_level_by_id = _legacy_requirements(requirement_sources)
    commands, environment_by_command_id = _legacy_commands(witness_plan)
    bindings = _legacy_bindings(
        route_sources,
        claim_level_by_id=claim_level_by_id,
        environment_by_command_id=environment_by_command_id,
    )
    bound_requirement_ids = {str(binding["requirementId"]) for binding in bindings}
    missing_blocking = sorted(
        requirement_id
        for requirement_id, claim_level in claim_level_by_id.items()
        if claim_level == "blocking" and requirement_id not in bound_requirement_ids
    )
    if missing_blocking:
        raise ValueError(
            "compact proof routes leave blocking requirements unbound: "
            + ", ".join(missing_blocking)
        )
    return {
        "schemaVersion": 1,
        "bindingId": "ci-coordinator.requirement-bindings",
        "requirements": requirements,
        "bindings": bindings,
        "witnessCommands": commands,
        "selection": {"changedPaths": [], "ownerIds": [], "requirementIds": []},
        "nonClaims": list(_LEGACY_ROOT_NON_CLAIMS),
    }


def assert_legacy_relation_preserved(
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
    *,
    allowed_added_paths: Sequence[str],
) -> RouteParity:
    baseline_requirements = _requirement_relation(baseline)
    candidate_requirements = _requirement_relation(candidate)
    if baseline_requirements != candidate_requirements:
        raise ValueError("compact route migration changed the requirement relation")
    baseline_non_claims = _requirement_non_claim_relation(baseline)
    candidate_non_claims = _requirement_non_claim_relation(candidate)
    canonicalized_non_claim_count = sum(
        baseline_non_claims[requirement_id] != candidate_non_claims[requirement_id]
        for requirement_id in baseline_non_claims
    )

    baseline_commands = _command_relation(baseline)
    candidate_commands = _command_relation(candidate)
    if baseline_commands != candidate_commands:
        raise ValueError("compact route migration changed the witness command relation")

    baseline_bindings = _binding_relation(baseline)
    candidate_bindings = _binding_relation(candidate)
    missing = sorted(baseline_bindings - candidate_bindings)
    if missing:
        raise ValueError(
            "compact route migration removed legacy routes: " + js_json_dumps(missing[:10])
        )
    allowed = {safe_repo_path(path) for path in allowed_added_paths}
    extras = candidate_bindings - baseline_bindings
    unexpected = sorted(row for row in extras if row[4] not in allowed)
    if unexpected:
        raise ValueError(
            "compact route migration introduced undeclared routes: "
            + js_json_dumps(unexpected[:10])
        )
    return RouteParity(
        binding_count=len(candidate_bindings),
        canonicalized_requirement_non_claim_count=canonicalized_non_claim_count,
        command_count=len(candidate_commands),
        extra_binding_count=len(extras),
        requirement_count=len(candidate_requirements),
    )


def legacy_relation_digest(document: Mapping[str, object]) -> str:
    relation = {
        "bindings": [list(row) for row in sorted(_binding_relation(document))],
        "commands": [list(row) for row in sorted(_command_relation(document))],
        "requirements": [list(row) for row in sorted(_requirement_relation(document))],
    }
    return hashlib.sha256(js_json_dumps(relation).encode()).hexdigest()


def _legacy_requirements(
    sources: Sequence[tuple[str, Mapping[str, object]]],
) -> tuple[list[JsonObject], dict[str, str]]:
    requirements: list[JsonObject] = []
    claim_level_by_id: dict[str, str] = {}
    for path, source in sources:
        for raw in as_array(source.get("requirements"), f"{path} requirements"):
            requirement = as_object(raw, f"{path} requirement")
            requirement_id = _text(requirement, "requirementId")
            if requirement_id in claim_level_by_id:
                raise ValueError(f"duplicate requirement id: {requirement_id}")
            claim_level = _text(requirement, "claimLevel")
            if claim_level not in {"blocking", "deferred"}:
                raise ValueError(f"unsupported requirement claim level: {claim_level}")
            claim_level_by_id[requirement_id] = claim_level
            requirements.append(
                {
                    "requirementId": requirement_id,
                    "ownerId": _text(requirement, "ownerId"),
                    "specPath": safe_repo_path(path),
                    "claimLevel": claim_level,
                    "proofState": (
                        "witness_backed" if claim_level == "blocking" else "explicitly_deferred"
                    ),
                    "nonClaims": _string_array(
                        requirement.get("nonClaims"), f"{requirement_id} nonClaims"
                    ),
                }
            )
    requirements.sort(key=lambda row: str(row["requirementId"]))
    return requirements, claim_level_by_id


def _legacy_commands(
    witness_plan: Mapping[str, object],
) -> tuple[list[JsonObject], dict[str, str]]:
    commands: list[JsonObject] = []
    environment_by_command_id: dict[str, str] = {}
    command_texts: set[str] = set()
    for raw in as_array(witness_plan.get("commands"), "witness plan commands"):
        command = as_object(raw, "witness plan command")
        command_id = _text(command, "id")
        if command_id in environment_by_command_id:
            raise ValueError(f"duplicate witness command id: {command_id}")
        command_text = " ".join(_string_array(command.get("argv"), f"{command_id} argv"))
        if command_text in command_texts:
            raise ValueError(f"duplicate witness command text: {command_text}")
        environment = as_object(command.get("environment"), f"{command_id} environment")
        classes = _string_array(environment.get("classes"), f"{command_id} environment classes")
        if len(classes) != 1:
            raise ValueError(f"witness command must declare exactly one environment: {command_id}")
        command_texts.add(command_text)
        environment_by_command_id[command_id] = classes[0]
        commands.append(
            {
                "commandId": command_id,
                "command": command_text,
                "environmentClass": classes[0],
            }
        )
    commands.sort(key=lambda row: str(row["commandId"]))
    return commands, environment_by_command_id


def _legacy_bindings(
    route_sources: Sequence[tuple[str, str, Mapping[str, object]]],
    *,
    claim_level_by_id: Mapping[str, str],
    environment_by_command_id: Mapping[str, str],
) -> list[JsonObject]:
    bindings: list[JsonObject] = []
    identities: set[tuple[str, str, str]] = set()
    requirement_prefixes: set[str] = set()
    scenario_ids: set[str] = set()
    witness_ids: set[str] = set()
    for expected_source_id, path, source in route_sources:
        context = f"proof route source {path}"
        _exact_keys(source, _ROUTE_SOURCE_KEYS, context)
        _literal(source, "schemaVersion", 2, context)
        _literal(source, "contractId", ROUTE_SOURCE_CONTRACT_ID, context)
        _literal(source, "sourceId", expected_source_id, context)
        _literal(source, "scenarioIdPrefix", SCENARIO_ID_PREFIX, context)
        _literal(source, "witnessIdPrefix", WITNESS_ID_PREFIX, context)
        _string_array(source.get("nonClaims"), f"{context} nonClaims")
        columns = _string_array(source.get("bindingColumns"), f"{context} bindingColumns")
        if columns != list(ROUTE_SOURCE_BINDING_COLUMNS):
            raise ValueError(f"{context} bindingColumns are not canonical")
        requirement_prefix = _text(source, "requirementIdPrefix")
        if _REQUIREMENT_PREFIX.fullmatch(requirement_prefix) is None:
            raise ValueError(f"{context} requirementIdPrefix is invalid")
        if requirement_prefix in requirement_prefixes:
            raise ValueError(f"duplicate proof route requirement prefix: {requirement_prefix}")
        requirement_prefixes.add(requirement_prefix)
        rows = as_array(source.get("bindings"), f"{context} bindings")
        if not rows:
            raise ValueError(f"{context} must declare at least one binding")
        previous_key: tuple[str, str, str, str] | None = None
        for raw in rows:
            row = as_array(raw, f"{context} binding")
            if len(row) != len(ROUTE_SOURCE_BINDING_COLUMNS):
                raise ValueError(f"{context} binding row does not match bindingColumns")
            requirement_id = requirement_prefix + _suffix(row[0], "requirementId")
            scenario_id = SCENARIO_ID_PREFIX + _suffix(row[1], "scenarioId")
            witness_id = WITNESS_ID_PREFIX + _suffix(row[2], "witnessId")
            witness_kind = _strict_text(row[3], f"{context} witnessKind")
            witness_path = safe_repo_path(row[4])
            key = (requirement_id, witness_path, scenario_id, witness_id)
            if previous_key is not None and key <= previous_key:
                raise ValueError(f"{context} binding rows must be unique and sorted")
            previous_key = key
            command_ids = _sorted_string_set(row[5], f"{context} commandIds")
            environments = _sorted_string_set(row[6], f"{context} environmentClasses")
            if requirement_id not in claim_level_by_id:
                raise ValueError(f"{context} references unknown requirement: {requirement_id}")
            for command_id in command_ids:
                environment = environment_by_command_id.get(command_id)
                if environment is None:
                    raise ValueError(f"{context} references unknown command: {command_id}")
                if environment not in environments:
                    raise ValueError(f"{context} omits command environment: {command_id}")
            identity = (requirement_id, scenario_id, witness_id)
            if identity in identities:
                raise ValueError(f"duplicate compact route identity: {js_json_dumps(identity)}")
            if scenario_id in scenario_ids or witness_id in witness_ids:
                raise ValueError("compact scenario and witness identities must be globally unique")
            identities.add(identity)
            scenario_ids.add(scenario_id)
            witness_ids.add(witness_id)
            bindings.append(
                {
                    "requirementId": requirement_id,
                    "scenarioId": scenario_id,
                    "witnessId": witness_id,
                    "witnessKind": witness_kind,
                    "witnessPath": witness_path,
                    "commandIds": command_ids,
                    "environmentClasses": environments,
                }
            )
    bindings.sort(
        key=lambda row: (
            str(row["requirementId"]),
            str(row["witnessPath"]),
            str(row["scenarioId"]),
            str(row["witnessId"]),
        )
    )
    return bindings


def _requirement_relation(document: Mapping[str, object]) -> set[tuple[str, ...]]:
    return {
        (
            _text(row, "requirementId"),
            _text(row, "ownerId"),
            safe_repo_path(_text(row, "specPath")),
            _text(row, "claimLevel"),
            _text(row, "proofState"),
        )
        for row in _object_rows(document, "requirements")
    }


def _command_relation(document: Mapping[str, object]) -> set[tuple[str, ...]]:
    return {
        (
            _text(row, "commandId"),
            _text(row, "command"),
            _text(row, "environmentClass"),
        )
        for row in _object_rows(document, "witnessCommands")
    }


def _requirement_non_claim_relation(
    document: Mapping[str, object],
) -> dict[str, tuple[str, ...]]:
    return {
        _text(row, "requirementId"): tuple(
            _string_array(row.get("nonClaims"), "requirement nonClaims")
        )
        for row in _object_rows(document, "requirements")
    }


def _binding_relation(document: Mapping[str, object]) -> set[tuple[object, ...]]:
    return {
        (
            _text(row, "requirementId"),
            _text(row, "scenarioId"),
            _text(row, "witnessId"),
            _text(row, "witnessKind"),
            safe_repo_path(_text(row, "witnessPath")),
            tuple(sorted(_string_array(row.get("commandIds"), "binding command ids"))),
            tuple(sorted(_string_array(row.get("environmentClasses"), "binding environments"))),
        )
        for row in _object_rows(document, "bindings")
    }


def _object_rows(document: Mapping[str, object], field: str) -> list[JsonObject]:
    return [as_object(row, field) for row in as_array(document.get(field), field)]


def _literal(value: Mapping[str, object], field: str, expected: object, context: str) -> None:
    if value.get(field) != expected or type(value.get(field)) is not type(expected):
        raise ValueError(f"{context} {field} must be {js_json_dumps(expected)}")


def _suffix(value: object, identity: str) -> str:
    result = _strict_text(value, identity)
    if result.startswith((SCENARIO_ID_PREFIX, WITNESS_ID_PREFIX, "REQ-CI-")):
        raise ValueError(f"{identity} must be a suffix, not a complete identity")
    return result


def _text(value: Mapping[str, object], field: str) -> str:
    return _strict_text(value.get(field), field)


def _sorted_string_set(value: object, context: str) -> list[str]:
    result = _string_array(value, context)
    if result != sorted(set(result)):
        raise ValueError(f"{context} must be unique and sorted")
    return result
