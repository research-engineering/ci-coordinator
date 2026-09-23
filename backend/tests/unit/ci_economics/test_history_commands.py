import json

import pytest
from pydantic import ValidationError

from ci_coordinator.ci_economics.history_commands import (
    ConfigureHistory,
    HistoryConfigurationRequest,
)
from ci_coordinator.ci_economics.history_payload import HistoryDatasetPayload
from ci_coordinator.kernel import hash_object
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER
from ci_economics.archive_factories import ARCHIVE_TIME, history_dataset


def _command_mapping() -> dict[str, object]:
    dataset = history_dataset()
    return {
        "installationId": 101,
        "repositoryId": 202,
        "expectedRevision": 0,
        "configuration": dataset.configuration.model_dump(mode="json"),
        "initialCreatedFrom": ARCHIVE_TIME.isoformat(),
        "rescan": False,
        "operationId": "configure-history",
        "actor": "operator-1",
    }


def test_history_command_roundtrip_preserves_scope_and_operation_identity() -> None:
    command = ConfigureHistory.model_validate(_command_mapping())
    assert command.scope == history_dataset().scope
    assert command.audit_key == "ci-economics-history:101:202:configure-history"
    restored = ConfigureHistory.model_validate_json(command.model_dump_json())
    assert restored == command and restored.command_digest == command.command_digest


def test_actor_free_request_preserves_the_existing_command_bytes_and_digest() -> None:
    original = _command_mapping()
    request = HistoryConfigurationRequest.model_validate(
        {key: value for key, value in original.items() if key != "actor"}
    )
    command = ConfigureHistory.from_request(request, actor="operator-1")
    assert command.model_dump_json() == json.dumps(original, separators=(",", ":"))
    assert command.command_digest == hash_object(original)
    assert command.scope == request.scope
    with pytest.raises(ValidationError):
        HistoryConfigurationRequest.model_validate(original)


@pytest.mark.parametrize("field,value", [("installation_id", True), ("actor", "forged")])
def test_actor_enrichment_revalidates_untrusted_instances(field: str, value: object) -> None:
    original = _command_mapping()
    request = HistoryConfigurationRequest.model_validate(
        {key: item for key, item in original.items() if key != "actor"}
    )
    object.__setattr__(request, field, value)
    with pytest.raises(ValidationError):
        ConfigureHistory.from_request(request, actor="operator-1")


@pytest.mark.parametrize("actor", ["", "x\x00y", "\ud800", "x" * 513])
def test_actor_enrichment_rejects_invalid_server_identity(actor: str) -> None:
    original = _command_mapping()
    request = HistoryConfigurationRequest.model_validate(
        {key: item for key, item in original.items() if key != "actor"}
    )
    with pytest.raises(ValidationError):
        ConfigureHistory.from_request(request, actor=actor)


@pytest.mark.parametrize(
    "missing",
    [
        "installationId",
        "repositoryId",
        "expectedRevision",
        "configuration",
        "initialCreatedFrom",
        "rescan",
        "operationId",
    ],
)
def test_public_request_requires_each_non_actor_operand(missing: str) -> None:
    original = _command_mapping()
    request = {key: value for key, value in original.items() if key not in {missing, "actor"}}
    with pytest.raises(ValidationError):
        HistoryConfigurationRequest.model_validate(request)


@pytest.mark.parametrize(
    "field,value",
    [
        ("installationId", True),
        ("repositoryId", 0),
        ("expectedRevision", -1),
        ("expectedRevision", MAX_SAFE_JSON_INTEGER),
        ("initialCreatedFrom", "2020-02-31T00:00:00Z"),
        ("initialCreatedFrom", "2020-01-01T00:00:00.000001Z"),
        ("initialCreatedFrom", None),
        ("expectedRevision", 1),
        ("rescan", True),
        ("rescan", "false"),
        ("operationId", ""),
        ("operationId", "invalid\noperation"),
        ("actor", ""),
        ("actor", "x\x00y"),
        ("actor", "\ud800"),
        ("actor", "x" * 513),
        ("extra", True),
    ],
)
def test_history_command_rejects_malformed_authority_operands(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        ConfigureHistory.model_validate({**_command_mapping(), field: value})


@pytest.mark.parametrize(
    "initial,field,value",
    [
        (False, "installationId", 102),
        (False, "repositoryId", 203),
        (False, "expectedRevision", 2),
        (True, "initialCreatedFrom", "2019-01-01T00:00:00Z"),
        (False, "rescan", True),
        (False, "operationId", "another-operation"),
        (False, "actor", "operator-2"),
        (
            False,
            "configuration",
            {**history_dataset().configuration.model_dump(mode="json"), "enabled": False},
        ),
    ],
)
def test_command_digest_is_sensitive_to_each_authority_operand(
    initial: bool, field: str, value: object
) -> None:
    payload = _command_mapping()
    if not initial:
        payload.update(expectedRevision=1, initialCreatedFrom=None)
    command = ConfigureHistory.model_validate(payload)
    changed = ConfigureHistory.model_validate({**payload, field: value})
    assert changed.command_digest != command.command_digest


def test_existing_configuration_requires_a_null_initial_boundary() -> None:
    payload = {**_command_mapping(), "expectedRevision": 1, "initialCreatedFrom": None}
    assert ConfigureHistory.model_validate(payload).initial_created_from is None
    assert ConfigureHistory.model_validate({**payload, "rescan": True}).rescan
    with pytest.raises(ValidationError):
        ConfigureHistory.model_validate({**payload, "initialCreatedFrom": ARCHIVE_TIME.isoformat()})


def test_dataset_audit_payload_revalidates_the_domain_relations() -> None:
    dataset = history_dataset()
    assert HistoryDatasetPayload.model_validate(dataset.canonical_mapping()).to_dataset() == dataset
    with pytest.raises(ValidationError):
        HistoryDatasetPayload.model_validate({**dataset.canonical_mapping(), "state": "erased"})
