from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from ci_coordinator.runtime import __main__ as runtime_main
from ci_coordinator.runtime import application as runtime_application
from ci_coordinator.runtime.environment import load_runtime_settings_from_environment
from ci_coordinator.runtime_settings import (
    DisabledRuntimeSettings,
    UnsupportedPythonRuntime,
)
from ci_coordinator.runtime_settings.contracts import RuntimeSettingsRejection


def test_environment_loading_delegates_to_pure_admission() -> None:
    result = load_runtime_settings_from_environment(
        {
            "CI_COORDINATOR_RUNTIME_MODE": "disabled",
            "CI_COORDINATOR_BIND_HOST": "127.0.0.1",
            "CI_COORDINATOR_BIND_PORT": "8080",
            "CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS": "30",
        }
    )

    assert isinstance(result, DisabledRuntimeSettings)
    assert result.mode == "disabled"


def test_executable_preserves_the_admission_failure_code(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        runtime_main,
        "load_runtime_settings_from_environment",
        lambda: RuntimeSettingsRejection("missing_required_setting", "CI_COORDINATOR_BIND_PORT"),
    )
    monkeypatch.setattr(
        runtime_application,
        "compose_runtime_application",
        lambda _: pytest.fail("composition must not run after settings rejection"),
    )

    assert runtime_main.main() == 2

    assert capsys.readouterr().err == (
        '{"code":"missing_required_setting","field":"CI_COORDINATOR_BIND_PORT"}\n'
    )


def test_executable_preserves_composition_rejection_before_server_start(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_main, "admit_python_runtime", lambda: None)
    monkeypatch.setattr(runtime_main, "load_runtime_settings_from_environment", object)
    monkeypatch.setattr(
        runtime_application,
        "compose_runtime_application",
        lambda _: runtime_application.RuntimeCompositionRejection(
            "runtime_dependencies_unavailable", ("runtime_configuration",)
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        SimpleNamespace(run=lambda *_args, **_kwargs: pytest.fail("server must not start")),
    )

    assert runtime_main.main() == 2
    assert capsys.readouterr().err == (
        '{"code":"runtime_dependencies_unavailable",'
        '"unavailableDependencies":["runtime_configuration"]}\n'
    )


def test_executable_rejects_an_unsupported_interpreter_before_loading_settings(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        runtime_main,
        "admit_python_runtime",
        lambda: UnsupportedPythonRuntime("PyPy", "3.13.15", "CPython", ("3.13.15",)),
    )
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        runtime_main,
        "load_runtime_settings_from_environment",
        lambda: pytest.fail("settings must not load on an unsupported interpreter"),
    )

    assert runtime_main.main() == 2

    assert capsys.readouterr().err == (
        '{"code":"unsupported_python_runtime","actualImplementation":"PyPy",'
        '"actualVersion":"3.13.15","actualBuildVariant":"gil-enabled",'
        '"supportedImplementation":"CPython","supportedVersions":["3.13.15"],'
        '"supportedBuildVariant":"gil-enabled"}\n'
    )


def test_executable_uses_one_hardened_bounded_server_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = object()
    calls: list[tuple[object, dict[str, object]]] = []

    def run(candidate: object, **options: object) -> None:
        calls.append((candidate, options))

    projection = SimpleNamespace(
        bind_host="127.0.0.1",
        bind_port=8080,
        shutdown_timeout_seconds=30,
    )
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_main, "admit_python_runtime", lambda: None)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_main, "load_runtime_settings_from_environment", object)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        runtime_application,
        "compose_runtime_application",
        lambda _: SimpleNamespace(app=app, settings=projection),
    )
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=run))

    assert runtime_main.main() == 0

    assert calls == [
        (
            app,
            {
                "host": "127.0.0.1",
                "port": 8080,
                "access_log": False,
                "proxy_headers": False,
                "forwarded_allow_ips": "",
                "limit_concurrency": runtime_main.UVICORN_MAX_CONCURRENT_REQUESTS,
                "timeout_graceful_shutdown": 15,
            },
        )
    ]
