import json
import subprocess
import sys
from importlib import import_module
from pathlib import Path
from types import ModuleType

import pytest

from ci_coordinator.audit_replay import (
    AllAuditReplayFilter,
    AuditJsonResourceFailure,
    AuditReplayLedgerSummary,
    AuditReplayReport,
    ValidAuditReplayLedgerSummary,
    ValidAuditReplayReport,
)


def import_named_module(module_name: str) -> ModuleType:
    return import_module(module_name)


def test_python_package_namespaces_are_importable() -> None:
    module_names = [
        "ci_coordinator",
        "ci_coordinator.api",
        "ci_coordinator.api.http",
        "ci_coordinator.app",
        "ci_coordinator.audit_replay",
        "ci_coordinator.identity_admission",
        "ci_coordinator.kernel",
    ]

    for module_name in module_names:
        module = import_named_module(module_name)

        assert module.__name__ == module_name


@pytest.mark.parametrize(
    ("module_name", "loads_application"),
    [
        ("ci_coordinator.api.http", False),
        ("ci_coordinator.api.http.body_limits", False),
        ("ci_coordinator.api.http.app", True),
    ],
)
def test_http_import_closure_is_explicit(
    module_name: str, loads_application: bool, tmp_path: Path
) -> None:
    source = Path(__file__).resolve().parents[1] / "src"
    program = """
import importlib
import json
import sys
import time

sys.path.insert(0, sys.argv[1])
started = time.monotonic_ns()
module = importlib.import_module(sys.argv[2])
elapsed = time.monotonic_ns() - started
print(json.dumps({
    "module": module.__name__,
    "elapsed_ns": elapsed,
    "application": "ci_coordinator.api.http.app" in sys.modules,
    "routers": sorted(name for name in sys.modules
                      if name == "ci_coordinator.api.http.routers"
                      or name.startswith("ci_coordinator.api.http.routers.")),
}))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program, str(source), module_name],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    observation = json.loads(result.stdout)
    assert observation["module"] == module_name
    assert observation["application"] is loads_application, observation
    assert bool(observation["routers"]) is loads_application, observation


def test_audit_replay_facade_preserves_constructible_public_types() -> None:
    ledger = AuditReplayLedgerSummary(valid=True, total_events=0)
    report = AuditReplayReport(
        ok=True,
        status="valid",
        ledger=ledger,
        replay_filter=AllAuditReplayFilter(),
        events=(),
    )

    assert report.status == "valid"
    assert issubclass(ValidAuditReplayReport, AuditReplayReport)
    typed_report = ValidAuditReplayReport(
        ledger=ValidAuditReplayLedgerSummary(total_events=0, last_event_hash=None),
        replay_filter=AllAuditReplayFilter(),
        events=(),
    )
    assert report == typed_report
    assert hash(report) == hash(typed_report)
    assert (
        AuditJsonResourceFailure(
            code="json_max_nodes_exceeded",
            instance_pointer="/payload",
            limit=10_000,
            observed=10_001,
        ).code
        == "json_max_nodes_exceeded"
    )
