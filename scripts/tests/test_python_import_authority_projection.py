from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from scripts.python_import_boundary_policy import IMPORT_RULES, imported_modules, violating_rule_ids

_PURE_CORES = ("planning_core", "verification_core", "runner_capacity")
_OS_CAPABILITY_FILES = (
    "consumer_contract_lab/bootstrap.py",
    "consumer_contract_lab/git_source.py",
    "consumer_contract_lab/node_executable.py",
    "consumer_contract_lab/process.py",
    "api/http/operator_ui_bundle.py",
    "production_admission/file.py",
    "runtime/environment.py",
    "target_artifacts/workflow.py",
    "target_artifacts/resources/ci_measurement_reporter.py",
    "target_authority_evidence/file.py",
)


def _project(tmp_path: Path, relative_path: str, source: str) -> tuple[str, tuple[str, ...]]:
    source_root = tmp_path / "ci_coordinator"
    path = source_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return str(path), imported_modules(path, source_root)


def _rejected_by(path: str, authorities: tuple[str, ...]) -> set[str]:
    return {rule for authority in authorities for rule in violating_rule_ids(path, authority)}


def _assert_exact_rejection(
    projection: tuple[str, tuple[str, ...]], authority: str, rule: str
) -> None:
    path, authorities = projection
    assert authority in authorities
    assert violating_rule_ids(path, authority) == (rule,)
    assert _rejected_by(path, authorities) == {rule}


@pytest.mark.parametrize("core", _PURE_CORES)
@pytest.mark.parametrize(
    "module",
    ("time", "socket", "subprocess", "random", "secrets", "uuid", "pathlib", "asyncio", "io"),
)
def test_pure_core_external_authority_is_closed(tmp_path: Path, core: str, module: str) -> None:
    _assert_exact_rejection(
        _project(tmp_path, f"{core}/probe.py", f"import {module}\n"),
        module,
        f"python.import-boundary.{core.replace('_', '-')}",
    )


@pytest.mark.parametrize("core", _PURE_CORES)
@pytest.mark.parametrize("method", ("now", "utcnow", "today"))
@pytest.mark.parametrize("expression", ("moment.{method}", 'getattr(moment, "{method}")'))
def test_clock_read_rejection_preserves_the_datetime_data_import(
    tmp_path: Path, core: str, method: str, expression: str
) -> None:
    source = f"from datetime import datetime as moment\nread = {expression.format(method=method)}\n"
    _assert_exact_rejection(
        _project(tmp_path, f"{core}/probe.py", source),
        f"datetime.datetime.{method}",
        f"python.import-boundary.{core.replace('_', '-')}",
    )


@pytest.mark.parametrize("core", _PURE_CORES)
@pytest.mark.parametrize("authority", ("clock", "SystemClock", "SystemMonotonicClock"))
@pytest.mark.parametrize(
    "source",
    (
        "from ci_coordinator.kernel import {authority} as clock_source\n",
        "import ci_coordinator as package\nclock_source = package.kernel.{authority}\n",
        "from ci_coordinator import kernel as values\n"
        'clock_source = getattr(values, "{authority}")\n',
        "import ci_coordinator.kernel\n"
        'clock_source = getattr(getattr(ci_coordinator, "kernel"), "{authority}")\n',
        "import ci_coordinator.kernel\n"
        'clock_source = getattr(ci_coordinator, "kernel").{authority}\n',
    ),
)
def test_pure_core_rejects_kernel_clock_authority(
    tmp_path: Path, core: str, authority: str, source: str
) -> None:
    _assert_exact_rejection(
        _project(tmp_path, f"{core}/probe.py", source.format(authority=authority)),
        f"ci_coordinator.kernel.{authority}",
        f"python.import-boundary.{core.replace('_', '-')}",
    )


@pytest.mark.parametrize("core", _PURE_CORES)
@pytest.mark.parametrize(
    ("source", "authority"),
    (
        (
            "import ci_coordinator\nvalue = ci_coordinator.persistence.models\n",
            "ci_coordinator.persistence.models",
        ),
        (
            "import ci_coordinator as package\nvalue = package.runtime.environment\n",
            "ci_coordinator.runtime.environment",
        ),
        (
            'import ci_coordinator as package\nvalue = getattr(package, "integrations")\n',
            "ci_coordinator.integrations",
        ),
        (
            "import ci_coordinator as package\n"
            "def read():\n    return package.persistence.models\n",
            "ci_coordinator.persistence.models",
        ),
        (
            "import ci_coordinator as package\n"
            "class Holder:\n    def read(self):\n        return package.persistence.models\n",
            "ci_coordinator.persistence.models",
        ),
    ),
)
def test_first_party_qualified_import_aliases_reach_the_layer_rule(
    tmp_path: Path, core: str, source: str, authority: str
) -> None:
    _assert_exact_rejection(
        _project(tmp_path, f"{core}/probe.py", source),
        authority,
        f"python.import-boundary.{core.replace('_', '-')}",
    )


@pytest.mark.parametrize(
    "source",
    (
        "import sys\nvalue = sys.__dict__['modules']\n",
        "import os\nvalue = os.__dict__['environ']\n",
        "def callback():\n    pass\nvalue = callback.__globals__\n",
        "value = ().__class__.__base__.__subclasses__()\n",
        'def callback():\n    pass\nvalue = getattr(callback, "__globals__")\n',
        'value = namespace["__" + "dict__"]\n',
    ),
)
def test_known_reflection_is_rejected_by_the_dynamic_loading_owner(
    tmp_path: Path, source: str
) -> None:
    _assert_exact_rejection(
        _project(tmp_path, "production_admission/file.py", source),
        "reserved:dynamic-import",
        "python.import-boundary.production-admission",
    )


@pytest.mark.parametrize("core", _PURE_CORES)
@pytest.mark.parametrize(
    "source",
    (
        "import dataclasses as data\nvalue = data.sys.modules\n",
        "from dataclasses import sys as runtime\nvalue = runtime.modules\n",
        'import dataclasses as data\nvalue = getattr(data.sys, "modules")\n',
        'import dataclasses as data\nvalue = getattr(getattr(data, "sys"), "modules")\n',
        'import dataclasses as data\nvalue = getattr(data, "sys").modules\n',
    ),
)
def test_nested_module_registry_is_projected(tmp_path: Path, core: str, source: str) -> None:
    _assert_exact_rejection(
        _project(tmp_path, f"{core}/probe.py", source),
        "sys.modules",
        f"python.import-boundary.{core.replace('_', '-')}",
    )


@pytest.mark.parametrize(
    ("source", "authority"),
    (
        ("import os as process\nvalue = process.environb[b'KEY']\n", "os.environb"),
        ("from os import getenvb as read\nvalue = read(b'KEY')\n", "os.getenvb"),
        ('import os\nvalue = getattr(os, "environb")\n', "os.environb"),
        ("import posix as process\nvalue = process.environ[b'KEY']\n", "posix.environ"),
        ("from nt import environ as environment\nvalue = environment\n", "nt.environ"),
        ("from posix import putenv\n", "posix.putenv"),
        ("from nt import unsetenv\n", "nt.unsetenv"),
    ),
)
def test_os_file_exception_does_not_grant_process_environment(
    tmp_path: Path, source: str, authority: str
) -> None:
    _assert_exact_rejection(
        _project(tmp_path, "production_admission/file.py", source),
        authority,
        "python.import-boundary.process-environment",
    )


@pytest.mark.parametrize("module", ("os", "posix", "nt"))
def test_platform_os_capabilities_are_rejected_outside_exact_files(
    tmp_path: Path, module: str
) -> None:
    _assert_exact_rejection(
        _project(tmp_path, "production_admission/sibling.py", f"import {module}\n"),
        module,
        "python.import-boundary.os-capability",
    )


@pytest.mark.parametrize("relative_path", _OS_CAPABILITY_FILES)
def test_exact_files_keep_their_os_capability(tmp_path: Path, relative_path: str) -> None:
    path, authorities = _project(tmp_path, relative_path, "import os\nvalue = os.fsync\n")
    assert "os" in authorities
    assert _rejected_by(path, authorities) == set()


@pytest.mark.parametrize(
    "authority",
    (
        "os.getenv",
        "os.getenvb",
        "os.putenv",
        "os.unsetenv",
        "nt.putenv",
        "nt.unsetenv",
        "posix.putenv",
        "posix.unsetenv",
    ),
)
def test_offline_tool_rejects_environment_getters_and_mutators(
    tmp_path: Path, authority: str
) -> None:
    module, member = authority.split(".")
    _assert_exact_rejection(
        _project(
            tmp_path, "consumer_contract_lab/node_executable.py", f"from {module} import {member}\n"
        ),
        authority,
        "python.import-boundary.consumer-tool-environment",
    )


def test_offline_tool_keeps_the_explicit_environment_snapshot(tmp_path: Path) -> None:
    path, authorities = _project(
        tmp_path,
        "consumer_contract_lab/node_executable.py",
        "import os\nsnapshot = dict(os.environ)\n",
    )
    assert "os.environ" in authorities
    assert _rejected_by(path, authorities) == set()


@pytest.mark.parametrize(
    "expression",
    (
        'getattr(getattr(data, "field", process.environb), "name")',
        'getattr(data, "field", process.environb).name',
    ),
)
def test_nested_getattr_retains_default_argument_authority(tmp_path: Path, expression: str) -> None:
    _assert_exact_rejection(
        _project(
            tmp_path,
            "production_admission/file.py",
            f"import dataclasses as data\nimport os as process\nvalue = {expression}\n",
        ),
        "os.environb",
        "python.import-boundary.process-environment",
    )


@pytest.mark.parametrize(
    "relative_path",
    ("runtime/environment.py", "target_artifacts/resources/ci_measurement_reporter.py"),
)
def test_existing_environment_owners_keep_byte_environment_access(
    tmp_path: Path, relative_path: str
) -> None:
    path, authorities = _project(tmp_path, relative_path, "from os import getenvb\n")
    assert _rejected_by(path, authorities) == set()


@pytest.mark.parametrize("core", _PURE_CORES)
@pytest.mark.parametrize(
    "source",
    (
        "from datetime import datetime, timedelta\n"
        "def deadline(now: datetime) -> datetime:\n    return now + timedelta(seconds=5)\n",
        "from dataclasses import dataclass, field, replace\n"
        "from typing import Literal, Final, cast\nfrom collections.abc import Mapping\n",
        "import math\nfrom heapq import heapify, heappop, heappush\nvalue = math.isfinite(1)\n",
        'import json\nimport re\nvalue = json.loads("{}")\n',
        "import ci_coordinator as package\nvalue = package.kernel.utf16_sort_key\n",
        "from ci_coordinator import kernel as values\nvalue = values.hash_object\n",
        "import ci_coordinator\n"
        'value = getattr(getattr(ci_coordinator, "kernel"), "hash_object")\n',
        "import ci_coordinator as package\n"
        "def read(package):\n    return package.persistence.models\n",
        "import ci_coordinator as package\n"
        'def read(package):\n    return getattr(getattr(package, "kernel"), "SystemClock")\n',
        "from datetime import datetime as moment\ndef read(moment):\n    return moment.now()\n",
        "class Record:\n    modules = ()\nvalue = Record().modules\n",
    ),
)
def test_pure_data_and_shadowed_names_remain_admitted(
    tmp_path: Path, core: str, source: str
) -> None:
    path, authorities = _project(tmp_path, f"{core}/probe.py", source)
    assert _rejected_by(path, authorities) == set()


@pytest.mark.parametrize("core", _PURE_CORES)
def test_current_pure_core_source_imports_remain_admitted(core: str) -> None:
    source_root = Path(__file__).resolve().parents[2] / "backend/src/ci_coordinator"
    files = tuple(sorted((source_root / core).rglob("*.py")))
    assert files
    for path in files:
        assert _rejected_by(str(path), imported_modules(path, source_root)) == set(), path


@pytest.mark.parametrize("module", ("httpx", "httpx2"))
def test_kernel_rejects_legacy_and_current_http_transport(tmp_path: Path, module: str) -> None:
    _assert_exact_rejection(
        _project(tmp_path, "kernel/probe.py", f"import {module} as transport\n"),
        module,
        "python.import-boundary.kernel",
    )


def test_http_denials_preserve_policy_parity_without_removing_legacy_guards() -> None:
    legacy_rules = tuple(rule for rule in IMPORT_RULES if "httpx" in rule.forbidden)
    assert legacy_rules
    for rule in legacy_rules:
        assert rule.violates("httpx")
        assert rule.violates("httpx2"), rule.rule_id


def test_current_http_library_keeps_the_integration_only_contract() -> None:
    root = Path(__file__).resolve().parents[2]
    config = tomllib.loads((root / "backend/pyproject.toml").read_text(encoding="utf-8"))
    import_linter = config["tool"]["importlinter"]
    contracts = [
        contract
        for contract in import_linter["contracts"]
        if contract["id"] == "python.import-boundary.http-client"
    ]
    assert len(contracts) == 1
    assert import_linter["include_external_packages"] is True
    assert import_linter["exclude_type_checking_imports"] is False
    assert contracts[0]["type"] == "protected"
    assert contracts[0]["protected_modules"] == ["httpx2"]
    assert contracts[0]["allowed_importers"] == ["ci_coordinator.integrations"]
    assert contracts[0]["as_packages"] is True
    assert contracts[0].get("ignore_imports", []) == []
    assert (
        violating_rule_ids(
            "/repo/backend/src/ci_coordinator/integrations/github/http_client.py", "httpx2"
        )
        == ()
    )
