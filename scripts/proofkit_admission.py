from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from scripts.proofkit_cli import invoke_proofkit, resolve_proofkit_executable
from scripts.proofkit_common import (
    JsonObject,
    as_array,
    as_object,
    js_json_dumps,
    parse_json_object,
    read_json_object,
    write_json,
)
from scripts.proofkit_inputs import repo_profile_input
from scripts.proofkit_text_policy import text_policy_report

REPO_ROOT = Path(__file__).resolve().parent.parent
_REPORT_KEYS = frozenset(
    {
        "diagnostics",
        "nonClaims",
        "reportId",
        "reportKind",
        "ruleResults",
        "schemaVersion",
        "state",
        "summary",
    }
)
_REPORT_KINDS = {
    "repo-profile-admission": "proofkit.repo-profile-structural",
    "text-policy": "proofkit.text-policy",
}
_WITNESS_COMMAND_KEYS = frozenset(
    {
        "argv",
        "cachePolicy",
        "credentialClass",
        "cwd",
        "environment",
        "exitCodePolicy",
        "expectedArtifacts",
        "id",
        "networkPolicy",
        "parallelGroup",
        "schemaVersion",
        "timeoutMs",
    }
)


def admission_report(
    mode: str,
    *,
    repo_root: Path = REPO_ROOT,
    proofkit_executable: str | Path | None = None,
) -> JsonObject:
    if mode == "text-policy":
        return text_policy_report(
            repo_root,
            proofkit_executable=proofkit_executable,
        )
    executable = resolve_proofkit_executable(proofkit_executable)
    if mode == "verify":
        reports = [
            _run_with_input(
                executable,
                "repo-profile-admission",
                repo_profile_input(repo_root),
                repo_root,
            ),
            _run_with_path(
                executable,
                "witness-plan",
                "proofkit/witness-plan-input.json",
                repo_root,
            ),
        ]
    else:
        raise ValueError(f"unknown Proofkit admission mode: {mode}")
    return {
        "schemaVersion": 1,
        "reportId": f"ci-coordinator.proofkit-{mode}",
        "reportKind": f"ci-coordinator.proofkit-{mode}",
        "state": "passed",
        "summary": {
            "admissionCount": len(reports),
            "commands": [report["command"] for report in reports],
        },
        "reports": reports,
        "nonClaims": [
            (
                "This wrapper admits caller-owned Proofkit inputs and does not execute "
                "native witnesses."
            ),
            (
                "A passing report does not prove provider execution, merge safety, "
                "or deployment readiness."
            ),
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print(
            "usage: python -m scripts.proofkit_admission <text-policy|verify>",
            file=sys.stderr,
        )
        return 2
    try:
        write_json(admission_report(arguments[0]))
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


def _run_with_input(
    executable: str,
    command: str,
    input_value: Mapping[str, object],
    repo_root: Path,
) -> JsonObject:
    result = invoke_proofkit(
        executable,
        command,
        ("--input", "-"),
        cwd=repo_root,
        input_text=js_json_dumps(input_value),
    )
    return _admitted_report(command, result.returncode, result.stdout, result.stderr)


def _run_with_path(
    executable: str,
    command: str,
    input_path: str,
    repo_root: Path,
) -> JsonObject:
    result = invoke_proofkit(
        executable,
        command,
        ("--input", input_path),
        cwd=repo_root,
    )
    return _admitted_witness_plan(
        command,
        result.returncode,
        result.stdout,
        result.stderr,
        read_json_object(repo_root / input_path),
    )


def _admitted_report(
    command: str,
    status: int,
    stdout: str,
    stderr: str,
) -> JsonObject:
    if status != 0:
        diagnostic = stderr.strip() or stdout.strip() or f"status {status}"
        raise RuntimeError(f"Proofkit {command} failed: {diagnostic}")
    report = parse_json_object(stdout, command)
    _assert_exact_keys(report, _REPORT_KEYS, f"Proofkit {command} report")
    if type(report.get("schemaVersion")) is not int or report["schemaVersion"] != 1:
        raise RuntimeError(f"Proofkit {command} emitted an unsupported report schema")
    if report.get("reportKind") != _REPORT_KINDS[command]:
        raise RuntimeError(f"Proofkit {command} emitted an unexpected report kind")
    if not isinstance(report.get("reportId"), str) or not report["reportId"]:
        raise RuntimeError(f"Proofkit {command} emitted an invalid report id")
    if report.get("state") != "passed":
        raise RuntimeError(f"Proofkit {command} did not emit a passing report")
    summary = as_object(report.get("summary"), f"Proofkit {command} summary")
    as_array(report.get("diagnostics"), f"Proofkit {command} diagnostics")
    as_array(report.get("ruleResults"), f"Proofkit {command} rule results")
    as_array(report.get("nonClaims"), f"Proofkit {command} non-claims")
    return {
        "command": command,
        "state": "passed",
        "summary": summary,
    }


def _admitted_witness_plan(
    command: str,
    status: int,
    stdout: str,
    stderr: str,
    input_value: Mapping[str, object],
) -> JsonObject:
    if status != 0:
        diagnostic = stderr.strip() or stdout.strip() or f"status {status}"
        raise RuntimeError(f"Proofkit {command} failed: {diagnostic}")
    plan = parse_json_object(stdout, command)
    expected = _expected_witness_plan(input_value)
    if json.dumps(plan, sort_keys=True, allow_nan=False) != json.dumps(
        expected, sort_keys=True, allow_nan=False
    ):
        raise RuntimeError(
            "Proofkit witness-plan output does not match the exact v1 normalized contract"
        )
    return {
        "command": command,
        "state": "passed",
        "summary": {
            "commandCount": len(expected["commands"]),
            "outputContract": "proofkit.witness-plan.output.v1",
            "parallelGroupCount": len(expected["parallelGroups"]),
        },
    }


def _expected_witness_plan(input_value: Mapping[str, object]) -> JsonObject:
    _assert_exact_keys(
        input_value,
        frozenset({"commands", "schemaVersion", "vocabulary"}),
        "witness-plan input",
    )
    if type(input_value.get("schemaVersion")) is not int or input_value["schemaVersion"] != 1:
        raise ValueError("witness-plan input schemaVersion must be 1")
    raw_commands = as_array(input_value.get("commands"), "witness-plan input commands")
    if not raw_commands:
        raise ValueError("witness-plan input commands must be non-empty")
    commands: list[JsonObject] = []
    for index, raw_command in enumerate(raw_commands):
        item = as_object(raw_command, f"witness-plan input commands[{index}]")
        _assert_exact_keys(item, _WITNESS_COMMAND_KEYS, "witness command")
        if type(item.get("schemaVersion")) is not int or item["schemaVersion"] != 1:
            raise ValueError("witness command schemaVersion must be 1")
        if not isinstance(item.get("id"), str) or not item["id"]:
            raise ValueError("witness command id must be non-empty text")
        if not isinstance(item.get("parallelGroup"), str) or not item["parallelGroup"]:
            raise ValueError("witness command parallelGroup must be non-empty text")
        commands.append(item)
    commands.sort(key=lambda item: str(item["id"]))
    command_ids = [str(item["id"]) for item in commands]
    if len(command_ids) != len(set(command_ids)):
        raise ValueError("witness command ids must be unique")
    grouped: dict[str, list[str]] = {}
    for item in commands:
        grouped.setdefault(str(item["parallelGroup"]), []).append(str(item["id"]))
    return {
        "commands": commands,
        "parallelGroups": [
            {"commandIds": sorted(grouped[group]), "parallelGroup": group}
            for group in sorted(grouped)
        ],
    }


def _assert_exact_keys(value: Mapping[str, object], expected: frozenset[str], context: str) -> None:
    observed = set(value)
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        raise ValueError(f"{context} keys differ; missing={missing}, extra={extra}")


if __name__ == "__main__":
    raise SystemExit(main())
