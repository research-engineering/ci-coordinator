from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

import ci_coordinator

_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "runtime_worker_imports"
_EAGER_IMPORT = (
    "from ci_coordinator.runtime.application import (\n"
    "    RuntimeCompositionRejection,\n"
    "    compose_runtime_application,\n"
    ")\n"
)


def _object(value: object) -> dict[str, object]:
    assert type(value) is dict and all(type(key) is str for key in value)
    return cast(dict[str, object], value)


def _run_main(subject: Path) -> dict[str, object]:
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "PYTHONPATH": os.pathsep.join((str(_FIXTURE), str(subject))),
        "PYTHONDONTWRITEBYTECODE": "1",
        "CI_COORDINATOR_RUNTIME_MODE": "disabled",
        "CI_COORDINATOR_BIND_HOST": "127.0.0.1",
        "CI_COORDINATOR_BIND_PORT": "8080",
        "CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS": "30",
    }
    completed = subprocess.run(
        [sys.executable, "-m", "ci_coordinator.runtime"],
        cwd=subject,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=45,
    )
    assert completed.stderr == ""
    return _object(json.loads(completed.stdout))


def _assert_real_main_and_worker(receipt: dict[str, object], subject: Path) -> None:
    parent = _object(receipt["parent"])
    children = receipt["children"]
    assert type(children) is list and len(children) == 2
    first, second = (_object(child) for child in children)
    assert first == second, "the second call must observe the same warm worker"
    for observed in (parent, first):
        assert type(observed["pid"]) is int and observed["pid"] > 0
        assert observed["main_file"] == str(subject / "ci_coordinator/runtime/__main__.py")
        assert observed["package_file"] == str(subject / "ci_coordinator/__init__.py")
        assert observed["fixture_file"] == str(_FIXTURE / "uvicorn.py")
    assert parent["pid"] != first["pid"]
    assert parent["main_name"] == "__main__"
    assert first["main_name"] == "__mp_main__"
    assert parent["application"] is True and parent["composition"] is True
    assert receipt["app_type"] == ["fastapi.applications", "FastAPI"]
    assert receipt["options"] == {
        "host": "127.0.0.1",
        "port": 8080,
        "access_log": False,
        "log_config": None,
        "proxy_headers": False,
        "forwarded_allow_ips": "",
        "limit_concurrency": 128,
        "timeout_graceful_shutdown": 15,
    }


def _assert_child_import_boundary(receipt: dict[str, object]) -> None:
    children = receipt["children"]
    assert type(children) is list and len(children) == 2
    for child in children:
        observed = _object(child)
        assert observed["application"] is False, "server application imported in worker main"
        assert observed["composition"] is False, "server composition imported in worker main"


def test_packaged_main_avoids_eager_imports_with_a_real_anyio_child_and_counterfactual(
    tmp_path: Path,
) -> None:
    # Exercise the subject's package layout, not a published-wheel or container claim.
    package = Path(ci_coordinator.__file__).resolve().parent
    assert package == (Path(__file__).resolve().parents[3] / "src/ci_coordinator").resolve()
    original_main = (package / "runtime/__main__.py").read_bytes()
    subject = (tmp_path / "subject").resolve()
    copied = subject / "ci_coordinator"
    shutil.copytree(package, copied, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    baseline = _run_main(subject)
    _assert_real_main_and_worker(baseline, subject)
    _assert_child_import_boundary(baseline)

    main = copied / "runtime/__main__.py"
    text = main.read_text(encoding="utf-8")
    anchor = "from __future__ import annotations\n"
    assert text.count(anchor) == 1
    main.write_text(text.replace(anchor, anchor + "\n" + _EAGER_IMPORT), encoding="utf-8")
    counterfactual = _run_main(subject)
    _assert_real_main_and_worker(counterfactual, subject)
    with pytest.raises(AssertionError, match="server application imported in worker main"):
        _assert_child_import_boundary(counterfactual)

    main.write_bytes(original_main)
    restored = _run_main(subject)
    _assert_real_main_and_worker(restored, subject)
    _assert_child_import_boundary(restored)
    assert (package / "runtime/__main__.py").read_bytes() == original_main
