from dataclasses import replace
from datetime import timedelta

import pytest
from pydantic import ValidationError

from ci_coordinator.ci_economics.discovery import ProviderObservationPage, ProviderRunDiscoveryPage
from ci_coordinator.ci_economics.history_checkpoint import HistoryCheckpoint, HistoryPendingPage
from ci_coordinator.ci_economics.history_checkpoint_payload import HistoryCheckpointPayload
from ci_coordinator.ci_economics.history_configuration import HistoryConfiguration
from ci_coordinator.ci_economics.history_cursor import HistoryCursor
from ci_coordinator.ci_economics.model import AttemptIdentity
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.config_control import RepositoryScope

from .archive_factories import ARCHIVE_TIME

SCOPE = RepositoryScope(101, 202)


def _checkpoint() -> HistoryCheckpoint:
    return HistoryCheckpoint(
        HistoryCursor.start(
            SCOPE,
            ARCHIVE_TIME,
            ARCHIVE_TIME + timedelta(days=1),
            cycle_started_at=ARCHIVE_TIME + timedelta(days=2),
        )
    )


def _page(checkpoint: HistoryCheckpoint, attempts: tuple[int, ...]) -> ProviderObservationPage:
    assert checkpoint.cursor is not None
    sources = tuple(
        ProviderRunCollectionSource(
            AttemptIdentity(SCOPE, index, attempt, "a" * 40),
            ARCHIVE_TIME,
            "2022-11-28",
            "b" * 64,
        )
        for index, attempt in enumerate(attempts, 1)
    )
    return ProviderObservationPage(
        ProviderRunDiscoveryPage(
            SCOPE,
            checkpoint.cursor.window,
            1,
            len(sources),
            sources,
            "exhausted",
        ),
        tuple(index + 400 for index in range(len(sources))),
    )


def _roundtrip(checkpoint: HistoryCheckpoint) -> HistoryCheckpoint:
    return HistoryCheckpointPayload.model_validate_json(
        HistoryCheckpointPayload.from_checkpoint(checkpoint).model_dump_json()
    ).to_checkpoint()


def test_pending_page_resumes_every_attempt_before_advancing_provider_cursor() -> None:
    initial = _checkpoint()
    pending, gap = initial.accept_page(_page(initial, (2, 1)))
    assert gap is None
    for run, attempt in ((1, 1), (1, 2), (2, 1)):
        pending = _roundtrip(pending)
        assert pending.cursor == initial.cursor
        assert pending.pending is not None
        assert pending.pending.attempt_cursor.workflow_run_id == run
        assert pending.pending.attempt_cursor.next_attempt == attempt
        pending = pending.complete_attempt(SCOPE, run, attempt)
    assert pending == replace(initial, complete=True)
    assert _roundtrip(pending) == pending


def test_page_receipt_cannot_hide_an_irreducible_provider_gap() -> None:
    initial = _checkpoint()
    assert initial.cursor is not None
    initial = HistoryCheckpoint(
        replace(
            initial.cursor,
            window=replace(
                initial.cursor.window, created_through=ARCHIVE_TIME + timedelta(seconds=1)
            ),
        )
    )
    observed = _page(initial, ())
    observed = replace(
        observed, page=replace(observed.page, provider_total=1001, termination="truncated")
    )
    successor, gap = initial.accept_page(observed)
    assert successor.cursor is not None
    assert successor.cursor.window.created_from == ARCHIVE_TIME + timedelta(seconds=1)
    assert gap == "provider_truncated"
    assert successor.pending is None


def test_subdivision_discards_page_attempts_without_claiming_them_imported() -> None:
    initial = _checkpoint()
    observed = _page(initial, (1,))
    saturated = replace(
        observed, page=replace(observed.page, provider_total=1001, termination="truncated")
    )
    successor, gap = initial.accept_page(saturated)
    assert gap is None and successor.pending is None
    assert successor.cursor is not None and initial.cursor is not None
    assert successor.cursor.window.created_through < initial.cursor.window.created_through
    with pytest.raises(ValueError):
        HistoryCheckpoint(initial.cursor, HistoryPendingPage(saturated))


@pytest.mark.parametrize("workflow_ids", [None, [400], [401]])
def test_skipping_requires_an_explicitly_unselected_workflow(
    workflow_ids: list[int] | None,
) -> None:
    initial = _checkpoint()
    pending, _ = initial.accept_page(_page(initial, (1,)))
    config = HistoryConfiguration.model_validate(
        {
            "enabled": True,
            "workflowIds": workflow_ids,
            "detailRetention": {"mode": "disabled"},
            "quota": {"attempts": 10, "jobs": 100, "gaps": 10, "canonicalBytes": 10000},
        }
    )
    if workflow_ids == [401]:
        assert pending.skip_unselected_run(config) == replace(initial, complete=True)
    else:
        with pytest.raises(ValueError):
            pending.skip_unselected_run(config)


@pytest.mark.parametrize(
    "field,value",
    [
        ("runIndex", True),
        ("runIndex", 1),
        ("nextAttempt", 0),
        ("nextAttempt", 3),
        ("repositoryId", 203),
        ("providerTotal", 0),
        ("workflowIds", [400]),
    ],
)
def test_pending_payload_cannot_substitute_a_source_or_resume_operand(
    field: str, value: object
) -> None:
    initial = _checkpoint()
    pending, _ = initial.accept_page(_page(initial, (2,)))
    raw = HistoryCheckpointPayload.from_checkpoint(pending).model_dump(mode="json")
    assert isinstance(raw["pending"], dict)
    raw["pending"][field] = value
    with pytest.raises((ValueError, ValidationError)):
        HistoryCheckpointPayload.model_validate(raw)


def test_pending_work_rejects_new_page_and_out_of_order_attempt() -> None:
    initial = _checkpoint()
    observed = _page(initial, (2,))
    pending, _ = initial.accept_page(observed)
    with pytest.raises(ValueError):
        pending.accept_page(observed)
    with pytest.raises(ValueError):
        pending.complete_attempt(SCOPE, 1, 2)
    with pytest.raises(ValueError):
        initial.complete_attempt(SCOPE, 1, 1)
    with pytest.raises(ValueError):
        replace(initial, complete=True).accept_page(observed)
