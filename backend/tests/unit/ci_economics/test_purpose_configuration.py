import json

import pytest
from pydantic import ValidationError

from ci_coordinator.ci_economics.analytics_configuration import PurposeSettingsSnapshot
from ci_coordinator.ci_economics.archive_analytics_models import PurposeEntry
from ci_economics.purpose_configuration_factories import purpose_command, purpose_query


@pytest.mark.parametrize(
    "changes",
    [
        {"generation": True},
        {"generation": 0},
        {"generation": 9007199254740992},
        {"expected_revision": -1},
        {"expected_revision": 9007199254740991},
        {"operation_id": ""},
        {"operation_id": "op/foreign"},
        {"extra": 1},
        {"actor": "\ud800"},
        {"actor": "a\x00b"},
    ],
)
def test_invalid_command_is_not_an_admitted_operation(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        purpose_command(**changes)


@pytest.mark.parametrize("name", ["", "x" * 513, "\U0001f680" * 513, "\udfff", "a\x00b"])
def test_invalid_job_names_fail_before_persistence(name: str) -> None:
    with pytest.raises(ValidationError):
        PurposeEntry(workflow_id=404, job_name=name, purposes=("lint",))


def test_scalar_bound_and_exact_non_normalized_names() -> None:
    name = "\U0001f680" * 512
    assert len(name.encode()) == 2048
    assert PurposeEntry(workflow_id=1, job_name=name, purposes=("test",)).job_name == name
    for name in ("Lint", "lint", "e\u0301", "\u00e9"):
        assert PurposeEntry(workflow_id=1, job_name=name, purposes=("test",)).job_name == name


def test_duplicate_keys_categories_and_cardinality_are_closed() -> None:
    entry = PurposeEntry(workflow_id=1, job_name="Lint", purposes=("lint",))
    for entries in (
        (entry, entry),
        tuple(entry.model_copy(update={"workflow_id": index + 1}) for index in range(65)),
    ):
        with pytest.raises(ValidationError):
            purpose_command(entries=entries)
    for purposes in ((), ("test", "test"), ("unknown",), ("mixed",)):
        with pytest.raises(ValidationError):
            PurposeEntry.model_validate(
                {"workflow_id": 1, "job_name": "Lint", "purposes": purposes}
            )
    assert (
        len(
            purpose_command(
                entries=tuple(
                    entry.model_copy(update={"workflow_id": index + 1}) for index in range(64)
                )
            ).entries
        )
        == 64
    )


def test_missing_empty_revision_and_json_round_trip_remain_distinct() -> None:
    missing = PurposeSettingsSnapshot(**purpose_query().model_dump(), revision=0, mapping=None)
    empty = purpose_command(entries=()).successor()
    assert missing.revision == 0 and empty.revision == 1
    assert empty.mapping is not None and empty.mapping.entries == ()
    assert (
        PurposeSettingsSnapshot.model_validate_json(json.dumps(empty.model_dump(mode="json")))
        == empty
    )
    with pytest.raises(ValidationError):
        PurposeSettingsSnapshot.model_validate(empty.model_copy(update={"generation": 2}))
    with pytest.raises(ValidationError):
        PurposeSettingsSnapshot.model_validate(missing.model_copy(update={"revision": 1}))


@pytest.mark.parametrize(
    "changes",
    [
        {"actor": "other"},
        {"repository_id": 203},
        {"generation": 2},
        {"expected_revision": 1},
        {"entries": ()},
    ],
)
def test_every_operation_operand_is_digest_bound(changes: dict[str, object]) -> None:
    assert purpose_command().command_digest != purpose_command(**changes).command_digest
