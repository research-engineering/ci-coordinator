from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Never, cast

from scripts.dev_environment.compose import ComposeError, ComposeProject
from scripts.dev_environment.diagnostics import (
    Diagnostic,
    OutputFormat,
    Reason,
    write_diagnostic,
    write_json,
)
from scripts.dev_environment.environment import EnvironmentError, admit_dependencies
from scripts.dev_environment.identity import InstanceIdentity, derive_instance_identity
from scripts.dev_environment.watch_session import CancelResult, inspect_watch


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise ValueError("invalid doctor arguments")


def run(
    argv: Sequence[str],
    *,
    repo_root: Path,
    stdout: object,
    state_home: Path | None = None,
) -> int:
    parser = _Parser(prog="dev:doctor")
    parser.add_argument("--format", choices=("human", "json"), default="json")
    options = parser.parse_args(argv)
    output_format = cast(OutputFormat, options.format)
    checks: list[dict[str, object]] = []
    identity = derive_instance_identity(repo_root, state_home=state_home)
    tools = tomllib.loads((repo_root / "mise.toml").read_text())["tools"]
    python_version = ".".join(str(value) for value in sys.version_info[:3])
    _record(
        checks,
        "python",
        None
        if sys.implementation.name == "cpython" and python_version == tools["python"]
        else Reason.UNSUPPORTED_TOOL,
    )
    try:
        admit_dependencies(identity, "backend")
    except EnvironmentError as error:
        _record(checks, "backend_dependencies", error.reason)
    else:
        _record(checks, "backend_dependencies", None)
    project = ComposeProject(
        identity,
        {"CI_COORDINATOR_DEV_ROOT_DIGEST": identity.root_digest},
        provider_environment=os.environ,
    )
    try:
        project.provider_preflight()
    except ComposeError as error:
        _record(checks, "docker", error.reason)
    else:
        _record(checks, "docker", None)
    _record(checks, "instance", _instance_reason(identity))
    watch = inspect_watch(identity)
    watch_reason = {
        "absent": None,
        "active": Reason.OPERATION_BUSY,
        "blocked": Reason.WATCH_BLOCKED,
        "unavailable": Reason.INVALID_STATE,
    }[watch.phase]
    _record(checks, "watch", watch_reason)
    if watch.phase != "absent":
        checks[-1]["session"] = asdict(watch)
        checks[-1]["recovery"] = CancelResult("blocked", watch.reason, watch.nonce).recovery
    ready = all(item["state"] == "passed" for item in checks)
    payload = {
        "schemaVersion": 1,
        "state": "ready" if ready else "needs_attention",
        "checks": checks,
    }
    if output_format == "json":
        write_json(stdout, payload)
    elif hasattr(stdout, "write"):
        stdout.write(f"Development prerequisites: {payload['state']}\n")
        for item in checks:
            stdout.write(f"  {item['name']}: {item['state']}\n")
            diagnostic = item.get("diagnostic")
            if isinstance(diagnostic, dict):
                stdout.write(Diagnostic(Reason(str(diagnostic["reason"])), "doctor").human())
            if "session" in item:
                stdout.write(f"  Watch record: {item['session']}\n{item['recovery']}\n")
    else:
        raise TypeError("output must provide write")
    return 0 if ready else 2


def main(argv: Sequence[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    try:
        return run(arguments, repo_root=Path(__file__).resolve().parents[2], stdout=sys.stdout)
    except (OSError, ValueError, EnvironmentError):
        write_diagnostic(
            sys.stderr,
            Diagnostic(Reason.INVALID_STATE, "doctor"),
            "human" if "human" in arguments else "json",
        )
        return 2


def _instance_reason(identity: InstanceIdentity) -> Reason | None:
    if not identity.state_directory.exists():
        return Reason.MISSING_STATE
    if (
        importlib.util.find_spec("cryptography") is None
        or importlib.util.find_spec("ci_coordinator") is None
    ):
        return Reason.DEPENDENCIES_MISSING
    try:
        from scripts.dev_environment.secrets import ForeignInstanceStateError, admit_instance_state
    except ImportError:
        return Reason.DEPENDENCIES_MISSING
    try:
        admit_instance_state(identity)
    except ForeignInstanceStateError:
        return Reason.FOREIGN_STATE
    except (OSError, ValueError):
        return Reason.INVALID_STATE
    return None


def _record(checks: list[dict[str, object]], name: str, reason: Reason | None) -> None:
    row: dict[str, object] = {
        "name": name,
        "state": "passed" if reason is None else "unavailable",
    }
    if reason is not None:
        row["diagnostic"] = Diagnostic(reason, "doctor", "preflight").projection()
    checks.append(row)
