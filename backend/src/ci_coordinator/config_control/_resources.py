from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from importlib.resources import files


class ContractResource(StrEnum):
    DOCUMENT_PROFILE = "config-document-profile.v1.json"
    DOCUMENT_SCHEMA = "repository-policy.schema.v1.json"
    SEMANTIC_PROFILE = "repository-policy-semantics.v1.json"
    COMPILED_SCHEMA = "compiled-repository-policy.schema.v1.json"
    RESULT_PROFILE = "policy-admission-result-profile.v1.json"
    FEASIBILITY_PROFILE = "config-producer-feasibility-profile.v1.json"
    AUDIT_RESOURCE_PROFILE = "audit-json-resource-profile.v1.json"
    AUDIT_BYTE_PROFILE = "audit-persistence-byte-profile.v1.json"


@dataclass(frozen=True, slots=True)
class ContractIdentity:
    field: str
    expected: str


CONTRACT_IDENTITIES = {
    ContractResource.DOCUMENT_PROFILE: ContractIdentity(
        field="profileId",
        expected="ci-config-document/v1",
    ),
    ContractResource.DOCUMENT_SCHEMA: ContractIdentity(
        field="$id",
        expected=(
            "https://github.com/research-engineering/ci-coordinator/"
            "schemas/repository-policy.schema.v1.json"
        ),
    ),
    ContractResource.SEMANTIC_PROFILE: ContractIdentity(
        field="profileId",
        expected="ci-repository-policy-semantics/v1",
    ),
    ContractResource.COMPILED_SCHEMA: ContractIdentity(
        field="$id",
        expected=(
            "https://github.com/research-engineering/ci-coordinator/"
            "schemas/compiled-repository-policy.schema.v1.json"
        ),
    ),
    ContractResource.RESULT_PROFILE: ContractIdentity(
        field="profileId",
        expected="ci-policy-admission-result/v1",
    ),
    ContractResource.FEASIBILITY_PROFILE: ContractIdentity(
        field="profileId",
        expected="ci-config-dynamic-ci-audit-feasibility/v1",
    ),
    ContractResource.AUDIT_RESOURCE_PROFILE: ContractIdentity(
        field="profileId",
        expected="ci-audit-event-json-resources/v1",
    ),
    ContractResource.AUDIT_BYTE_PROFILE: ContractIdentity(
        field="profileId",
        expected="ci-audit-event-persistence-bytes/v1",
    ),
}


@cache
def contract_bytes(resource: ContractResource) -> bytes:
    return files(__package__).joinpath("resources", resource.value).read_bytes()


def contract_document(resource: ContractResource) -> dict[str, object]:
    candidate = json.loads(contract_bytes(resource))
    if not isinstance(candidate, dict):
        raise RuntimeError(f"contract resource {resource.value} must contain a JSON object")

    identity = CONTRACT_IDENTITIES[resource]
    if candidate.get(identity.field) != identity.expected:
        raise RuntimeError(f"contract resource {resource.value} has an unexpected identity")
    return candidate


def contract_identity(resource: ContractResource) -> str:
    contract_document(resource)
    return CONTRACT_IDENTITIES[resource].expected
