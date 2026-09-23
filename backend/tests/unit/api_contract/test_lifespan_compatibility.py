from __future__ import annotations

from asyncio import CancelledError
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import anyio
import pytest
from anyio.from_thread import start_blocking_portal
from fastapi import FastAPI
from schemathesis.python import asgi

from .harness import Harness
from .lifespan_compatibility import ClosingLifespan


def test_native_lifecycle_closes_both_ends_of_both_streams(harness: Harness) -> None:
    with harness.example():
        lifecycle = asgi._LIFESPANS[id(harness.app)]
    assert lifecycle.task is not None and lifecycle.task.done()
    assert id(harness.app) not in asgi._LIFESPANS
    _assert_streams_closed(lifecycle)


def _assert_streams_closed(lifecycle: asgi._Lifespan) -> None:
    for stream in (lifecycle.receive_stream, lifecycle.send_stream):
        with pytest.raises(anyio.ClosedResourceError):
            lifecycle.portal.call(stream.send, {"type": "unexpected"})
        with pytest.raises(anyio.ClosedResourceError):
            lifecycle.portal.call(stream.receive)


def test_startup_failure_is_preserved_and_registry_can_be_drained() -> None:
    failure = ValueError("controlled startup refusal")

    @asynccontextmanager
    async def broken_lifespan(_app: FastAPI) -> AsyncIterator[None]:
        raise failure
        yield

    app = FastAPI(lifespan=broken_lifespan)
    with (
        pytest.raises(ValueError, match="controlled startup refusal") as raised,
        asgi.get_client(cast(asgi.ASGIApp, app)),
    ):
        pytest.fail("startup succeeded")
    assert raised.value is failure
    lifecycle = asgi._LIFESPANS[id(app)]
    assert lifecycle.error is failure
    assert lifecycle.task is not None and lifecycle.task.done()
    _assert_streams_closed(lifecycle)
    asgi.shutdown_lifespans()
    assert id(app) not in asgi._LIFESPANS


def test_shutdown_failure_is_preserved_and_closes_all_streams() -> None:
    failure = ValueError("controlled shutdown refusal")

    @asynccontextmanager
    async def broken_lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        raise failure

    app = FastAPI(lifespan=broken_lifespan)
    with asgi.get_client(cast(asgi.ASGIApp, app)):
        lifecycle = asgi._LIFESPANS[id(app)]
    with pytest.raises(ValueError, match="controlled shutdown refusal") as raised:
        asgi.shutdown_lifespans()
    assert raised.value is failure
    assert id(app) not in asgi._LIFESPANS
    assert lifecycle.task is not None and lifecycle.task.done()
    _assert_streams_closed(lifecycle)
    asgi.shutdown_lifespans()


@pytest.mark.parametrize("transition", ["start", "stop"])
def test_cancelled_transition_preserves_cancellation_and_closes_streams(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, transition: str
) -> None:
    failure = CancelledError("controlled transition cancellation")

    def cancel_native_transition(_lifespan: asgi._Lifespan) -> None:
        raise failure

    monkeypatch.setattr(ClosingLifespan.__bases__[0], transition, cancel_native_transition)
    with start_blocking_portal() as portal:
        lifecycle = ClosingLifespan(cast(asgi.ASGIApp, harness.app), portal)
        with pytest.raises(CancelledError) as raised:
            if transition == "start":
                lifecycle.start()
            else:
                lifecycle.stop()
        assert raised.value is failure
        _assert_streams_closed(lifecycle)
