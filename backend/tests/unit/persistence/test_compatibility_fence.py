from __future__ import annotations

import pytest

from ci_coordinator.persistence.compatibility_fence import _failure_for_sqlstate
from ci_coordinator.persistence.errors import (
    DatabaseCompatibilityError,
    DatabaseCompatibilityOutcomeUnknown,
    DatabaseCompatibilityTimeout,
    DatabaseCompatibilityTransactionTimeout,
    DatabaseQueryCancelled,
)


@pytest.mark.parametrize(
    ("sqlstate", "error_type"),
    (
        ("55P03", DatabaseCompatibilityTimeout),
        ("57014", DatabaseQueryCancelled),
        ("25P04", DatabaseCompatibilityTransactionTimeout),
        ("08006", DatabaseCompatibilityOutcomeUnknown),
        (None, DatabaseCompatibilityError),
    ),
)
def test_fence_sqlstate_mapping_preserves_the_profile_failure_algebra(
    sqlstate: str | None,
    error_type: type[DatabaseCompatibilityError],
) -> None:
    assert isinstance(_failure_for_sqlstate(sqlstate), error_type)
