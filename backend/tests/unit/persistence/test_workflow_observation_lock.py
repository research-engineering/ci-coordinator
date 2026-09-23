import asyncio
from typing import cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence.workflow_observation_lock import (
    lock_workflow_observation,
    try_lock_workflow_observations,
)


@pytest.mark.parametrize("delivery_id", [None, 1, "", "a" * 129, "bad\x00id", "\ud800"])
def test_observation_guard_rejects_unrepresentable_identity_before_sql(delivery_id: object) -> None:
    connection = AsyncMock(spec=AsyncConnection)
    with pytest.raises(ValueError):
        asyncio.run(lock_workflow_observation(connection, cast(str, delivery_id)))
    connection.execute.assert_not_called()


@pytest.mark.parametrize(
    "identities",
    [[], ("a", "a"), ("a", ""), tuple(str(index) for index in range(1_001))],
)
def test_observation_guard_rejects_invalid_batch_before_sql(identities: object) -> None:
    connection = AsyncMock(spec=AsyncConnection)
    with pytest.raises(ValueError):
        asyncio.run(try_lock_workflow_observations(connection, cast(tuple[str, ...], identities)))
    connection.execute.assert_not_called()
