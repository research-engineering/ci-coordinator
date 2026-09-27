import logging
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from ci_coordinator.consumer_contract_lab.process import LifetimeScope


@pytest.fixture(autouse=True)
def explicit_consumer_lab_lifetime() -> Iterator[None]:
    if sys.platform not in {"darwin", "linux"}:
        yield
        return
    from scripts.bounded_process import current_process_scope

    scope = current_process_scope()
    if scope is None:
        yield
        return
    from ci_coordinator.consumer_contract_lab import bootstrap, process

    with process.borrowed_lifetime(scope), bootstrap.borrowed_lifetime(scope):
        yield


@pytest.fixture
def managed_consumer_lifetime(tmp_path: Path) -> Iterator[tuple[LifetimeScope, int, int]]:
    from scripts.dev_environment.environment import borrow_managed_process
    from scripts.tests.test_dev_environment_dependencies import (
        dependency_identity_fixture,
        managed_entry_context,
    )

    from ci_coordinator.consumer_contract_lab import bootstrap, process

    directory = tmp_path / "lifetime"
    directory.mkdir()
    identity = dependency_identity_fixture(directory)
    with (
        managed_entry_context(identity) as (context, writer, descriptor),
        borrow_managed_process(context) as scope,
        process.borrowed_lifetime(scope),
        bootstrap.borrowed_lifetime(scope),
    ):
        yield scope, writer, descriptor


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
