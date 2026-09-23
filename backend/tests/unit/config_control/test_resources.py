from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ci_coordinator.config_control._resources import (
    CONTRACT_IDENTITIES,
    ContractResource,
    contract_bytes,
    contract_document,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CANONICAL_ROOT = REPOSITORY_ROOT / "docs/specs/ci-coordinator-core"


@pytest.mark.parametrize("resource", tuple(ContractResource))
def test_packaged_contract_is_byte_exact_canonical_projection(
    resource: ContractResource,
) -> None:
    canonical = (CANONICAL_ROOT / resource.value).read_bytes()
    packaged = contract_bytes(resource)

    assert packaged == canonical
    assert sha256(packaged).digest() == sha256(canonical).digest()


@pytest.mark.parametrize("resource", tuple(ContractResource))
def test_contract_loader_verifies_machine_identity(resource: ContractResource) -> None:
    document = contract_document(resource)
    identity = CONTRACT_IDENTITIES[resource]

    assert document[identity.field] == identity.expected


def test_contract_loader_does_not_share_mutable_documents() -> None:
    first = contract_document(ContractResource.DOCUMENT_PROFILE)
    first["profileId"] = "mutated"

    second = contract_document(ContractResource.DOCUMENT_PROFILE)
    assert second["profileId"] == "ci-config-document/v1"


def test_every_schema_default_is_valid_against_its_effective_subschema() -> None:
    schema = contract_document(ContractResource.DOCUMENT_SCHEMA)
    validator = Draft202012Validator(schema)
    annotated = [candidate for candidate in _schema_objects(schema) if "default" in candidate]

    assert len(annotated) == 22
    assert all(
        validator.evolve(schema=candidate).is_valid(candidate["default"]) for candidate in annotated
    )


def _schema_objects(value: object) -> list[dict[str, object]]:
    if isinstance(value, dict):
        descendants = [value]
        for child in value.values():
            descendants.extend(_schema_objects(child))
        return descendants
    if isinstance(value, list):
        descendants = []
        for child in value:
            descendants.extend(_schema_objects(child))
        return descendants
    return []
