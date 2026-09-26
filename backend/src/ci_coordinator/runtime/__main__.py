"""Executable CI Coordinator runtime."""

from __future__ import annotations

import json
import sys
from typing import Final

from ci_coordinator.runtime.environment import load_runtime_settings_from_environment
from ci_coordinator.runtime.shutdown_budget import partition_shutdown_budget
from ci_coordinator.runtime_settings import RuntimeSettingsRejection, admit_python_runtime

UVICORN_MAX_CONCURRENT_REQUESTS: Final = 128


def main() -> int:
    from ci_coordinator.runtime.application import (
        RuntimeCompositionRejection,
        compose_runtime_application,
    )

    python_rejection = admit_python_runtime()
    if python_rejection is not None:
        return _reject(
            "unsupported_python_runtime",
            actualImplementation=python_rejection.actual_implementation,
            actualVersion=python_rejection.actual_version,
            actualBuildVariant=python_rejection.actual_build_variant,
            supportedImplementation=python_rejection.supported_implementation,
            supportedVersions=python_rejection.supported_versions,
            supportedBuildVariant=python_rejection.supported_build_variant,
        )
    settings = load_runtime_settings_from_environment()
    if isinstance(settings, RuntimeSettingsRejection):
        return _reject(settings.code, field=settings.field_name)
    application = compose_runtime_application(settings)
    if isinstance(application, RuntimeCompositionRejection):
        return _reject(
            application.code,
            unavailableDependencies=application.unavailable_dependencies,
        )
    import uvicorn

    shutdown = partition_shutdown_budget(application.settings.shutdown_timeout_seconds)
    uvicorn.run(
        application.app,
        host=application.settings.bind_host,
        port=application.settings.bind_port,
        access_log=False,
        proxy_headers=False,
        forwarded_allow_ips="",
        limit_concurrency=UVICORN_MAX_CONCURRENT_REQUESTS,
        timeout_graceful_shutdown=shutdown.uvicorn_grace_seconds,
    )
    return 0


def _reject(code: str, **details: object) -> int:
    print(json.dumps({"code": code, **details}, separators=(",", ":")), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
