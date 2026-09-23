from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from scripts.proofkit_common import JsonObject, as_array, as_object
from scripts.proofkit_selective_plan import path_matches_any

SELECTIVE_PLAN_OUTPUT_KEYS = frozenset(
    {
        "artifactIntegrity",
        "changedPaths",
        "failures",
        "fallbackCoverage",
        "generatedArtifacts",
        "nonClaims",
        "planState",
        "privatePathExclusions",
        "proofLikePaths",
        "publicApiContractTouched",
        "requiredCommands",
        "scanObligation",
        "schemaVersion",
        "skippedGates",
        "touchedRequirementWitnesses",
        "unknownEdges",
    }
)


@dataclass(frozen=True, slots=True)
class RequiredCommandRoute:
    command: str
    command_id: str
    command_ownership: str | None
    reason: str
    source_path: str | None


def assert_admitted_plan(
    report: Mapping[str, object],
    label: str,
    *,
    input_value: Mapping[str, object],
) -> None:
    assert_selective_plan_contract(report, label, input_value=input_value)
    if (
        report.get("planState") != "ok"
        or _array_or_empty(report.get("failures"))
        or _array_or_empty(report.get("unknownEdges"))
    ):
        raise ValueError(f"{label} was not admitted")


def assert_selective_plan_contract(
    report: Mapping[str, object],
    label: str,
    *,
    input_value: Mapping[str, object],
) -> None:
    observed_keys = set(report)
    if observed_keys != SELECTIVE_PLAN_OUTPUT_KEYS:
        missing = sorted(SELECTIVE_PLAN_OUTPUT_KEYS - observed_keys)
        extra = sorted(observed_keys - SELECTIVE_PLAN_OUTPUT_KEYS)
        raise ValueError(f"{label} output keys differ; missing={missing}, extra={extra}")
    if type(report.get("schemaVersion")) is not int or report["schemaVersion"] != 1:
        raise ValueError(f"{label} emitted an unsupported schema version")
    assert_required_commands_match_input(report, input_value, label=label)


def assert_required_commands_match_input(
    report: Mapping[str, object],
    input_value: Mapping[str, object],
    *,
    label: str,
) -> None:
    observed_rows = _object_rows(report.get("requiredCommands"), "required commands")
    observed = {
        _required_command_route(row, context=f"{label} required command") for row in observed_rows
    }
    if len(observed) != len(observed_rows):
        raise ValueError(f"{label} repeated a required command route")
    expected = _expected_required_command_routes(input_value)
    if observed != expected:
        missing = sorted(repr(route) for route in expected - observed)
        unexpected = sorted(repr(route) for route in observed - expected)
        raise ValueError(
            f"{label} required command routes differ; missing={missing}, unexpected={unexpected}"
        )


def _expected_required_command_routes(
    input_value: Mapping[str, object],
) -> set[RequiredCommandRoute]:
    routes: dict[tuple[str, str, str | None], RequiredCommandRoute] = {}

    def add(route: RequiredCommandRoute) -> None:
        identity = (route.command_id, route.command, route.source_path)
        previous = routes.get(identity)
        if previous is not None and previous != route:
            raise ValueError(
                f"selective-plan input has a command route collision: {route.command_id}"
            )
        routes[identity] = route

    _assert_inactive_route_sources(input_value)
    for item in _object_rows(input_value.get("baseCommands"), "base commands"):
        add(_required_command_route(item, context="base command"))
    full_workspace = input_value.get("fullWorkspaceCommand")
    if full_workspace is not None:
        add(
            _required_command_route(
                as_object(full_workspace, "full workspace command"),
                context="full workspace command",
            )
        )
    for item in _object_rows(input_value.get("packageCommands"), "package commands"):
        add(_required_command_route(item, context="package command"))

    changed_paths = _string_array(input_value.get("changedPaths"), "changed paths")
    for rule in _object_rows(input_value.get("pathTriggeredCommands"), "path commands"):
        patterns = _string_array(rule.get("pathPatterns"), "path command patterns")
        if any(path_matches_any(patterns, path) for path in changed_paths):
            command = as_object(rule.get("command"), "path command")
            add(_required_command_route(command, context="path command"))

    scan = as_object(input_value.get("scanObligation"), "scan obligation")
    add(
        _required_command_route(
            {
                "command": scan.get("command"),
                "commandOwnership": scan.get("commandOwnership"),
                "id": scan.get("commandId"),
                "reason": scan.get("reason"),
            },
            context="scan obligation",
        )
    )

    for witness in _object_rows(
        input_value.get("touchedRequirementWitnesses"),
        "touched requirement witnesses",
    ):
        source_path = _required_string(witness, "path")
        for command_text in _string_array(witness.get("commands"), f"{source_path} commands"):
            add(
                RequiredCommandRoute(
                    command=command_text,
                    command_id="requirement-witness",
                    command_ownership=None,
                    reason="changed_requirement_witness",
                    source_path=source_path,
                )
            )

    dependency = as_object(input_value.get("dependencyFreshness"), "dependency freshness")
    if _string_array(dependency.get("paths"), "dependency freshness paths"):
        add(
            RequiredCommandRoute(
                command=_required_string(dependency, "command"),
                command_id="dependency-freshness",
                command_ownership=None,
                reason="workspace_dependency_surface_changed",
                source_path=None,
            )
        )

    ignored_paths = set(
        _string_array(input_value.get("ignoredProofLikePaths"), "ignored proof-like paths")
    )
    proof_patterns = _string_array(
        input_value.get("proofLikePathPatterns"), "proof-like path patterns"
    )
    proof_like_touched = any(
        path not in ignored_paths and path_matches_any(proof_patterns, path)
        for path in changed_paths
    )
    requirement_impact = as_object(input_value.get("requirementImpact"), "requirement impact")
    requirement_impact_touched = requirement_impact.get("touched")
    if type(requirement_impact_touched) is not bool:
        raise ValueError("requirement impact touched must be boolean")
    if requirement_impact_touched or proof_like_touched:
        add(
            RequiredCommandRoute(
                command=_required_string(requirement_impact, "command"),
                command_id="requirement-impact",
                command_ownership=None,
                reason="changed_requirement_or_proof_surface",
                source_path=None,
            )
        )

    public_api = as_object(input_value.get("publicApi"), "public API")
    public_api_touched = public_api.get("touched")
    if type(public_api_touched) is not bool:
        raise ValueError("public API touched must be boolean")
    if public_api_touched:
        add(
            RequiredCommandRoute(
                command=_required_string(public_api, "command"),
                command_id="public-api",
                command_ownership=None,
                reason="public_api_contract_surface_changed",
                source_path=None,
            )
        )
    return set(routes.values())


def _assert_inactive_route_sources(input_value: Mapping[str, object]) -> None:
    for field in (
        "artifactIntegrityPolicies",
        "fallbackCoverage",
        "generatedArtifactRules",
    ):
        if as_array(input_value.get(field), f"selective-plan input {field}"):
            raise ValueError(f"CI Coordinator route oracle does not admit non-empty {field}")


def _required_command_route(value: Mapping[str, object], *, context: str) -> RequiredCommandRoute:
    allowed_keys = {"command", "commandOwnership", "id", "reason", "sourcePath"}
    mandatory_keys = {"command", "id", "reason"}
    missing = mandatory_keys - set(value)
    extra = set(value) - allowed_keys
    if missing or extra:
        raise ValueError(
            f"{context} fields differ; missing={sorted(missing)}, extra={sorted(extra)}"
        )
    ownership = value.get("commandOwnership")
    source_path = value.get("sourcePath")
    if ownership is not None and (not isinstance(ownership, str) or not ownership):
        raise ValueError(f"{context} commandOwnership must be non-empty text")
    if source_path is not None and (not isinstance(source_path, str) or not source_path):
        raise ValueError(f"{context} sourcePath must be non-empty text")
    return RequiredCommandRoute(
        command=_required_nonempty_string(value, "command", context),
        command_id=_required_nonempty_string(value, "id", context),
        command_ownership=ownership,
        reason=_required_nonempty_string(value, "reason", context),
        source_path=source_path,
    )


def _object_rows(value: object, context: str) -> list[JsonObject]:
    return [as_object(row, context) for row in _array_or_empty(value)]


def _array_or_empty(value: object) -> list[object]:
    return [] if value is None else list(as_array(value, "report field"))


def _required_string(value: Mapping[str, object], field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str):
        raise TypeError(f"{field} must be a string")
    return result


def _required_nonempty_string(value: Mapping[str, object], field: str, context: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result:
        raise ValueError(f"{context} {field} must be non-empty text")
    return result


def _string_array(value: object, context: str) -> list[str]:
    raw = as_array(value, context)
    if any(not isinstance(item, str) for item in raw):
        raise ValueError(f"{context} must contain only strings")
    return [item for item in raw if isinstance(item, str)]
