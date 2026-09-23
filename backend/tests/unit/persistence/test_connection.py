from __future__ import annotations

import asyncio

from ci_coordinator.persistence.connection import create_postgres_engine


def test_postgres_engine_hides_sqlalchemy_bind_parameters() -> None:
    engine = create_postgres_engine("postgresql+psycopg://test:test@localhost:5432/test")
    try:
        assert engine.sync_engine.hide_parameters
    finally:
        asyncio.run(engine.dispose())
