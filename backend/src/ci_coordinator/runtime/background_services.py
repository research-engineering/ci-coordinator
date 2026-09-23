import asyncio

from ci_coordinator.observability import BackgroundHealth, BackgroundHealthState
from ci_coordinator.runtime.resources import (
    RuntimeBackgroundHealthProbe,
    RuntimeBackgroundService,
)


class RuntimeBackgroundGroup:
    def __init__(
        self,
        *,
        primary: RuntimeBackgroundService,
        additional: tuple[RuntimeBackgroundService, ...],
    ) -> None:
        if type(additional) is not tuple or not 1 <= len(additional) <= 15:
            raise ValueError("additional background services require a bounded nonempty tuple")
        services = (primary, *additional)
        if len({id(service) for service in services}) != len(services):
            raise ValueError("background services must have distinct lifetime owners")
        self._primary = primary
        self._services = services
        self._attempted: list[RuntimeBackgroundService] = []
        self._started = False
        self._starting = False
        self._stopped = False

    async def start(self) -> None:
        if self._started or self._stopped:
            raise RuntimeError("background services cannot be restarted")
        self._started = True
        self._starting = True
        try:
            for service in self._services:
                self._attempted.append(service)
                await service.start()
        finally:
            self._starting = False

    async def stop(self) -> bool:
        if self._starting:
            raise RuntimeError("background startup must settle before drain")
        self._stopped = True
        results = await asyncio.gather(
            *(service.stop() for service in self._attempted), return_exceptions=True
        )
        return all(result is True for result in results)

    def background_health(self) -> BackgroundHealth:
        if isinstance(self._primary, RuntimeBackgroundHealthProbe):
            return self._primary.background_health()
        return BackgroundHealth(BackgroundHealthState.UNOBSERVABLE)
