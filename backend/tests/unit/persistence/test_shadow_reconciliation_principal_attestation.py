from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest

from ci_coordinator.persistence.shadow_reconciliation_principal_attestation import (
    _column_facts_are_exact,
    _table_facts_are_exact,
)


@pytest.mark.parametrize(
    ("predicate", "expected_width"),
    ((_table_facts_are_exact, 3), (_column_facts_are_exact, 4)),
)
@pytest.mark.parametrize("width_delta", (-1, 1))
def test_principal_attestation_rejects_malformed_provider_rows(
    predicate: Callable[[tuple[Sequence[object], ...]], bool],
    expected_width: int,
    width_delta: int,
) -> None:
    row = (None,) * (expected_width + width_delta)

    assert not predicate((row,))
