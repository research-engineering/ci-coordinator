from __future__ import annotations

import asyncio

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.planning_core import ConservativeEpochCoverageComparator

from ._epoch_support import admitted_dynamic_epoch

SCOPE = RepositoryScope(100, 200)


def test_conservative_comparator_proves_equal_compiled_coverage() -> None:
    active = admitted_dynamic_epoch(pretty=False)
    target = admitted_dynamic_epoch(pretty=True)

    relation = asyncio.run(
        ConservativeEpochCoverageComparator(active, target).compare(
            scope=SCOPE,
            active_epoch_id=active.epoch_id,
            target_epoch_id=target.epoch_id,
        )
    )

    assert active.epoch_id != target.epoch_id
    assert relation == "equal"


def test_conservative_comparator_proves_only_unidirectional_strengthening() -> None:
    active = admitted_dynamic_epoch(default_depth="smoke")
    stronger = admitted_dynamic_epoch(
        default_depth="standard",
        full_depth="exhaustive",
        omit_allowed=False,
    )
    weaker = admitted_dynamic_epoch(default_depth="smoke", full_depth="targeted")

    stronger_relation = asyncio.run(
        ConservativeEpochCoverageComparator(active, stronger).compare(
            scope=SCOPE,
            active_epoch_id=active.epoch_id,
            target_epoch_id=stronger.epoch_id,
        )
    )
    weaker_relation = asyncio.run(
        ConservativeEpochCoverageComparator(active, weaker).compare(
            scope=SCOPE,
            active_epoch_id=active.epoch_id,
            target_epoch_id=weaker.epoch_id,
        )
    )

    assert stronger_relation == "greater"
    assert weaker_relation == "less"


def test_conservative_comparator_rejects_mixed_or_unmodeled_changes() -> None:
    active = admitted_dynamic_epoch(default_depth="smoke")
    mixed = admitted_dynamic_epoch(default_depth="standard", full_depth="standard")
    changed_context = admitted_dynamic_epoch(
        default_depth="smoke",
        fallback_timeout_seconds=61,
    )

    mixed_relation = asyncio.run(
        ConservativeEpochCoverageComparator(active, mixed).compare(
            scope=SCOPE,
            active_epoch_id=active.epoch_id,
            target_epoch_id=mixed.epoch_id,
        )
    )
    context_relation = asyncio.run(
        ConservativeEpochCoverageComparator(active, changed_context).compare(
            scope=SCOPE,
            active_epoch_id=active.epoch_id,
            target_epoch_id=changed_context.epoch_id,
        )
    )

    assert mixed_relation == "incomparable"
    assert context_relation == "incomparable"


def test_conservative_comparator_rejects_unbound_epoch_identity_as_unknown() -> None:
    active = admitted_dynamic_epoch()
    target = admitted_dynamic_epoch(
        default_depth="exhaustive",
        full_depth="exhaustive",
    )

    relation = asyncio.run(
        ConservativeEpochCoverageComparator(active, target).compare(
            scope=SCOPE,
            active_epoch_id="f" * 64,
            target_epoch_id=target.epoch_id,
        )
    )

    assert relation == "unknown"
