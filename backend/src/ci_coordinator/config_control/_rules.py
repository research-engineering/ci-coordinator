from __future__ import annotations

from typing import Final, cast

from ci_coordinator.config_control._parser_support import document_schema
from ci_coordinator.config_control._resources import ContractResource, contract_document
from ci_coordinator.config_control._schema_validation import (
    child_pointer,
    resolve_local_schema,
    select_type_branch,
    validate_structure,
)
from ci_coordinator.config_control.contracts import PolicyDiagnostic

type StructureAdmissionResult = dict[str, object] | PolicyDiagnostic


def normalize_policy_document(value: object) -> StructureAdmissionResult:
    failure = _validate_document_structure(value)
    if failure is not None:
        return failure
    normalized = _project_defaults(value, document_schema(), "")
    failure = _validate_document_structure(normalized)
    if failure is not None:
        return failure
    if not isinstance(normalized, dict):
        raise RuntimeError("validated repository policy root must be an object")
    admitted = cast(dict[str, object], normalized)
    _assert_required_normalized_pointers(admitted)
    return admitted


def _validate_document_structure(value: object) -> PolicyDiagnostic | None:
    failure = validate_structure(value, "document")
    if failure is None:
        return None
    return PolicyDiagnostic(
        code="structure.invalid",
        phase="structure",
        rule_id=f"schema:{failure.keyword}",
        instance_pointer=failure.instance_pointer,
        parameters={
            "schemaKeyword": failure.keyword,
            "schemaPointer": failure.schema_pointer,
        },
    )


def _project_defaults(value: object, schema: dict[str, object], schema_pointer: str) -> object:
    effective, effective_pointer = resolve_local_schema(schema, schema_pointer, "document")
    alternatives = effective.get("oneOf")
    if isinstance(alternatives, list):
        selected = select_type_branch(
            value,
            cast(list[object], alternatives),
            effective_pointer,
            "document",
        )
        if selected is None:
            raise RuntimeError("validated document has no unique default-projection branch")
        return _project_defaults(value, selected[0], selected[1])

    projected = _clone_json(value)
    conjunction = effective.get("allOf")
    if isinstance(conjunction, list):
        for index, candidate in enumerate(conjunction):
            if not isinstance(candidate, dict):
                raise RuntimeError("document schema allOf branches must be objects")
            projected = _project_defaults(
                projected,
                cast(dict[str, object], candidate),
                child_pointer(effective_pointer, "allOf", str(index)),
            )

    if isinstance(projected, dict):
        projected = cast(dict[str, object], projected)
        properties = effective.get("properties")
        if isinstance(properties, dict):
            properties = cast(dict[str, object], properties)
            for key in sorted(properties):
                child_schema = properties[key]
                if not isinstance(child_schema, dict):
                    raise RuntimeError("document schema properties must be objects")
                child_schema = cast(dict[str, object], child_schema)
                child_schema_pointer = child_pointer(effective_pointer, "properties", key)
                if key not in projected:
                    default = _default_annotation(child_schema, child_schema_pointer)
                    if default is _NO_DEFAULT:
                        continue
                    projected[key] = _clone_json(default)
                projected[key] = _project_defaults(
                    projected[key],
                    child_schema,
                    child_schema_pointer,
                )
    elif isinstance(projected, list):
        items = effective.get("items")
        if isinstance(items, dict):
            items = cast(dict[str, object], items)
            item_pointer = child_pointer(effective_pointer, "items")
            projected = [_project_defaults(item, items, item_pointer) for item in projected]
    return projected


_NO_DEFAULT: Final = object()


def _default_annotation(schema: dict[str, object], schema_pointer: str) -> object:
    if "default" in schema:
        return schema["default"]
    effective, _ = resolve_local_schema(schema, schema_pointer, "document")
    return effective.get("default", _NO_DEFAULT)


def _clone_json(value: object) -> object:
    if isinstance(value, dict):
        return {key: _clone_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clone_json(item) for item in value]
    return value


def _assert_required_normalized_pointers(value: object) -> None:
    profile = contract_document(ContractResource.DOCUMENT_PROFILE)
    normalization = profile.get("normalization")
    patterns = (
        normalization.get("requiredNormalizedPointers") if isinstance(normalization, dict) else None
    )
    if not isinstance(patterns, list) or not all(isinstance(pattern, str) for pattern in patterns):
        raise RuntimeError("document profile normalized pointers must be a string array")
    for pattern in patterns:
        if not _normalized_pointer_exists(value, pattern.split("/")[1:]):
            raise RuntimeError(
                f"normalized document is missing required pointer pattern: {pattern}"
            )


def _normalized_pointer_exists(value: object, tokens: list[str]) -> bool:
    if value is None or not tokens:
        return True
    segment, remaining = tokens[0], tokens[1:]
    if segment == "*":
        return isinstance(value, list) and all(
            _normalized_pointer_exists(item, remaining) for item in value
        )
    decoded = segment.replace("~1", "/").replace("~0", "~")
    if not isinstance(value, dict):
        return False
    admitted = cast(dict[str, object], value)
    return decoded in admitted and _normalized_pointer_exists(admitted[decoded], remaining)
