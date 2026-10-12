from dataclasses import replace
from datetime import datetime
from typing import Literal

import pytest
from ci_economics.archive_factories import (
    ARCHIVE_TIME,
    history_dataset,
    history_discovery,
    history_scan,
)
from pydantic import ValidationError

from ci_coordinator.api.http.ci_history_contracts import (
    HistoryMutationResponse,
    HistoryPopulationBoundResponse,
    history_status_response,
)
from ci_coordinator.ci_economics.archive_retention import DetailRetentionPolicy
from ci_coordinator.ci_economics.history_administration import HistoryStatus
from ci_coordinator.ci_economics.history_configuration import HistoryConfiguration, HistoryDefaults
from ci_coordinator.ci_economics.history_payload import HistoryDatasetPayload
from ci_coordinator.ci_economics.history_population_bound import HistoryPopulationBound
from ci_coordinator.ci_economics.history_scan import acquire_history_claim


@pytest.mark.parametrize("override", [True, False])
def test_public_progress_is_allowlisted_and_effective_policy_has_exact_origin(
    override: bool,
) -> None:
    dataset = history_dataset()
    if not override:
        dataset = replace(
            dataset,
            configuration=HistoryConfiguration.model_validate(
                {
                    **dataset.configuration.model_dump(),
                    "detailRetention": None,
                }
            ),
        )
    acquired = acquire_history_claim(
        dataset, history_scan(dataset), now=ARCHIVE_TIME, worker_id="a" * 64, token="b" * 64
    )
    assert acquired is not None
    scan, _ = acquired
    status = HistoryStatus(
        dataset.scope,
        HistoryDefaults(7, DetailRetentionPolicy("forever"), ARCHIVE_TIME),
        dataset,
        scan,
        2,
        ARCHIVE_TIME,
        history_discovery(dataset),
    )
    response = history_status_response(status)
    body = response.to_wire_mapping()
    assert set(body) == {
        "schemaVersion",
        "installationId",
        "repositoryId",
        "defaults",
        "effectiveDetailRetention",
        "snapshot",
        "scan",
        "discovery",
        "populationBound",
        "pendingRechecks",
        "observedAt",
    }
    assert body["populationBound"] == {
        "createdFrom": "2019-01-01T00:00:00Z",
        "source": "unrecorded",
        "repositoryCreatedAt": None,
        "recordedAt": None,
    }
    assert body["effectiveDetailRetention"] == {
        "source": "repository_override" if override else "service_default",
        "revision": 1 if override else 7,
        "policy": {"mode": "days", "days": 365, "anchor": "first_successful_detail_import"}
        if override
        else {"mode": "forever"},
    }
    assert body["snapshot"] == dataset.canonical_mapping()
    assert response.scan is not None
    assert set(response.scan.to_wire_mapping()) == {
        "revision",
        "createdFrom",
        "createdThrough",
        "windowFrom",
        "windowThrough",
        "pageNumber",
        "cycleStartedAt",
        "nextAttemptAt",
        "leaseExpiresAt",
        "pagesSeen",
        "attemptsSeen",
        "lastOutcome",
        "traversalComplete",
    }
    assert scan.lease is not None
    assert response.scan.lease_expires_at == scan.lease.expires_at
    assert response.scan.created_from == scan.checkpoint.cursor.created_from
    assert response.scan.created_through == scan.checkpoint.cursor.created_through
    assert body["pendingRechecks"] == 2 and body["observedAt"] == "2020-01-01T00:00:00Z"
    assert scan.lease.worker_id not in response.model_dump_json()
    assert scan.lease.token not in response.model_dump_json()


def test_unconfigured_status_has_defaults_without_invented_progress() -> None:
    status = HistoryStatus(
        history_dataset().scope,
        HistoryDefaults(1, DetailRetentionPolicy.default(), ARCHIVE_TIME),
        None,
        None,
        0,
        ARCHIVE_TIME,
    )
    response = history_status_response(status)
    assert response.scan is None and response.snapshot is None
    assert response.population_bound is None
    assert response.effective_detail_retention is None and response.pending_rechecks == 0
    assert response.defaults.detail_retention.to_policy() == status.defaults.detail_retention


@pytest.mark.parametrize(
    "outcome",
    [
        "committed",
        "replayed",
        "revision_conflict",
        "operation_conflict",
        "capacity_reached",
        "dataset_fenced",
        "invalid_population",
    ],
)
@pytest.mark.parametrize("has_snapshot", [False, True])
def test_every_outcome_snapshot_pair_is_admitted_if_and_only_if_consistent(
    outcome: Literal[
        "committed",
        "replayed",
        "revision_conflict",
        "operation_conflict",
        "capacity_reached",
        "dataset_fenced",
        "invalid_population",
    ],
    has_snapshot: bool,
) -> None:
    snapshot = (
        HistoryDatasetPayload.model_validate(history_dataset().canonical_mapping())
        if has_snapshot
        else None
    )
    if has_snapshot == (outcome in {"committed", "replayed"}):
        response = HistoryMutationResponse(
            operation_id="operation-1", outcome=outcome, snapshot=snapshot
        )
        assert response.outcome == outcome and response.snapshot == snapshot
    else:
        with pytest.raises(ValidationError, match="contradicts"):
            HistoryMutationResponse(operation_id="operation-1", outcome=outcome, snapshot=snapshot)


@pytest.mark.parametrize(
    "recorded,expected",
    [
        (
            HistoryPopulationBound(
                history_dataset().scope,
                1,
                ARCHIVE_TIME.replace(year=2019),
                "repository_created_at",
                ARCHIVE_TIME.replace(year=2019, hour=12, minute=34, second=56),
                1,
                ARCHIVE_TIME,
            ),
            {
                "createdFrom": "2019-01-01T00:00:00Z",
                "source": "repository_created_at",
                "repositoryCreatedAt": "2019-01-01T12:34:56Z",
                "recordedAt": "2020-01-01T00:00:00Z",
            },
        ),
        (
            HistoryPopulationBound(
                history_dataset().scope,
                1,
                ARCHIVE_TIME.replace(year=2019),
                "explicit",
                None,
                1,
                ARCHIVE_TIME,
            ),
            {
                "createdFrom": "2019-01-01T00:00:00Z",
                "source": "explicit",
                "repositoryCreatedAt": None,
                "recordedAt": "2020-01-01T00:00:00Z",
            },
        ),
        (
            HistoryPopulationBound(
                history_dataset().scope,
                1,
                ARCHIVE_TIME.replace(year=2019, month=6),
                "explicit",
                None,
                1,
                ARCHIVE_TIME,
            ),
            {
                "createdFrom": "2019-01-01T00:00:00Z",
                "source": "unrecorded",
                "repositoryCreatedAt": None,
                "recordedAt": None,
            },
        ),
    ],
)
def test_history_status_population_bound_projection(
    recorded: HistoryPopulationBound, expected: dict[str, object]
) -> None:
    # Kills: wire alias drift, a stale (non-current) row shown as provenance, or a null branch.
    dataset = history_dataset()
    status = HistoryStatus(
        dataset.scope,
        HistoryDefaults(1, DetailRetentionPolicy.default(), ARCHIVE_TIME),
        dataset,
        history_scan(dataset),
        0,
        ARCHIVE_TIME,
        history_discovery(dataset),
        recorded,
    )
    assert history_status_response(status).to_wire_mapping()["populationBound"] == expected


@pytest.mark.parametrize(
    "source,created_at,recorded_at",
    [
        ("unrecorded", None, ARCHIVE_TIME),
        ("explicit", ARCHIVE_TIME, ARCHIVE_TIME),
        ("explicit", None, None),
        ("repository_created_at", None, ARCHIVE_TIME),
    ],
)
def test_population_bound_response_rejects_contradictory_evidence(
    source: Literal["explicit", "repository_created_at", "unrecorded"],
    created_at: datetime | None,
    recorded_at: datetime | None,
) -> None:
    with pytest.raises(ValidationError, match="contradicts its evidence"):
        HistoryPopulationBoundResponse(
            created_from=ARCHIVE_TIME,
            source=source,
            repository_created_at=created_at,
            recorded_at=recorded_at,
        )
