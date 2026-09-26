"""No-network server stand-in for the real packaged-main worker witness."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _imports() -> dict[str, object]:
    main_file = sys.modules["__main__"].__file__
    package_file = sys.modules["ci_coordinator"].__file__
    assert type(main_file) is str and type(package_file) is str
    return {
        "pid": os.getpid(),
        "main_name": sys.modules["__main__"].__name__,
        "main_file": str(Path(main_file).resolve()),
        "package_file": str(Path(package_file).resolve()),
        "fixture_file": str(Path(__file__).resolve()),
        "application": "ci_coordinator.runtime.application" in sys.modules,
        "composition": "ci_coordinator.runtime.composition" in sys.modules,
    }


def run(app: object, **options: object) -> None:
    import asyncio

    from anyio import CapacityLimiter, to_process

    async def observe() -> tuple[object, object]:
        limiter = CapacityLimiter(1)
        first = await to_process.run_sync(_imports, cancellable=True, limiter=limiter)
        second = await to_process.run_sync(_imports, cancellable=True, limiter=limiter)
        return first, second

    first, second = asyncio.run(observe())
    print(
        json.dumps(
            {
                "parent": _imports(),
                "children": [first, second],
                "app_type": [type(app).__module__, type(app).__name__],
                "options": options,
            },
            sort_keys=True,
        )
    )
