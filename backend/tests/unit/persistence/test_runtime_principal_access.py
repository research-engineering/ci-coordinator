from __future__ import annotations

import pytest

from ci_coordinator.persistence.runtime_principal_access import (
    RuntimePrincipalAccessError,
    admit_runtime_role_name,
)


@pytest.mark.parametrize(
    "role",
    (
        "",
        "UPPERCASE",
        "1runtime",
        "runtime-role",
        "runtime role",
        'runtime";drop role postgres;--',
        "r" * 64,
    ),
)
def test_runtime_role_name_rejects_unbounded_or_quoted_identifiers(role: str) -> None:
    with pytest.raises(RuntimePrincipalAccessError) as rejection:
        admit_runtime_role_name(role)

    assert rejection.value.code == "runtime_role_invalid"


@pytest.mark.parametrize("role", ("r", "ci_coordinator_runtime", "r2_durable"))
def test_runtime_role_name_admits_bounded_portable_identifiers(role: str) -> None:
    assert admit_runtime_role_name(role) == role
