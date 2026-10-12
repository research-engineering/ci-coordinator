import json
from datetime import timedelta

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


def test_optional_expansion_preserves_old_command_bytes_and_binds_new_lower_bound() -> None:
    original = {**_command_mapping(), "expectedRevision": 1, "initialCreatedFrom": None}
    absent = ConfigureHistory.model_validate(original)
    explicit_null = ConfigureHistory.model_validate({**original, "expandCreatedFrom": None})
    expanded = ConfigureHistory.model_validate(
        {**original, "expandCreatedFrom": (ARCHIVE_TIME - timedelta(days=400)).isoformat()}
    )

    assert absent.model_dump_json() == json.dumps(original, separators=(",", ":"))
    assert explicit_null.model_dump_json() == absent.model_dump_json()
    assert explicit_null.command_digest == absent.command_digest
    assert expanded.command_digest != absent.command_digest
    assert ConfigureHistory.model_validate_json(expanded.model_dump_json()) == expanded


@pytest.mark.parametrize(
    "patch,reason",
    [
        (
            {"expectedRevision": 0, "initialCreatedFrom": ARCHIVE_TIME.isoformat()},
            "history expansion requires an existing dataset",
        ),
        ({"expandCreatedFrom": "2020-02-31T00:00:00Z"}, "day is out of range for month"),
        (
            {"expandCreatedFrom": "2019-01-01T00:00:00.000001Z"},
            "history boundary requires second precision",
        ),
        ({"rescan": True}, "history expansion requires an existing dataset"),
    ],
)
def test_expansion_rejects_initial_rescan_and_invalid_calendar_or_precision(
    patch: dict[str, object],
    reason: str,
) -> None:
    baseline = {
        **{key: value for key, value in _command_mapping().items() if key != "actor"},
        "expectedRevision": 1,
        "initialCreatedFrom": None,
        "expandCreatedFrom": "2019-01-01T00:00:00Z",
    }
    assert HistoryConfigurationRequest.model_validate(baseline).expand_created_from is not None
    with pytest.raises(ValidationError) as error:
        HistoryConfigurationRequest.model_validate({**baseline, **patch})
    assert any(reason in issue["msg"] for issue in error.value.errors()), error.value.errors()


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


_CATALOG_SOURCE = {"kind": "repository_created_at", "repositoryCreatedAt": "2020-01-01T12:34:56Z"}


def test_omitted_and_null_bound_source_keep_existing_digest_bytes() -> None:
    # Kills: serializing populationBoundSource: null, or a default "explicit" key.
    original = _command_mapping()
    absent = ConfigureHistory.model_validate(original)
    explicit_null = ConfigureHistory.model_validate({**original, "populationBoundSource": None})
    sourced = ConfigureHistory.model_validate(
        {**original, "populationBoundSource": _CATALOG_SOURCE}
    )

    assert absent.model_dump_json() == json.dumps(original, separators=(",", ":"))
    assert explicit_null.model_dump_json() == absent.model_dump_json()
    assert explicit_null.command_digest == absent.command_digest == hash_object(original)
    assert sourced.command_digest != absent.command_digest
    assert sourced.population_bound_source is not None
    assert ConfigureHistory.model_validate_json(sourced.model_dump_json()) == sourced


@pytest.mark.parametrize(
    "expanded,bound,created_at,admitted",
    [
        (False, "2020-01-01T00:00:00Z", "2020-01-01T23:59:59Z", True),
        (False, "2020-01-01T00:00:00Z", "2020-01-01T00:00:00+00:00", True),
        (True, "2019-06-01T00:00:00Z", "2019-06-01T08:00:00Z", True),
        (False, "2020-01-01T00:00:01Z", "2020-01-01T12:34:56Z", False),
        (False, "2020-01-02T00:00:00Z", "2020-01-01T12:34:56Z", False),
        (True, "2019-05-31T00:00:00Z", "2019-06-01T08:00:00Z", False),
        (False, "2019-12-31T00:00:00Z", "2020-01-01T01:00:00+00:00", False),
    ],
)
def test_catalog_bound_source_requires_exact_utc_day_floor_of_its_bound(
    expanded: bool, bound: str, created_at: str, admitted: bool
) -> None:
    # Kills: dropping or loosening the floor validator (second, day or local-time floor).
    payload = {
        **_command_mapping(),
        "populationBoundSource": {
            "kind": "repository_created_at",
            "repositoryCreatedAt": created_at,
        },
    }
    if expanded:
        payload.update(expectedRevision=1, initialCreatedFrom=None, expandCreatedFrom=bound)
    else:
        payload.update(initialCreatedFrom=bound)
    if admitted:
        assert ConfigureHistory.model_validate(payload).population_bound_source is not None
    else:
        with pytest.raises(ValidationError, match="creation UTC day"):
            ConfigureHistory.model_validate(payload)


@pytest.mark.parametrize(
    "patch",
    [
        {"expectedRevision": 1, "initialCreatedFrom": None},
        {"expectedRevision": 1, "initialCreatedFrom": None, "rescan": True},
        {"populationBoundSource": {**_CATALOG_SOURCE, "kind": "explicit"}},
        {"populationBoundSource": {**_CATALOG_SOURCE, "extra": True}},
        {"populationBoundSource": {"kind": "repository_created_at"}},
    ],
)
def test_catalog_bound_source_rejected_without_a_bound_or_with_rescan(
    patch: dict[str, object],
) -> None:
    # Kills: admitting the catalog source on plain updates, rescans or an open shape.
    payload = {**_command_mapping(), "populationBoundSource": _CATALOG_SOURCE, **patch}
    with pytest.raises(ValidationError):
        ConfigureHistory.model_validate(payload)
