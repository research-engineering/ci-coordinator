"""Command line interface for target-repository artifact drift control."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from ci_coordinator.runtime_settings import admit_python_runtime
from ci_coordinator.target_artifacts.model import RenderedTargetArtifacts
from ci_coordinator.target_artifacts.renderer import (
    TargetArtifactsRenderError,
    render_target_artifacts,
)
from ci_coordinator.target_artifacts.reporter import render_measurement_reporter
from ci_coordinator.target_artifacts.requester import render_plan_requester
from ci_coordinator.target_artifacts.source_codec import (
    TargetArtifactsAdmissionError,
    parse_target_artifacts_source,
)
from ci_coordinator.target_artifacts.trust_root import (
    check_plan_trust_root,
    render_plan_trust_root,
    write_plan_trust_root,
)
from ci_coordinator.target_artifacts.workflow import (
    check_artifacts,
    check_exact_file,
    write_artifacts,
    write_exact_file,
)


def main(argv: Sequence[str] | None = None) -> int:
    if admit_python_runtime() is not None:
        return _reject("unsupported_python_runtime")
    arguments = _parser().parse_args(argv)
    if arguments.command in {"render-trust-root", "check-trust-root"}:
        return _run_trust_root(arguments)
    if arguments.command in {
        "render-requester",
        "check-requester",
        "render-reporter",
        "check-reporter",
    }:
        return _run_optional_artifact(arguments)
    return _run_target_artifacts(arguments)


def _run_target_artifacts(arguments: argparse.Namespace) -> int:
    source_path = Path(arguments.source)
    output_directory = Path(arguments.output_directory)
    try:
        source = parse_target_artifacts_source(source_path.read_bytes())
        rendered = render_target_artifacts(source)
        if arguments.command == "render":
            write_artifacts(output_directory, rendered)
            return _report("target_artifacts_rendered", files=_filenames(rendered))
        drift = check_artifacts(output_directory, rendered)
    except (
        OSError,
        TargetArtifactsAdmissionError,
        TargetArtifactsRenderError,
        TypeError,
        ValueError,
    ) as error:
        return _reject("target_artifacts_invalid", detail=type(error).__name__)
    if drift:
        return _reject("target_artifacts_drift", files=list(drift), exit_code=1)
    return _report("target_artifacts_current", files=_filenames(rendered))


def _run_trust_root(arguments: argparse.Namespace) -> int:
    output_path = Path(arguments.output)
    try:
        expected = render_plan_trust_root(
            key_id=arguments.key_id,
            public_key_pem=Path(arguments.public_key).read_bytes(),
        )
        if arguments.command == "render-trust-root":
            write_plan_trust_root(output_path, expected)
            return _report("plan_trust_root_rendered", file=str(output_path))
        current = check_plan_trust_root(output_path, expected)
    except (OSError, TypeError, ValueError) as error:
        return _reject("plan_trust_root_invalid", detail=type(error).__name__)
    if not current:
        return _reject("plan_trust_root_drift", file=str(output_path), exit_code=1)
    return _report("plan_trust_root_current", file=str(output_path))


def _run_optional_artifact(arguments: argparse.Namespace) -> int:
    output_path = Path(arguments.output)
    reporter = arguments.command in {"render-reporter", "check-reporter"}
    artifact = "measurement_reporter" if reporter else "plan_requester"
    try:
        expected = render_measurement_reporter() if reporter else render_plan_requester()
        if arguments.command in {"render-requester", "render-reporter"}:
            write_exact_file(output_path, expected)
            return _report(f"{artifact}_rendered", file=str(output_path))
        current = check_exact_file(output_path, expected)
    except (OSError, TypeError, ValueError) as error:
        return _reject(f"{artifact}_invalid", detail=type(error).__name__)
    if not current:
        return _reject(f"{artifact}_drift", file=str(output_path), exit_code=1)
    return _report(f"{artifact}_current", file=str(output_path))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ci-coordinator-target-artifacts")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("render", "check"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--source", required=True)
        subparser.add_argument("--output-directory", required=True)
    for command in ("render-trust-root", "check-trust-root"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--key-id", required=True)
        subparser.add_argument("--public-key", required=True)
        subparser.add_argument("--output", required=True)
    for command in ("render-requester", "check-requester", "render-reporter", "check-reporter"):
        subparsers.add_parser(command).add_argument("--output", required=True)
    return parser


def _filenames(rendered: RenderedTargetArtifacts) -> list[str]:
    return [filename for filename, _ in rendered.by_filename()]


def _report(code: str, **details: object) -> int:
    print(json.dumps({"code": code, **details}, separators=(",", ":"), sort_keys=True))
    return 0


def _reject(code: str, *, exit_code: int = 2, **details: object) -> int:
    print(
        json.dumps({"code": code, **details}, separators=(",", ":"), sort_keys=True),
        file=sys.stderr,
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
