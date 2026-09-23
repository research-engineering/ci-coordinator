from __future__ import annotations

import ci_coordinator.persistence as persistence


def test_persistence_public_surface_does_not_export_raw_engine_construction() -> None:
    assert "create_postgres_engine" not in persistence.__all__
    assert not hasattr(persistence, "create_postgres_engine")
