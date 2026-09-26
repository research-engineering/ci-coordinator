import logging

import pytest


@pytest.fixture(autouse=True)
def dedicated_runtime_event_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    # Model a dedicated process without pytest attaching foreign capture handlers.
    logger = logging.Logger("ci_coordinator.events")
    original = logging.getLogger

    def get_logger(name: str | None = None) -> logging.Logger:
        return logger if name == logger.name else original(name)

    monkeypatch.setattr(logging, "getLogger", get_logger)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
