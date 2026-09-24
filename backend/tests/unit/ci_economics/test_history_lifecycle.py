from dataclasses import replace
from datetime import timedelta

import pytest

from ci_coordinator.ci_economics.discovery import ProviderObservationPage, ProviderRunDiscoveryPage
from ci_coordinator.ci_economics.history_configuration import (
    HistoryConfiguration,
    HistoryDatasetState,
    HistoryUsage,
)
from ci_coordinator.ci_economics.history_lifecycle import configure_history_state
from ci_coordinator.ci_economics.history_scan import acquire_history_claim
from ci_coordinator.ci_economics.model import AttemptIdentity
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER
from ci_economics.archive_factories import ARCHIVE_TIME, history_dataset, history_scan


@pytest.mark.parametrize("enabled", [False, True])
def test_initial_population_uses_exact_source_lower_bound_and_database_upper_bound(
    enabled: bool,
) -> None:
    dataset = history_dataset()
    config = HistoryConfiguration.model_validate(
        {**dataset.configuration.model_dump(), "enabled": enabled}
    )
    now = ARCHIVE_TIME + timedelta(microseconds=123456)
    created, scan = configure_history_state(
        dataset.scope,
        config,
        prior=None,
        scan=None,
        now=now,
        initial_created_from=ARCHIVE_TIME - timedelta(days=500),
    )
    assert created.state == ("active" if enabled else "paused")
    assert created.usage == HistoryUsage.empty()
    assert created.generation == created.configuration_revision == created.data_revision == 1
    assert scan.checkpoint.cursor.created_from == ARCHIVE_TIME - timedelta(days=500)
    assert scan.checkpoint.cursor.created_through == ARCHIVE_TIME
    assert scan.checkpoint.cursor.cycle_started_at == now
    assert scan.lease is None and not scan.checkpoint.complete


@pytest.mark.parametrize(
    "patch",
    [
        {"enabled": False},
        {"detailRetention": None},
        {"quota": {"attempts": 1, "jobs": 1, "gaps": 1, "canonicalBytes": 1}},
    ],
)
def test_non_population_edits_preserve_progress_and_occupied_usage_but_fence_old_worker(
    patch: dict[str, object],
) -> None:
    dataset = replace(
        history_dataset(), usage=HistoryUsage(attempts=2, jobs=5, gaps=0, canonicalBytes=500)
    )
    original = history_scan(dataset)
    acquired = acquire_history_claim(
        dataset, original, now=ARCHIVE_TIME, worker_id="a" * 64, token="b" * 64
    )
    assert acquired is not None
    leased, _ = acquired
    configuration = HistoryConfiguration.model_validate(
        {**dataset.configuration.model_dump(), **patch}
    )
    successor, scan = configure_history_state(
        dataset.scope,
        configuration,
        prior=dataset,
        scan=leased,
        now=ARCHIVE_TIME + timedelta(seconds=1),
    )
    assert successor.usage == dataset.usage and successor.data_revision == dataset.data_revision
    assert successor.generation == dataset.generation
    assert successor.configuration_revision == dataset.configuration_revision + 1
    assert scan.configuration_revision == successor.configuration_revision
    assert scan.checkpoint == original.checkpoint
    assert scan.lease is None and scan.revision == leased.revision + 1


@pytest.mark.parametrize("rescan,workflow_ids", [(True, None), (False, [10])])
def test_explicit_rescan_or_selector_change_restarts_without_erasing_statistics(
    rescan: bool,
    workflow_ids: list[int] | None,
) -> None:
    dataset = history_dataset()
    original = history_scan(dataset)
    configuration = HistoryConfiguration.model_validate(
        {**dataset.configuration.model_dump(), "workflowIds": workflow_ids}
    )
    now = ARCHIVE_TIME + timedelta(days=2)
    successor, scan = configure_history_state(
        dataset.scope,
        configuration,
        prior=dataset,
        scan=original,
        now=now,
        rescan=rescan,
    )
    assert successor.generation == dataset.generation and successor.usage == dataset.usage
    assert successor.data_revision == dataset.data_revision
    assert scan.checkpoint.cursor.created_from == original.checkpoint.cursor.created_from
    assert scan.checkpoint.cursor.created_through == now
    assert scan.checkpoint.cursor.page_number == 1
    assert scan.pages_seen == scan.attempts_seen == 0
    assert scan.checkpoint.pending is None and scan.lease is None


def test_earlier_bound_expansion_preserves_statistics_and_restarts_backfill() -> None:
    dataset = replace(
        history_dataset(), usage=HistoryUsage(attempts=2, jobs=5, gaps=0, canonicalBytes=500)
    )
    original = history_scan(dataset)
    earlier = original.checkpoint.cursor.created_from - timedelta(days=30)
    now = ARCHIVE_TIME + timedelta(days=2)

    successor, scan = configure_history_state(
        dataset.scope,
        dataset.configuration,
        prior=dataset,
        scan=original,
        now=now,
        expand_created_from=earlier,
    )

    assert successor.configuration == dataset.configuration
    assert successor.generation == dataset.generation
    assert successor.data_revision == dataset.data_revision
    assert successor.usage == dataset.usage
    assert successor.configuration_revision == dataset.configuration_revision + 1
    assert scan.revision == original.revision + 1 and scan.lease is None
    assert scan.checkpoint.cursor.created_from == earlier
    assert scan.checkpoint.cursor.created_through == now


@pytest.mark.parametrize("violation", ["equal", "later", "configuration", "rescan"])
def test_expansion_rejects_non_extension_or_compound_configuration(violation: str) -> None:
    dataset = history_dataset()
    original = history_scan(dataset)
    lower = original.checkpoint.cursor.created_from
    configuration = (
        HistoryConfiguration.model_validate(
            {**dataset.configuration.model_dump(), "enabled": False}
        )
        if violation == "configuration"
        else dataset.configuration
    )
    expanded = lower + timedelta(days=1) if violation == "later" else lower
    if violation in {"configuration", "rescan"}:
        expanded -= timedelta(days=1)

    with pytest.raises(ValueError, match="earlier bound and unchanged policy"):
        configure_history_state(
            dataset.scope,
            configuration,
            prior=dataset,
            scan=original,
            now=ARCHIVE_TIME + timedelta(days=2),
            expand_created_from=expanded,
            rescan=violation == "rescan",
        )


def test_expansion_cannot_discard_a_committed_pending_page() -> None:
    dataset = history_dataset()
    original = history_scan(dataset)
    cursor = original.checkpoint.cursor
    source = ProviderRunCollectionSource(
        AttemptIdentity(dataset.scope, 303, 1, "a" * 40),
        cursor.created_from,
        "2022-11-28",
        "b" * 64,
    )
    observed = ProviderObservationPage(
        ProviderRunDiscoveryPage(dataset.scope, cursor.window, 1, 1, (source,), "exhausted"),
        (404,),
    )
    checkpoint, gap = original.checkpoint.accept_page(observed)
    assert gap is None and checkpoint.pending is not None
    pending = replace(original, checkpoint=checkpoint)
    with pytest.raises(ValueError, match="pending attempt work"):
        configure_history_state(
            dataset.scope,
            dataset.configuration,
            prior=dataset,
            scan=pending,
            now=ARCHIVE_TIME + timedelta(days=2),
            expand_created_from=cursor.created_from - timedelta(days=1),
        )


@pytest.mark.parametrize("field", ["scope", "generation", "configuration_revision"])
def test_configuration_rejects_each_independent_foreign_scan_operand(field: str) -> None:
    dataset = history_dataset()
    scan = history_scan(dataset)
    scope = dataset.scope
    if field == "scope":
        scope = RepositoryScope(101, 999)
    elif field == "generation":
        scan = replace(scan, generation=2)
    else:
        scan = replace(scan, configuration_revision=2)
    with pytest.raises(ValueError):
        configure_history_state(
            scope,
            dataset.configuration,
            prior=dataset,
            scan=scan,
            now=ARCHIVE_TIME,
        )


@pytest.mark.parametrize("state", ["erasing", "erased"])
def test_ordinary_configuration_cannot_restore_a_fenced_dataset(state: HistoryDatasetState) -> None:
    original = history_dataset()
    paused = HistoryConfiguration.model_validate(
        {**original.configuration.model_dump(), "enabled": False}
    )
    dataset = replace(original, state=state, configuration=paused)
    with pytest.raises(ValueError):
        configure_history_state(
            dataset.scope,
            original.configuration,
            prior=dataset,
            scan=history_scan(dataset),
            now=ARCHIVE_TIME,
        )


def test_configuration_rejects_stale_time_missing_scan_and_replaced_population() -> None:
    dataset = history_dataset()
    for scan, now, lower in [
        (None, ARCHIVE_TIME, None),
        (history_scan(dataset), ARCHIVE_TIME - timedelta(seconds=1), None),
        (history_scan(dataset), ARCHIVE_TIME, ARCHIVE_TIME - timedelta(days=1)),
    ]:
        with pytest.raises((TypeError, ValueError)):
            configure_history_state(
                dataset.scope,
                dataset.configuration,
                prior=dataset,
                scan=scan,
                now=now,
                initial_created_from=lower,
            )


def test_configuration_cannot_overflow_its_revision_space() -> None:
    dataset = replace(history_dataset(), configuration_revision=MAX_SAFE_JSON_INTEGER)
    with pytest.raises(ValueError):
        configure_history_state(
            dataset.scope,
            dataset.configuration,
            prior=dataset,
            scan=history_scan(dataset),
            now=ARCHIVE_TIME,
        )
