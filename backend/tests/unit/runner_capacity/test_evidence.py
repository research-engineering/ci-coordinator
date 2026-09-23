from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ci_coordinator.runner_capacity import (
    CapacityClassSelector,
    ObservedSelfHostedRunner,
    RunnerSnapshot,
    VisibleRunnerGroup,
    project_runner_snapshot,
)

NOW = datetime(2026, 9, 5, tzinfo=UTC)


def _runner(
    runner_id: int,
    *labels: str,
    online: bool = True,
    busy: bool = False,
) -> ObservedSelfHostedRunner:
    return ObservedSelfHostedRunner(
        runner_id,
        online,
        busy,
        tuple(sorted(label.lower() for label in labels)),
    )


def _snapshot(
    selectors: tuple[CapacityClassSelector, ...],
    *,
    repository_runners: tuple[ObservedSelfHostedRunner, ...] = (),
    groups: tuple[VisibleRunnerGroup, ...] = (),
) -> RunnerSnapshot | None:
    return project_runner_snapshot(
        selectors=selectors,
        repository_runners=repository_runners,
        groups=groups,
        observed_at=NOW,
        freshness_ttl_seconds=15,
    )


def test_label_eligibility_is_conjunctive_normalized_and_free_only() -> None:
    snapshot = _snapshot(
        (CapacityClassSelector("linux", ("linux", "self-hosted", "x64"), None),),
        repository_runners=(
            _runner(1, "SELF-HOSTED", "Linux", "X64"),
            _runner(2, "self-hosted", "linux"),
            _runner(3, "self-hosted", "linux", "x64", busy=True),
            _runner(4, "self-hosted", "linux", "x64", online=False),
        ),
    )

    assert snapshot is not None
    assert snapshot.free_slots_for("linux") == 1


def test_group_selector_is_exact_and_restricted_group_is_unknown() -> None:
    groups = (
        VisibleRunnerGroup(1, "Build", False, (_runner(1, "linux"),)),
        VisibleRunnerGroup(2, "Restricted", True, (_runner(2, "linux"),)),
    )
    snapshot = _snapshot(
        (
            CapacityClassSelector("absent", (), "Missing"),
            CapacityClassSelector("build", ("linux",), "Build"),
            CapacityClassSelector("case-mismatch", (), "build"),
            CapacityClassSelector("restricted", (), "Restricted"),
        ),
        groups=groups,
    )

    assert snapshot is not None
    assert tuple(
        (capacity.capacity_class_id, capacity.free_slots) for capacity in snapshot.capacities
    ) == (("absent", 0), ("build", 1), ("case-mismatch", 0))


def test_duplicate_group_name_makes_group_selector_unknown() -> None:
    snapshot = _snapshot(
        (CapacityClassSelector("build", ("linux",), "Build"),),
        groups=(
            VisibleRunnerGroup(1, "Build", False, (_runner(1, "linux"),)),
            VisibleRunnerGroup(2, "Build", False, (_runner(2, "linux"),)),
        ),
    )

    assert snapshot is not None
    assert snapshot.capacities == ()


def test_label_selector_excludes_repository_evidence_owned_by_restricted_group() -> None:
    runner = _runner(1, "dev")
    snapshot = _snapshot(
        (CapacityClassSelector("dev", ("dev",), None),),
        repository_runners=(runner,),
        groups=(VisibleRunnerGroup(1, "Restricted", True, (runner,)),),
    )

    assert snapshot is not None
    assert snapshot.free_slots_for("dev") == 0


def test_every_intersecting_capacity_class_is_omitted() -> None:
    snapshot = _snapshot(
        (
            CapacityClassSelector("all-linux", ("linux",), None),
            CapacityClassSelector("dev-linux", ("dev", "linux"), None),
            CapacityClassSelector("windows", ("windows",), None),
        ),
        repository_runners=(
            _runner(1, "dev", "linux"),
            _runner(2, "windows"),
        ),
    )

    assert snapshot is not None
    assert tuple(capacity.capacity_class_id for capacity in snapshot.capacities) == ("windows",)


def test_transient_state_disagreement_is_combined_conservatively() -> None:
    repository_runner = _runner(1, "linux")
    group_runner = _runner(1, "linux", busy=True)

    snapshot = _snapshot(
        (CapacityClassSelector("linux", ("linux",), None),),
        repository_runners=(repository_runner,),
        groups=(VisibleRunnerGroup(1, "One", False, (group_runner,)),),
    )

    assert snapshot is not None
    assert snapshot.free_slots_for("linux") == 0


@pytest.mark.parametrize("conflict", ["labels", "membership"])
def test_conflicting_provider_identity_rejects_the_snapshot(conflict: str) -> None:
    repository_runner = _runner(1, "linux")
    group_runner = _runner(1, "windows" if conflict == "labels" else "linux")
    groups = [VisibleRunnerGroup(1, "One", False, (group_runner,))]
    if conflict == "membership":
        groups.append(VisibleRunnerGroup(2, "Two", False, (group_runner,)))

    assert (
        _snapshot(
            (CapacityClassSelector("linux", ("linux",), None),),
            repository_runners=(repository_runner,),
            groups=tuple(groups),
        )
        is None
    )
