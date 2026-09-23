import pytest

from ci_coordinator.ci_economics.archive_statistics import (
    ArchivedAttemptHeader,
    ArchivedAttemptStatistics,
)
from ci_coordinator.ci_economics.history_read import (
    HistoryReadCursor,
    HistoryReadKey,
    HistoryReadQuery,
)
from ci_coordinator.ci_economics.history_read_cursor import HistoryCursorCodec, history_query_digest
from ci_coordinator.ci_economics.history_retention_commands import HistoryRetentionSelection
from ci_economics.archive_factories import ARCHIVE_TIME, archived_statistics


def read_query(**changes: object) -> HistoryReadQuery:
    return HistoryReadQuery.model_validate(
        {
            "installationId": 101,
            "repositoryId": 202,
            "generation": 1,
            "kind": "records",
            **changes,
        }
    )


@pytest.mark.parametrize(
    "field",
    [
        "installationId",
        "repositoryId",
        "generation",
        "limit",
        "workflowId",
        "workflowRunId",
        "runAttempt",
    ],
)
@pytest.mark.parametrize("value", [True, False, "1", 1.0, 0, -1, 9007199254740992])
def test_every_integer_rejects_coercion_and_out_of_domain(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        read_query(**{field: value})


@pytest.mark.parametrize(
    "changes",
    [
        {"limit": 51},
        {"kind": "jobs"},
        {"kind": "detail"},
        {"workflowRunId": 303},
        {"kind": "gaps", "workflowId": 404},
        {"kind": "gaps", "jobName": "Lint"},
        {"createdFrom": "2026-02-01T00:00:00Z", "createdThrough": "2026-01-01T00:00:00Z"},
        {"kind": "jobs", "workflowRunId": 303, "runAttempt": 1, "jobName": "a\x00b"},
    ],
)
def test_incompatible_filters_fail_closed(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        read_query(**changes)


@pytest.mark.parametrize(
    "changes,actor",
    [
        ({"installationId": 202, "repositoryId": 101}, "operator"),
        ({"repositoryId": 203}, "operator"),
        ({"generation": 2}, "operator"),
        ({"workflowId": 404}, "operator"),
        ({"limit": 1}, "operator"),
        ({}, "other"),
    ],
)
def test_cursor_authenticates_actor_scope_generation_and_filters(
    changes: dict[str, object], actor: str
) -> None:
    codec = HistoryCursorCodec(b"test-only-archive-cursor-key-value")
    query = read_query()
    cursor = HistoryReadCursor(
        queryDigest=history_query_digest(query, "operator"),
        configurationRevision=2,
        dataRevision=3,
        observedAt=ARCHIVE_TIME,
        key=HistoryReadKey(time=ARCHIVE_TIME, run=303, attempt=1),
    )
    token = codec.encode(cursor)
    assert codec.decode(token, query=query, actor="operator") == cursor
    with pytest.raises(ValueError):
        codec.decode(token, query=read_query(**changes), actor=actor)
    with pytest.raises(ValueError):
        codec.decode(token[:-1] + ("0" if token[-1] != "0" else "1"), query=query, actor="operator")
    with pytest.raises(ValueError):
        HistoryCursorCodec(b"different-test-only-key-value-123").decode(
            token, query=query, actor="operator"
        )


@pytest.mark.parametrize("token", ["", "x" * 4097, "a.b.c", "?.abc", "e30=.abc"])
def test_malformed_cursor_is_a_bounded_rejection(token: str) -> None:
    with pytest.raises(ValueError):
        HistoryCursorCodec(b"test-only-archive-cursor-key-value").decode(
            token, query=read_query(), actor="operator"
        )


@pytest.mark.parametrize(
    "population,total,count,valid",
    [
        ("complete", 1, 1, True),
        ("complete", 2, 1, False),
        ("partial", None, 1, True),
        ("partial", None, 0, False),
        ("partial", 2, 1, True),
        ("partial", 1, 1, False),
        ("unavailable", None, 0, True),
        ("unavailable", 1, 0, False),
        ("conflict", None, 1, True),
        ("conflict", 0, 1, False),
    ],
)
def test_header_count_and_full_population_have_the_same_owner_oracle(
    population: str, total: int | None, count: int, valid: bool
) -> None:
    original = archived_statistics().model_dump()
    data = {**original, "population": population, "providerJobTotal": total}
    data["jobs"] = data["jobs"] if count else ()
    header = ArchivedAttemptHeader.model_validate({k: v for k, v in data.items() if k != "jobs"})
    if valid:
        header.admit_job_count(count)
        assert len(ArchivedAttemptStatistics.model_validate(data).jobs) == count
    else:
        with pytest.raises(ValueError):
            header.admit_job_count(count)
        with pytest.raises(ValueError):
            ArchivedAttemptStatistics.model_validate(data)


@pytest.mark.parametrize(
    "keys",
    [
        [],
        [{"workflowRunId": 303, "runAttempt": 1}] * 2,
        [{"workflowRunId": i + 1, "runAttempt": 1} for i in range(101)],
    ],
)
def test_retention_selection_is_finite_and_explicit(keys: list[dict[str, int]]) -> None:
    with pytest.raises(ValueError):
        HistoryRetentionSelection.model_validate(
            {
                "installationId": 101,
                "repositoryId": 202,
                "generation": 1,
                "configurationRevision": 1,
                "dataRevision": 1,
                "defaultRevision": 1,
                "importedThrough": ARCHIVE_TIME.isoformat(),
                "action": "erase_details",
                "keys": keys,
            }
        )


def test_a_keys_field_remains_data_during_nested_instance_revalidation() -> None:
    from ci_coordinator.ci_economics.history_retention_commands import (
        ApplyHistoryRetention,
        HistoryRetentionRequest,
        HistoryRetentionSelection,
    )

    selection = HistoryRetentionSelection.model_validate(
        {
            "installationId": 101,
            "repositoryId": 202,
            "generation": 1,
            "configurationRevision": 1,
            "dataRevision": 1,
            "defaultRevision": 1,
            "importedThrough": "2026-09-01T00:00:00Z",
            "action": "erase_details",
            "keys": [{"workflowRunId": 303, "runAttempt": 1}],
        }
    )
    assert HistoryRetentionSelection.model_validate(selection) == selection
    assert HistoryRetentionSelection.model_validate_json(selection.model_dump_json()) == selection
    request = HistoryRetentionRequest(
        selection=selection, reviewedDigest="a" * 64, operationId="retention"
    )
    command = ApplyHistoryRetention.from_request(request, actor="operator")
    assert command.selection == selection and command.actor == "operator"
    for keys in ((), (selection.keys[0], selection.keys[0])):
        with pytest.raises(ValueError):
            HistoryRetentionSelection.model_validate(selection.model_copy(update={"keys": keys}))
