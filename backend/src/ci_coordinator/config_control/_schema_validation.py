from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import Literal, cast

from jsonschema import Draft202012Validator

from ci_coordinator.config_control._parser_support import document_schema
from ci_coordinator.config_control._resources import ContractResource, contract_document

type SchemaIdentity = Literal["document", "compiled"]

_VALUE_KEYWORD_ORDER = (
    "type",
    "const",
    "enum",
    "required",
    "additionalProperties",
    "minimum",
    "maximum",
    "minLength",
    "maxLength",
    "pattern",
    "minItems",
    "maxItems",
)
_STRUCTURAL_KEYWORD_ORDER = (
    *_VALUE_KEYWORD_ORDER,
    "oneOf",
    "allOf",
    "properties",
    "items",
)


@dataclass(frozen=True, slots=True)
class StructuralFailure:
    keyword: str
    instance_pointer: str
    schema_pointer: str


def validate_structure(value: object, identity: SchemaIdentity) -> StructuralFailure | None:
    _admit_contract()
    failure = _first_failure(
        value,
        schema_document(identity),
        identity=identity,
        instance_pointer="",
        schema_pointer="",
    )
    library_valid = _validator(identity).is_valid(value)
    if (failure is None) != library_valid:
        raise RuntimeError("deterministic structural validation disagrees with jsonschema")
    return failure


def resolve_local_schema(
    schema: dict[str, object],
    schema_pointer: str,
    identity: SchemaIdentity,
) -> tuple[dict[str, object], str]:
    current = schema
    pointer = schema_pointer
    seen: set[str] = set()
    while "$ref" in current:
        reference = current.get("$ref")
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise RuntimeError("policy schema may contain only local references")
        if reference in seen:
            raise RuntimeError("policy schema contains a reference cycle")
        seen.add(reference)
        candidate: object = schema_document(identity)
        for token in reference[2:].split("/"):
            decoded = token.replace("~1", "/").replace("~0", "~")
            if not isinstance(candidate, dict):
                raise RuntimeError(f"policy schema reference does not resolve: {reference}")
            admitted = cast(dict[str, object], candidate)
            if decoded not in admitted:
                raise RuntimeError(f"policy schema reference does not resolve: {reference}")
            candidate = admitted[decoded]
        if not isinstance(candidate, dict):
            raise RuntimeError(f"policy schema reference is not an object: {reference}")
        current = cast(dict[str, object], candidate)
        pointer = reference[1:]
    return current, pointer


def select_type_branch(
    value: object,
    alternatives: list[object],
    schema_pointer: str,
    identity: SchemaIdentity,
) -> tuple[dict[str, object], str] | None:
    matches: list[tuple[dict[str, object], str]] = []
    for index, candidate in enumerate(alternatives):
        if not isinstance(candidate, dict):
            raise RuntimeError("policy schema oneOf branches must be objects")
        candidate = cast(dict[str, object], candidate)
        candidate_pointer = child_pointer(schema_pointer, "oneOf", str(index))
        effective, _ = resolve_local_schema(candidate, candidate_pointer, identity)
        if _matches_declared_type(value, effective.get("type")):
            matches.append((candidate, candidate_pointer))
    return matches[0] if len(matches) == 1 else None


def schema_document(identity: SchemaIdentity) -> dict[str, object]:
    if identity == "document":
        return document_schema()
    return contract_document(ContractResource.COMPILED_SCHEMA)


def child_pointer(pointer: str, *tokens: str) -> str:
    encoded = "/".join(token.replace("~", "~0").replace("/", "~1") for token in tokens)
    return f"{pointer}/{encoded}"


def _first_failure(
    value: object,
    schema: dict[str, object],
    *,
    identity: SchemaIdentity,
    instance_pointer: str,
    schema_pointer: str,
) -> StructuralFailure | None:
    effective, effective_pointer = resolve_local_schema(schema, schema_pointer, identity)

    for keyword in _VALUE_KEYWORD_ORDER:
        if keyword in effective and _keyword_fails(value, identity, effective_pointer, keyword):
            return _failure(keyword, instance_pointer, effective_pointer)

    alternatives = effective.get("oneOf")
    if isinstance(alternatives, list):
        selected = select_type_branch(
            value,
            cast(list[object], alternatives),
            effective_pointer,
            identity,
        )
        if selected is None:
            return _failure("oneOf", instance_pointer, effective_pointer)
        failure = _first_failure(
            value,
            selected[0],
            identity=identity,
            instance_pointer=instance_pointer,
            schema_pointer=selected[1],
        )
        if failure is not None:
            return failure

    conjunction = effective.get("allOf")
    if isinstance(conjunction, list):
        for index, candidate in enumerate(conjunction):
            if not isinstance(candidate, dict):
                raise RuntimeError("policy schema allOf branches must be objects")
            candidate = cast(dict[str, object], candidate)
            failure = _first_failure(
                value,
                candidate,
                identity=identity,
                instance_pointer=instance_pointer,
                schema_pointer=child_pointer(effective_pointer, "allOf", str(index)),
            )
            if failure is not None:
                return failure

    if isinstance(value, dict):
        value = cast(dict[str, object], value)
        properties = effective.get("properties")
        if isinstance(properties, dict):
            properties = cast(dict[str, object], properties)
            for key in sorted(set(value).intersection(properties)):
                child_schema = properties[key]
                if not isinstance(child_schema, dict):
                    raise RuntimeError("policy schema properties must be objects")
                child_schema = cast(dict[str, object], child_schema)
                failure = _first_failure(
                    value[key],
                    child_schema,
                    identity=identity,
                    instance_pointer=child_pointer(instance_pointer, key),
                    schema_pointer=child_pointer(effective_pointer, "properties", key),
                )
                if failure is not None:
                    return failure
    if isinstance(value, list):
        items = effective.get("items")
        if isinstance(items, dict):
            items = cast(dict[str, object], items)
            for index, item in enumerate(value):
                failure = _first_failure(
                    item,
                    items,
                    identity=identity,
                    instance_pointer=child_pointer(instance_pointer, str(index)),
                    schema_pointer=child_pointer(effective_pointer, "items"),
                )
                if failure is not None:
                    return failure
    return None


def _failure(keyword: str, instance_pointer: str, schema_pointer: str) -> StructuralFailure:
    return StructuralFailure(
        keyword=keyword,
        instance_pointer=instance_pointer,
        schema_pointer=child_pointer(schema_pointer, keyword),
    )


def _matches_declared_type(value: object, declared: object) -> bool:
    if isinstance(declared, list):
        return any(_matches_declared_type(value, candidate) for candidate in declared)
    return isinstance(declared, str) and Draft202012Validator.TYPE_CHECKER.is_type(value, declared)


def _keyword_fails(
    value: object,
    identity: SchemaIdentity,
    schema_pointer: str,
    keyword: str,
) -> bool:
    return not _keyword_validator(identity, schema_pointer, keyword).is_valid(value)


@cache
def _keyword_validator(
    identity: SchemaIdentity,
    schema_pointer: str,
    keyword: str,
) -> Draft202012Validator:
    schema = _schema_at_pointer(identity, schema_pointer)
    projection = {keyword: schema[keyword]}
    if keyword == "additionalProperties":
        properties = schema.get("properties")
        if isinstance(properties, dict):
            projection["properties"] = {key: {} for key in properties}
    return Draft202012Validator(projection)


def _schema_at_pointer(identity: SchemaIdentity, pointer: str) -> dict[str, object]:
    candidate: object = schema_document(identity)
    if pointer:
        for token in pointer[1:].split("/"):
            decoded = token.replace("~1", "/").replace("~0", "~")
            if isinstance(candidate, dict) and decoded in candidate:
                candidate = cast(dict[str, object], candidate)[decoded]
                continue
            if isinstance(candidate, list) and decoded.isdigit() and int(decoded) < len(candidate):
                candidate = candidate[int(decoded)]
                continue
            raise RuntimeError(f"policy schema pointer does not resolve: {pointer}")
    if not isinstance(candidate, dict):
        raise RuntimeError(f"policy schema pointer is not an object: {pointer}")
    return cast(dict[str, object], candidate)


@cache
def _validator(identity: SchemaIdentity) -> Draft202012Validator:
    schema = schema_document(identity)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@cache
def _admit_contract() -> None:
    profile = contract_document(ContractResource.DOCUMENT_PROFILE)
    diagnostics = profile.get("diagnostics")
    structural = (
        diagnostics.get("structuralFailureAlgorithm") if isinstance(diagnostics, dict) else None
    )
    keyword_order = structural.get("keywordOrder") if isinstance(structural, dict) else None
    if keyword_order != list(_STRUCTURAL_KEYWORD_ORDER):
        raise RuntimeError("unsupported structural keyword order")
