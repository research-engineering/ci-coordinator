import asyncio
from typing import cast

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence._schema_activity import activity_events
from ci_coordinator.persistence.activity_write import delete_sessions_with_activity


def test_foreign_delete_statement_is_rejected_before_connection_use() -> None:
    with pytest.raises(ValueError, match="activity deletion requires the session table"):
        asyncio.run(
            delete_sessions_with_activity(
                cast(AsyncConnection, object()), delete(activity_events), "revoked"
            )
        )
