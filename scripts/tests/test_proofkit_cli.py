from __future__ import annotations

import json
import sys
from copy import deepcopy
from importlib.resources import files
from pathlib import Path

import pytest

# isort: split
from scripts.proofkit_admission import _admitted_report, _admitted_witness_plan
from scripts.proofkit_cli import invoke_proofkit, resolve_proofkit_executable
from scripts.proofkit_common import parse_json_object, read_json_object

USED_PROOFKIT_COMMANDS = (
    "repo-profile-admission",
    "requirement-bindings",
    "requirement-source-admission",
    "requirement-source-transition",
    "selective-gate-plan",
    "text-policy",
    "witness-plan",
)


@pytest.mark.parametrize("command", USED_PROOFKIT_COMMANDS)
def test_installed_carrier_preserves_consumed_cli_contract(command: str) -> None:
    contract = json.loads(
        files("agentic_proofkit").joinpath("proofkit/cli-contract.v2.json").read_text()
    )
    assert contract["schemaVersion"] == 2
    assert contract["processContract"]["commandRouteGrammar"]["omittedRoutePolicy"] == (
        "command_id"
    )
    matches = [entry for entry in contract["commands"] if entry["command"] == command]
    assert len(matches) == 1
    entry = matches[0]
    assert entry.get("route", [command]) == [command]
    assert entry["input"] == "required"
    assert entry["stdin"] is True
    assert "--input" in entry["allowedFlags"]
    for direction in ("input", "output"):
        selected = entry[f"{direction}Contract"]
        assert selected["contractId"] == f"proofkit.{command}.{direction}.v1"
        assert selected["schemaVersion"] == 1
        assert selected["rootType"] == "object"
        assert selected["closed"] is True


@pytest.mark.parametrize("linked", [False, True])
def test_resolver_uses_only_the_active_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, linked: bool
) -> None:
    environment = tmp_path / "environment"
    entrypoint = environment / "bin" / "agentic-proofkit"
    entrypoint.parent.mkdir(parents=True)
    target = environment / "carrier" if linked else entrypoint
    target.write_bytes(b"owned executable\n")
    target.chmod(0o700)
    if linked:
        entrypoint.symlink_to(target)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    (unrelated / "agentic-proofkit").write_bytes(b"unrelated executable\n")
    (unrelated / "agentic-proofkit").chmod(0o700)
    monkeypatch.setattr(sys, "prefix", str(environment))
    monkeypatch.setenv("PATH", str(unrelated))

    assert resolve_proofkit_executable() == str(target)


@pytest.mark.parametrize("case", ["missing", "directory", "not-executable", "escaped-link"])
def test_resolver_refuses_an_unadmitted_entrypoint_without_path_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    environment = tmp_path / "environment"
    entrypoint = environment / "bin" / "agentic-proofkit"
    entrypoint.parent.mkdir(parents=True)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    fallback = unrelated / "agentic-proofkit"
    fallback.write_bytes(b"unrelated executable\n")
    fallback.chmod(0o700)
    if case == "directory":
        entrypoint.mkdir()
    elif case == "not-executable":
        entrypoint.write_bytes(b"not executable\n")
        entrypoint.chmod(0o600)
    elif case == "escaped-link":
        entrypoint.symlink_to(fallback)
    monkeypatch.setattr(sys, "prefix", str(environment))
    monkeypatch.setenv("PATH", str(unrelated))

    with pytest.raises(FileNotFoundError):
        resolve_proofkit_executable()


def test_resolver_preserves_explicit_in_process_test_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "absent"))
    injected = tmp_path / "controlled test executable"
    assert resolve_proofkit_executable(injected) == str(injected)


def test_invoke_proofkit_preserves_status_and_streams(tmp_path: Path) -> None:
    result = invoke_proofkit(
        sys.executable,
        "-c",
        ("import sys; print('out'); print('err', file=sys.stderr); raise SystemExit(4)",),
        cwd=tmp_path,
    )

    assert result.returncode == 4
    assert result.stdout == "out\n"
    assert result.stderr == "err\n"


def test_invoke_proofkit_enforces_output_bound_while_process_runs(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="output exceeded 1024 bytes"):
        invoke_proofkit(
            sys.executable,
            "-c",
            ("import os, time; os.write(1, b'x' * 2048); time.sleep(5)",),
            cwd=tmp_path,
            max_output_bytes=1024,
        )


def test_invoke_proofkit_enforces_timeout(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="timed out"):
        invoke_proofkit(
            sys.executable,
            "-c",
            ("import time; time.sleep(5)",),
            cwd=tmp_path,
            timeout_seconds=0.05,
        )


@pytest.mark.parametrize(
    ("command", "diagnostic"),
    (
        ("repo-profile-admission", "repo profile must be an object"),
        ("requirement-bindings", "schemaVersion must be 1"),
        ("requirement-source-admission", "schemaVersion must be 1"),
        ("requirement-source-transition", "schemaVersion must be 1"),
        ("selective-gate-plan", "schemaVersion must be 1"),
        ("text-policy", "schemaVersion must be 1"),
        ("witness-plan", "must include object vocabulary"),
    ),
)
def test_used_proofkit_command_rejects_malformed_input(command: str, diagnostic: str) -> None:
    result = invoke_proofkit(
        resolve_proofkit_executable(),
        command,
        ("--input", "-"),
        cwd=Path(__file__).resolve().parents[2],
        input_text="{}",
    )

    assert result.returncode != 0
    assert diagnostic in result.stderr


@pytest.mark.parametrize("command", USED_PROOFKIT_COMMANDS)
def test_used_proofkit_command_rejects_unsupported_flags(command: str) -> None:
    unsupported_flag = "--unsupported-ci-coordinator-flag"
    result = invoke_proofkit(
        resolve_proofkit_executable(),
        command,
        ("--input", "-", unsupported_flag),
        cwd=Path(__file__).resolve().parents[2],
        input_text="{}",
    )

    assert result.returncode != 0
    assert result.stderr.strip() == f"unsupported argument for {command}: {unsupported_flag}"


def test_requirement_source_transition_accepts_identity_transition() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    source = json.loads(
        (repo_root / "docs/specs/ci-coordinator-proofkit-adoption/requirements.v1.json").read_text(
            encoding="utf-8"
        )
    )
    result = invoke_proofkit(
        resolve_proofkit_executable(),
        "requirement-source-transition",
        ("--input", "-"),
        cwd=repo_root,
        input_text=json.dumps(
            {
                "next": source,
                "nonClaims": ["Identity transition proves CLI compatibility only."],
                "previous": source,
                "schemaVersion": 1,
                "transitionId": "ci-coordinator.proofkit-compatibility",
            }
        ),
    )

    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["reportKind"] == "proofkit.requirement-source-transition"
    assert report["schemaVersion"] == 1
    assert report["state"] == "passed"


@pytest.mark.parametrize(
    "permutation_path",
    (
        None,
        (),
        ("commands", 0),
        ("commands", 0, "environment"),
        ("commands", 0, "exitCodePolicy"),
        ("parallelGroups", 0),
    ),
)
def test_witness_plan_requires_exact_normalized_v1_output(
    permutation_path: tuple[str | int, ...] | None,
) -> None:
    input_value = _witness_plan_input()
    output = {
        "commands": input_value["commands"],
        "parallelGroups": [{"commandIds": ["quality.check"], "parallelGroup": "quality"}],
    }
    original = json.dumps(output)
    independent_output = json.loads(original)
    if permutation_path is not None:
        target = independent_output
        for part in permutation_path:
            target = target[part]
        entries = list(target.items())
        target.clear()
        target.update(reversed(entries))
        assert json.dumps(independent_output) != original
    assert json.dumps(output) == original

    admitted = _admitted_witness_plan(
        "witness-plan",
        0,
        json.dumps(independent_output),
        "",
        input_value,
    )

    assert admitted["state"] == "passed"
    assert admitted["summary"] == {
        "commandCount": 1,
        "outputContract": "proofkit.witness-plan.output.v1",
        "parallelGroupCount": 1,
    }


@pytest.mark.parametrize("output", ({}, {"commands": [], "parallelGroups": []}))
def test_witness_plan_rejects_successful_but_incomplete_json(output: object) -> None:
    with pytest.raises(RuntimeError, match="exact v1 normalized contract"):
        _admitted_witness_plan(
            "witness-plan",
            0,
            json.dumps(output),
            "",
            _witness_plan_input(),
        )


def _state_bearing_report() -> dict[str, object]:
    return {
        "diagnostics": [],
        "nonClaims": [],
        "reportId": "ci-coordinator.test",
        "reportKind": "proofkit.text-policy",
        "ruleResults": [],
        "schemaVersion": 1,
        "state": "passed",
        "summary": {},
    }


def test_state_bearing_report_accepts_exact_v1_root() -> None:
    admitted = _admitted_report(
        "text-policy",
        0,
        json.dumps(_state_bearing_report()),
        "",
    )

    assert admitted["state"] == "passed"


@pytest.mark.parametrize(
    ("mutation", "diagnostic"),
    (
        (lambda report: report.pop("diagnostics"), "keys differ"),
        (lambda report: report.update(unexpected=True), "keys differ"),
        (lambda report: report.update(schemaVersion=2), "unsupported report schema"),
        (
            lambda report: report.update(reportKind="proofkit.foreign"),
            "unexpected report kind",
        ),
        (lambda report: report.update(reportId=""), "invalid report id"),
        (lambda report: report.update(state="failed"), "did not emit a passing report"),
        (lambda report: report.update(summary=[]), "must be an object"),
        (lambda report: report.update(diagnostics={}), "must be an array"),
        (lambda report: report.update(ruleResults={}), "must be an array"),
        (lambda report: report.update(nonClaims={}), "must be an array"),
    ),
)
def test_state_bearing_report_rejects_each_root_contract_mutant(
    mutation: object,
    diagnostic: str,
) -> None:
    report = deepcopy(_state_bearing_report())
    assert callable(mutation)
    mutation(report)

    with pytest.raises((RuntimeError, TypeError, ValueError), match=diagnostic):
        _admitted_report("text-policy", 0, json.dumps(report), "")


@pytest.mark.parametrize(
    "mutation",
    (
        lambda output: output.update(unexpected=True),
        lambda output: output.pop("parallelGroups"),
        lambda output: output["commands"][0].update(timeoutMs=1001),
        lambda output: output["commands"][0].update(schemaVersion=True),
        lambda output: output["commands"][0].update(schemaVersion=1.0),
        lambda output: output["commands"][0].update(timeoutMs=1000.0),
        lambda output: output["commands"][0]["exitCodePolicy"].update(successCodes=[False]),
    ),
)
def test_witness_plan_rejects_each_exact_projection_mutant(mutation: object) -> None:
    input_value = _witness_plan_input()
    output = {
        "commands": deepcopy(input_value["commands"]),
        "parallelGroups": [{"commandIds": ["quality.check"], "parallelGroup": "quality"}],
    }
    assert callable(mutation)
    mutation(output)

    with pytest.raises(RuntimeError, match="exact v1 normalized contract"):
        _admitted_witness_plan(
            "witness-plan",
            0,
            json.dumps(output),
            "",
            input_value,
        )


def test_state_bearing_report_rejects_nonzero_status_before_parsing() -> None:
    report = {
        "diagnostics": [],
        "nonClaims": [],
        "reportId": "ci-coordinator.test",
        "reportKind": "proofkit.text-policy",
        "ruleResults": [],
        "schemaVersion": 1,
        "state": "passed",
        "summary": {},
    }

    with pytest.raises(RuntimeError, match="Proofkit text-policy failed: rejected"):
        _admitted_report("text-policy", 1, json.dumps(report), "rejected")


@pytest.mark.parametrize(
    "source",
    (
        '{"state":"failed","state":"passed"}',
        '{"outer":{"state":"failed","state":"passed"}}',
        '{"value":NaN}',
        '{"value":1e9999}',
        '{"value":"\\ud800"}',
    ),
)
def test_proofkit_json_admission_rejects_ambiguous_values(source: str) -> None:
    with pytest.raises(ValueError, match="did not emit JSON"):
        parse_json_object(source, "proofkit")


def test_proofkit_json_admission_accepts_valid_surrogate_pair() -> None:
    value = parse_json_object('{"value":"\\ud83d\\ude00"}', "proofkit")

    assert value["value"] == "\U0001f600"


def test_proofkit_file_admission_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "input.json"
    path.write_text('{"schemaVersion":0,"schemaVersion":1}', encoding="utf-8")

    with pytest.raises(ValueError, match="is not valid JSON"):
        read_json_object(path)


def _witness_plan_input() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "vocabulary": {},
        "commands": [
            {
                "argv": ["python", "-m", "scripts.quality"],
                "cachePolicy": "read-only",
                "credentialClass": "none",
                "cwd": ".",
                "environment": {
                    "allowlist": ["PATH"],
                    "classes": ["local-python"],
                    "inherit": "allowlist",
                },
                "exitCodePolicy": {"kind": "zero", "successCodes": [0]},
                "expectedArtifacts": [],
                "id": "quality.check",
                "networkPolicy": "none",
                "parallelGroup": "quality",
                "schemaVersion": 1,
                "timeoutMs": 1000,
            }
        ],
    }
