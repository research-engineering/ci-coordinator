from __future__ import annotations

from typing import cast

import pytest
from psycopg import Cursor

from ci_coordinator.persistence import runtime_principal_access
from ci_coordinator.persistence.runtime_principal_access import (
    DatabaseRow,
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


_TABLE_FACTS: tuple[DatabaseRow, ...] = (
    ("SELECT", True, False),
    ("INSERT", False, False),
    ("UPDATE", False, False),
    ("DELETE", False, False),
    ("TRUNCATE", False, False),
    ("REFERENCES", False, False),
    ("TRIGGER", False, False),
    ("MAINTAIN", False, False),
)
_COLUMN_FACTS: tuple[DatabaseRow, ...] = (
    ("SELECT", True, False),
    ("INSERT", False, False),
    ("UPDATE", False, False),
    ("REFERENCES", False, False),
)


class _MetadataCursor:
    def __init__(
        self,
        *,
        table_facts: tuple[DatabaseRow, ...] = _TABLE_FACTS,
        column_facts: tuple[DatabaseRow, ...] = _COLUMN_FACTS,
    ) -> None:
        self.results = (table_facts, column_facts)
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, statement: str, parameters: tuple[object, ...]) -> None:
        self.calls.append((statement, parameters))

    def fetchall(self) -> tuple[DatabaseRow, ...]:
        return self.results[len(self.calls) - 1]


def test_metadata_checker_requires_separate_effective_column_facts() -> None:
    cursor = _MetadataCursor()

    assert runtime_principal_access._migration_metadata_access_is_exact(
        cast(Cursor[DatabaseRow], cursor), "runtime_reader", "public.alembic_version"
    )
    assert len(cursor.calls) == 2
    assert "has_table_privilege" in cursor.calls[0][0]
    assert "has_any_column_privilege" in cursor.calls[1][0]
    assert cursor.calls[1][1] == (
        "runtime_reader",
        "public.alembic_version",
        "runtime_reader",
        "public.alembic_version",
        ["SELECT", "INSERT", "UPDATE", "REFERENCES"],
    )


@pytest.mark.parametrize(
    ("privilege", "granted", "grantable"),
    (
        ("INSERT", True, False),
        ("UPDATE", True, False),
        ("REFERENCES", True, False),
        ("SELECT", True, True),
        ("INSERT", False, True),
        ("UPDATE", False, True),
        ("REFERENCES", False, True),
        ("SELECT", None, False),
        ("SELECT", True, None),
    ),
)
def test_metadata_checker_rejects_excess_or_unknown_column_facts(
    privilege: str, granted: bool | None, grantable: bool | None
) -> None:
    cursor = _MetadataCursor(
        column_facts=tuple(
            (name, granted, grantable) if name == privilege else (name, allowed, delegated)
            for name, allowed, delegated in _COLUMN_FACTS
        )
    )

    assert not runtime_principal_access._migration_metadata_access_is_exact(
        cast(Cursor[DatabaseRow], cursor), "runtime_reader", "public.alembic_version"
    )
    assert len(cursor.calls) == 2


@pytest.mark.parametrize("column_facts", ((), _COLUMN_FACTS[:-1]))
def test_metadata_checker_rejects_incomplete_column_observation(
    column_facts: tuple[DatabaseRow, ...],
) -> None:
    cursor = _MetadataCursor(column_facts=column_facts)

    assert not runtime_principal_access._migration_metadata_access_is_exact(
        cast(Cursor[DatabaseRow], cursor), "runtime_reader", "public.alembic_version"
    )


@pytest.mark.parametrize(
    "table_facts",
    (
        (),
        _TABLE_FACTS[1:],
        (("SELECT", False, False), *_TABLE_FACTS[1:]),
        (("SELECT", True, True), *_TABLE_FACTS[1:]),
    ),
)
def test_metadata_checker_keeps_table_select_and_delegation_guards(
    table_facts: tuple[DatabaseRow, ...],
) -> None:
    cursor = _MetadataCursor(table_facts=table_facts)

    assert not runtime_principal_access._migration_metadata_access_is_exact(
        cast(Cursor[DatabaseRow], cursor), "runtime_reader", "public.alembic_version"
    )
    assert len(cursor.calls) == 1
