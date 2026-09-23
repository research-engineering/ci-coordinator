from __future__ import annotations

import json

from ci_coordinator.config_control import PolicyDiagnostic, RepositoryScope, ValidatedEpochDraft
from ci_coordinator.config_control._document import parse_policy_document
from ci_coordinator.config_control._parser_support import ParsedDocument
from ci_coordinator.config_control._rules import normalize_policy_document

HASH = "a" * 64


def policy_failure(source: bytes, source_format: str) -> PolicyDiagnostic:
    result = parse_policy_document(source, source_format)
    assert isinstance(result, PolicyDiagnostic)
    return result


def normalization_failure(value: object) -> PolicyDiagnostic:
    result = normalize_policy_document(value)
    assert isinstance(result, PolicyDiagnostic)
    return result


def valid_policy_document() -> dict[str, object]:
    return {
        "schemaVersion": "ci-repository-policy/v1",
        "repository": {
            "installationId": 1,
            "repositoryId": 2,
            "owner": "example-org",
            "name": "ci-coordinator",
            "defaultBranch": "main",
            "rules": [
                {
                    "name": "main",
                    "on": {"event": "push", "branches": ["main"]},
                    "mode": "observe",
                    "expectedSignals": [
                        {
                            "kind": "workflow",
                            "name": "CI",
                            "workflowFile": "ci.yml",
                            "source": "native",
                            "requiredConclusion": "success",
                            "required": True,
                        }
                    ],
                }
            ],
        },
    }


def valid_policy_source() -> bytes:
    return json.dumps(
        valid_policy_document(),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def parsed_value(result: object) -> object:
    assert isinstance(result, ParsedDocument)
    return result.value


def assert_failure(
    failure: PolicyDiagnostic,
    code: str,
    *,
    pointer: str | None = "",
    parameters: object | None = None,
) -> None:
    assert failure.code == code
    if pointer is not None:
        assert failure.instance_pointer == pointer
    assert dict(failure.parameters) == ({} if parameters is None else parameters)


def valid_epoch_draft() -> ValidatedEpochDraft:
    return ValidatedEpochDraft(
        source_format="json",
        source_bytes=b"{}",
        scope=RepositoryScope(installation_id=1, repository_id=2),
        normalized_document_bytes=b"{}",
        compiled_policy_bytes=b"{}",
        document_schema_id="document-schema",
        document_profile_id="document-profile",
        semantic_profile_id="semantic-profile",
        compiled_schema_id="compiled-schema",
        producer_resource_profile_id="resource-profile",
        producer_byte_profile_id="byte-profile",
        producer_feasibility_profile_id="feasibility-profile",
        source_hash=HASH,
        document_hash=HASH,
        epoch_hash=HASH,
        epoch_id=HASH,
    )


def valid_epoch_draft_arguments() -> dict[str, object]:
    return {
        "source_format": "json",
        "source_bytes": b"{}",
        "scope": RepositoryScope(installation_id=1, repository_id=2),
        "normalized_document_bytes": b"{}",
        "compiled_policy_bytes": b"{}",
        "document_schema_id": "document-schema",
        "document_profile_id": "document-profile",
        "semantic_profile_id": "semantic-profile",
        "compiled_schema_id": "compiled-schema",
        "producer_resource_profile_id": "resource-profile",
        "producer_byte_profile_id": "byte-profile",
        "producer_feasibility_profile_id": "feasibility-profile",
        "source_hash": HASH,
        "document_hash": HASH,
        "epoch_hash": HASH,
        "epoch_id": HASH,
    }
