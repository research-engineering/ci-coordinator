from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import cast

from ci_coordinator.config_control._resources import ContractResource, contract_document
from ci_coordinator.config_control.contracts import PolicyDiagnostic

type JsonObject = dict[str, object]


@dataclass(frozen=True, slots=True)
class _SemanticCandidate:
    rule_id: str
    pointer: str


def validate_policy_semantics(document: JsonObject) -> PolicyDiagnostic | None:
    candidates = [
        *_pointer_class_candidates(document),
        *_repository_rule_candidates(document),
        *_dynamic_policy_candidates(document),
    ]
    if not candidates:
        return None
    code_order = _diagnostic_code_order()
    selected = min(
        candidates,
        key=lambda candidate: (
            _pointer_sort_key(document, candidate.pointer),
            code_order[_diagnostic_code(candidate.rule_id)],
            candidate.rule_id,
        ),
    )
    return PolicyDiagnostic(
        code=_diagnostic_code(selected.rule_id),
        phase="semantics",
        rule_id=selected.rule_id,
        instance_pointer=selected.pointer,
        parameters={},
    )


def _pointer_class_candidates(document: JsonObject) -> list[_SemanticCandidate]:
    candidates: list[_SemanticCandidate] = []
    for pointer, value in _class_occurrences(document, "canonical-string"):
        if _has_ecmascript_trim_endpoint(_string(value)):
            candidates.append(_SemanticCandidate("string.canonical-whitespace", pointer))
    for pointer, value in _class_occurrences(document, "case-key"):
        if any(ord(character) > 0x7F for character in _string(value)):
            candidates.append(_SemanticCandidate("string.ascii-case-key", pointer))
    for pointer, value in _class_occurrences(document, "nominal-id"):
        if not _valid_nominal_id(_string(value)):
            candidates.append(_SemanticCandidate("identity.valid-nominal-id", pointer))
    for pointer, value in _class_occurrences(document, "path-pattern"):
        if not _valid_path_pattern(_string(value)):
            candidates.append(_SemanticCandidate("path.valid-pattern", pointer))
    for pointer, value in _class_occurrences(document, "exact-unique-list"):
        candidates.extend(
            _duplicate_candidates(
                [_string(item) for item in _array(value)],
                pointer,
                "list.unique-value",
            )
        )
    return candidates


def _repository_rule_candidates(document: JsonObject) -> list[_SemanticCandidate]:
    repository = _object(document["repository"])
    default_branch = _string(repository["defaultBranch"])
    rules = [_object(rule) for rule in _array(repository["rules"])]
    candidates = _duplicate_candidates(
        [_string(rule["name"]) for rule in rules],
        "/repository/rules",
        "rule.unique-name",
        child_suffix="/name",
    )
    for rule_index, rule in enumerate(rules):
        rule_pointer = f"/repository/rules/{rule_index}"
        event = _object(rule["on"])
        branches = [_string(branch) for branch in _array(event["branches"])]
        candidates.extend(
            _duplicate_candidates(
                branches,
                f"{rule_pointer}/on/branches",
                "rule.unique-branch",
            )
        )
        if default_branch not in branches:
            candidates.append(
                _SemanticCandidate("rule.default-branch-covered", f"{rule_pointer}/on/branches")
            )
        mode = _string(rule["mode"])
        if mode == "dispatch":
            candidates.append(_SemanticCandidate("rule.dispatch-disabled", f"{rule_pointer}/mode"))
        timing = _object(rule["timing"])
        mutable = _integer(timing["mutableDecisionWindowSeconds"])
        absence = _integer(timing["absenceVerificationWindowSeconds"])
        late = _integer(timing["lateFindingWindowSeconds"])
        if mutable < absence:
            candidates.append(
                _SemanticCandidate(
                    "timing.mutable-after-absence",
                    f"{rule_pointer}/timing/mutableDecisionWindowSeconds",
                )
            )
        if late < mutable:
            candidates.append(
                _SemanticCandidate(
                    "timing.late-after-mutable",
                    f"{rule_pointer}/timing/lateFindingWindowSeconds",
                )
            )
        expected = [_object(signal) for signal in _array(rule["expectedSignals"])]
        omitted = [_object(signal) for signal in _array(rule["omittedSignals"])]
        expected_collision_keys = {_signal_collision_key(signal) for signal in expected}
        for signal_index, signal in enumerate(expected):
            signal_pointer = f"{rule_pointer}/expectedSignals/{signal_index}"
            if not _boolean(signal["required"]):
                candidates.append(
                    _SemanticCandidate("signal.required", f"{signal_pointer}/required")
                )
            if _string(signal["requiredConclusion"]) != "success":
                candidates.append(
                    _SemanticCandidate(
                        "signal.success-conclusion",
                        f"{signal_pointer}/requiredConclusion",
                    )
                )
        for signal_index, signal in enumerate(omitted):
            signal_pointer = f"{rule_pointer}/omittedSignals/{signal_index}"
            if _signal_collision_key(signal) in expected_collision_keys:
                candidates.append(
                    _SemanticCandidate("signal.expected-omitted-disjoint", signal_pointer)
                )
            if mode == "hybrid" and not _boolean(signal["verifyAbsence"]):
                candidates.append(
                    _SemanticCandidate(
                        "signal.hybrid-verifies-absence",
                        f"{signal_pointer}/verifyAbsence",
                    )
                )
    return candidates


def _dynamic_policy_candidates(document: JsonObject) -> list[_SemanticCandidate]:
    repository = _object(document["repository"])
    dynamic_value = repository["dynamicCi"]
    if dynamic_value is None:
        return []
    dynamic = _object(dynamic_value)
    candidates: list[_SemanticCandidate] = []
    advice = _object(dynamic["agentAdvice"])
    if _boolean(advice["enabled"]):
        if not _array(advice["modelIdAllowlist"]):
            candidates.append(
                _SemanticCandidate(
                    "agent.enabled-model-required",
                    "/repository/dynamicCi/agentAdvice/modelIdAllowlist",
                )
            )
        if not _array(advice["promptHashAllowlist"]):
            candidates.append(
                _SemanticCandidate(
                    "agent.enabled-prompt-required",
                    "/repository/dynamicCi/agentAdvice/promptHashAllowlist",
                )
            )
        if not _boolean(advice["promptInjectionEvalRequired"]):
            candidates.append(
                _SemanticCandidate(
                    "agent.injection-eval-required",
                    "/repository/dynamicCi/agentAdvice/promptInjectionEvalRequired",
                )
            )

    risk_classes = [_string(item) for item in _array(dynamic["riskClasses"])]
    obligations = [_object(item) for item in _array(dynamic["obligations"])]
    witnesses = [_object(item) for item in _array(dynamic["witnesses"])]
    profiles = [_object(item) for item in _array(dynamic["executionProfiles"])]
    candidates.extend(
        _derived_duplicate_candidates(
            obligations,
            "obligationId",
            "obligation.unique-id",
            "/repository/dynamicCi/obligations",
            child_suffix="/obligationId",
        )
    )
    candidates.extend(
        _derived_duplicate_candidates(
            witnesses,
            "witnessId",
            "witness.unique-id",
            "/repository/dynamicCi/witnesses",
            child_suffix="/witnessId",
        )
    )
    candidates.extend(
        _derived_duplicate_candidates(
            profiles,
            "profileId",
            "profile.unique-id",
            "/repository/dynamicCi/executionProfiles",
            child_suffix="/profileId",
        )
    )
    witnesses_by_id: dict[str, JsonObject] = {}
    for witness in witnesses:
        witnesses_by_id.setdefault(_string(witness["witnessId"]), witness)
    profile_ids = {_string(profile["profileId"]) for profile in profiles}
    referenced_witness_ids: set[str] = set()
    referenced_profile_ids: set[str] = set()
    referenced_risk_classes: set[str] = set()
    depth_order = _depth_order()
    for obligation_index, obligation in enumerate(obligations):
        pointer = f"/repository/dynamicCi/obligations/{obligation_index}"
        default_depth = _string(obligation["defaultDepth"])
        full_depth = _string(obligation["fullDepth"])
        if depth_order[full_depth] < depth_order[default_depth]:
            candidates.append(
                _SemanticCandidate("obligation.depth-monotonic", f"{pointer}/fullDepth")
            )
        responsibility = _object(obligation["responsibility"])
        paths = [_string(item) for item in _array(responsibility["paths"])]
        obligation_risks = [_string(item) for item in _array(responsibility["riskClasses"])]
        if _boolean(obligation["omitAllowed"]) and not paths and not obligation_risks:
            candidates.append(
                _SemanticCandidate(
                    "obligation.omission-has-responsibility",
                    f"{pointer}/responsibility",
                )
            )
        for item_index, risk_class in enumerate(obligation_risks):
            referenced_risk_classes.add(risk_class)
            if risk_class not in risk_classes:
                candidates.append(
                    _SemanticCandidate(
                        "obligation.known-risk-class",
                        f"{pointer}/responsibility/riskClasses/{item_index}",
                    )
                )
        for item_index, witness_value in enumerate(_array(obligation["requiredWitnessIds"])):
            witness_id = _string(witness_value)
            referenced_witness_ids.add(witness_id)
            required_witness = witnesses_by_id.get(witness_id)
            if required_witness is None:
                candidates.append(
                    _SemanticCandidate(
                        "obligation.known-witness",
                        f"{pointer}/requiredWitnessIds/{item_index}",
                    )
                )
                continue
            supported = {_string(item) for item in _array(required_witness["supportedDepths"])}
            if default_depth not in supported:
                candidates.append(
                    _SemanticCandidate(
                        "obligation.default-depth-supported",
                        f"{pointer}/defaultDepth",
                    )
                )
            if full_depth not in supported:
                candidates.append(
                    _SemanticCandidate(
                        "obligation.full-depth-supported",
                        f"{pointer}/fullDepth",
                    )
                )

    for witness_index, witness in enumerate(witnesses):
        profile_id = _string(witness["executionProfileId"])
        referenced_profile_ids.add(profile_id)
        if profile_id not in profile_ids:
            candidates.append(
                _SemanticCandidate(
                    "witness.known-execution-profile",
                    f"/repository/dynamicCi/witnesses/{witness_index}/executionProfileId",
                )
            )

    for item_index, risk_class in enumerate(risk_classes):
        if risk_class not in referenced_risk_classes:
            candidates.append(
                _SemanticCandidate(
                    "catalog.no-orphan-risk-class",
                    f"/repository/dynamicCi/riskClasses/{item_index}",
                )
            )
    for witness_index, witness in enumerate(witnesses):
        if _string(witness["witnessId"]) not in referenced_witness_ids:
            candidates.append(
                _SemanticCandidate(
                    "catalog.no-orphan-witness",
                    f"/repository/dynamicCi/witnesses/{witness_index}/witnessId",
                )
            )
    for profile_index, profile in enumerate(profiles):
        if _string(profile["profileId"]) not in referenced_profile_ids:
            candidates.append(
                _SemanticCandidate(
                    "catalog.no-orphan-profile",
                    f"/repository/dynamicCi/executionProfiles/{profile_index}/profileId",
                )
            )
        sharding = _object(profile["shardingPolicy"])
        if _integer(sharding["maxParallel"]) > _integer(sharding["maxShards"]):
            candidates.append(
                _SemanticCandidate(
                    "profile.parallelism-bounded",
                    f"/repository/dynamicCi/executionProfiles/{profile_index}"
                    "/shardingPolicy/maxParallel",
                )
            )
        if not any(
            _number(sharding[field]) > 0 for field in ("cpuWeight", "wallWeight", "operatorWeight")
        ):
            candidates.append(
                _SemanticCandidate(
                    "profile.objective-nonzero",
                    f"/repository/dynamicCi/executionProfiles/{profile_index}/shardingPolicy",
                )
            )
    return candidates


def _derived_duplicate_candidates(
    values: list[JsonObject],
    field: str,
    rule_id: str,
    pointer: str,
    *,
    child_suffix: str,
) -> list[_SemanticCandidate]:
    identities = [_string(value[field]) for value in values]
    return _duplicate_candidates(
        identities,
        pointer,
        rule_id,
        child_suffix=child_suffix,
    )


def _valid_nominal_id(value: str) -> bool:
    return (
        bool(value)
        and len(value) <= 64
        and "a" <= value[0] <= "z"
        and all(
            "a" <= character <= "z" or "0" <= character <= "9" or character in "._-"
            for character in value
        )
    )


def _duplicate_candidates(
    values: list[str],
    pointer: str,
    rule_id: str,
    *,
    child_suffix: str = "",
) -> list[_SemanticCandidate]:
    seen: set[str] = set()
    candidates: list[_SemanticCandidate] = []
    for index, value in enumerate(values):
        if value in seen:
            candidates.append(_SemanticCandidate(rule_id, f"{pointer}/{index}{child_suffix}"))
        else:
            seen.add(value)
    return candidates


def _class_occurrences(document: JsonObject, class_name: str) -> list[tuple[str, object]]:
    profile = _semantics_profile()
    classes = profile.get("pointerClasses")
    patterns = classes.get(class_name) if isinstance(classes, dict) else None
    if not isinstance(patterns, list) or not all(isinstance(pattern, str) for pattern in patterns):
        raise RuntimeError(f"semantic pointer class is invalid: {class_name}")
    return [
        occurrence
        for pattern in patterns
        for occurrence in _pattern_occurrences(document, pattern.split("/")[1:], "")
    ]


def _pattern_occurrences(
    value: object,
    tokens: list[str],
    pointer: str,
) -> list[tuple[str, object]]:
    if value is None:
        return []
    if not tokens:
        return [(pointer, value)]
    segment, remaining = tokens[0], tokens[1:]
    if segment == "*":
        return [
            occurrence
            for index, item in enumerate(_array(value))
            for occurrence in _pattern_occurrences(item, remaining, f"{pointer}/{index}")
        ]
    child = _object(value)[segment]
    encoded = segment.replace("~", "~0").replace("/", "~1")
    return _pattern_occurrences(child, remaining, f"{pointer}/{encoded}")


def _valid_path_pattern(value: str) -> bool:
    if not _valid_relative_path(value):
        return False
    forbidden = frozenset("]![()+@")
    if any(character in forbidden for character in value):
        return False
    index = 0
    while index < len(value):
        character = value[index]
        if character == "}":
            return False
        if character != "{":
            index += 1
            continue
        close = value.find("}", index + 1)
        if close == -1:
            return False
        alternatives = value[index + 1 : close].split(",")
        if len(alternatives) < 2 or any(not alternative for alternative in alternatives):
            return False
        if any(any(token in alternative for token in "*?{}") for alternative in alternatives):
            return False
        index = close + 1
    return True


def _valid_relative_path(value: str) -> bool:
    return (
        bool(value)
        and not value.startswith(("/", "./"))
        and "\\" not in value
        and ".." not in value.split("/")
    )


_ECMASCRIPT_TRIM_CODE_POINTS = frozenset(
    [
        *range(0x0009, 0x000E),
        0x0020,
        0x00A0,
        0x1680,
        *range(0x2000, 0x200B),
        0x2028,
        0x2029,
        0x202F,
        0x205F,
        0x3000,
        0xFEFF,
    ]
)


def _has_ecmascript_trim_endpoint(value: str) -> bool:
    return bool(value) and (
        ord(value[0]) in _ECMASCRIPT_TRIM_CODE_POINTS
        or ord(value[-1]) in _ECMASCRIPT_TRIM_CODE_POINTS
    )


def _signal_collision_key(signal: JsonObject) -> str:
    return (
        f"{_string(signal['kind'])}:"
        f"{_ascii_lower(_string(signal['workflowFile']))}:"
        f"{_ascii_lower(_string(signal['name']))}"
    )


def _ascii_lower(value: str) -> str:
    return "".join(
        chr(ord(character) + 32) if "A" <= character <= "Z" else character for character in value
    )


def _diagnostic_code(rule_id: str) -> str:
    profile = _semantics_profile()
    diagnostics = profile.get("diagnostics")
    projection = diagnostics.get("codeProjection") if isinstance(diagnostics, dict) else None
    if not isinstance(projection, dict):
        raise RuntimeError("semantic diagnostic code projection must be an object")
    candidate = projection.get(rule_id, projection.get("default"))
    if not isinstance(candidate, str):
        raise RuntimeError(f"semantic diagnostic code is missing: {rule_id}")
    return candidate


@cache
def _diagnostic_code_order() -> dict[str, int]:
    profile = contract_document(ContractResource.DOCUMENT_PROFILE)
    diagnostics = profile.get("diagnostics")
    codes = diagnostics.get("codes") if isinstance(diagnostics, dict) else None
    if not isinstance(codes, list) or not all(isinstance(code, str) for code in codes):
        raise RuntimeError("document profile diagnostic codes must be a string array")
    return {code: index for index, code in enumerate(codes)}


@cache
def _depth_order() -> dict[str, int]:
    profile = _semantics_profile()
    relations = profile.get("relations")
    values = relations.get("depthOrder") if isinstance(relations, dict) else None
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise RuntimeError("semantic depth order must be a string array")
    return {value: index for index, value in enumerate(values)}


@cache
def _semantics_profile() -> JsonObject:
    return contract_document(ContractResource.SEMANTIC_PROFILE)


def _pointer_sort_key(document: JsonObject, pointer: str) -> tuple[tuple[int, int | str], ...]:
    current: object = document
    result: list[tuple[int, int | str]] = []
    if not pointer:
        return ()
    for token in pointer[1:].split("/"):
        decoded = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            index = int(decoded)
            result.append((0, index))
            current = current[index]
        else:
            result.append((1, decoded))
            current = _object(current)[decoded]
    return tuple(result)


def _object(value: object) -> JsonObject:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise RuntimeError("semantic validation requires a structurally admitted object")
    return cast(JsonObject, value)


def _array(value: object) -> list[object]:
    if not isinstance(value, list):
        raise RuntimeError("semantic validation requires a structurally admitted array")
    return cast(list[object], value)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise RuntimeError("semantic validation requires a structurally admitted string")
    return value


def _integer(value: object) -> int:
    if type(value) is int:
        return value
    if type(value) is float and value.is_integer():
        return int(value)
    raise RuntimeError("semantic validation requires a structurally admitted integer")


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        raise RuntimeError("semantic validation requires a structurally admitted boolean")
    return value


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError("normalized policy number invariant failed")
    return float(value)
