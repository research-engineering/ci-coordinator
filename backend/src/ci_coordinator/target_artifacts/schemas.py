"""Packaged JSON Schema admission for target artifacts."""

from __future__ import annotations

from functools import cache
from importlib.resources import files
from typing import Final, Literal, cast

from jsonschema import Draft202012Validator

from ci_coordinator.kernel import load_strict_json

type TargetArtifactSchema = Literal[
    "source",
    "execution_registry",
    "test_manifest",
    "graph",
    "plan_trust_root",
]

_RESOURCE_NAMES: Final[dict[TargetArtifactSchema, str]] = {
    "source": "target-artifacts-source.schema.v1.json",
    "execution_registry": "target-execution-registry.schema.v1.json",
    "test_manifest": "test-manifest.schema.v1.json",
    "graph": "dependency-graph.schema.v1.json",
    "plan_trust_root": "plan-trust-root.schema.v1.json",
}


class TargetArtifactSchemaError(ValueError):
    """A source or rendered artifact is outside its normative JSON Schema."""


def validate_artifact_value(schema_name: TargetArtifactSchema, value: object) -> None:
    if not _validator(schema_name).is_valid(value):
        raise TargetArtifactSchemaError(f"{schema_name} does not satisfy its schema")


@cache
def _validator(schema_name: TargetArtifactSchema) -> Draft202012Validator:
    resource = files("ci_coordinator.target_artifacts.resources").joinpath(
        _RESOURCE_NAMES[schema_name]
    )
    schema = load_strict_json(resource.read_bytes(), max_bytes=1_048_576)
    if type(schema) is not dict:
        raise RuntimeError("target artifact schema must be an object")
    document = cast(dict[str, object], schema)
    Draft202012Validator.check_schema(document)
    return Draft202012Validator(document)
