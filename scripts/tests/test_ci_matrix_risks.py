from __future__ import annotations

import json
from copy import deepcopy
from io import StringIO
from pathlib import Path

import pytest
from pydantic import ValidationError
from ruamel.yaml import YAML
from scripts.ci_matrix_risks import (
    CLASS_IDS,
    PROPERTY_IDS,
    SECTION_IDS,
    RiskCoverage,
    admit_assertion,
)
from scripts.proofkit_common import read_json_object

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "mutation", ["missing", "suppressed", "late", "non-GNU", "other-command", "budget"]
)
def test_native_watchdog_requires_prior_unsuppressed_GNU_admission(mutation: str) -> None:
    from scripts import ci_matrix_risk_execution as execution

    source = (ROOT / ".github/workflows/python-persistence.yml").read_bytes()
    execution.native_execution_owner(ROOT, source)
    yaml = YAML(typ="safe")
    workflow = yaml.load(source)
    steps = workflow["jobs"]["native-test-shards"]["steps"]
    admission = next(
        step for step in steps if step.get("run", "").strip() == execution.GNU_WATCHDOG_ADMISSION
    )
    command = next(step for step in steps if step.get("id") == "native_shard")
    if mutation == "missing":
        steps.remove(admission)
    elif mutation == "suppressed":
        admission["continue-on-error"] = True
    elif mutation == "late":
        steps.remove(admission)
        steps.append(admission)
    elif mutation == "non-GNU":
        admission["run"] = admission["run"].replace("GNU coreutils", "uutils coreutils")
    elif mutation == "other-command":
        command["run"] = command["run"].replace("/usr/bin/gnutimeout", "timeout")
    else:
        command["run"] = command["run"].replace("960s", "961s")
    output = StringIO()
    yaml.dump(workflow, output)
    with pytest.raises(ValueError, match=r"risk execution command|GNU watchdog admission"):
        execution.native_execution_owner(ROOT, output.getvalue().encode())


def _profile() -> dict[str, object]:
    return read_json_object(ROOT / "proofkit/ci-risk-coverage.v1.json")


def test_risk_projection_accounts_for_classes_and_properties_separately() -> None:
    profile = RiskCoverage.model_validate(_profile())
    assert {
        row.candidateId for row in profile.candidateRows if row.family == "tool-class"
    } == CLASS_IDS
    assert {
        row.candidateId for row in profile.candidateRows if row.family == "property"
    } == PROPERTY_IDS
    assert any(row.applicability != "covered" for row in profile.candidateRows)
    assert {row.section for row in profile.sectionDispositions} == SECTION_IDS


@pytest.mark.parametrize(
    "applicability,disposition",
    [
        ("partial", "covered"),
        ("deferred", "not-applicable"),
        ("unknown", "covered"),
        ("covered", "unresolved"),
        ("not-applicable", "confirmed-gap"),
    ],
)
def test_roadmap_disposition_cannot_upgrade_or_erase_the_evidence_state(
    applicability: str, disposition: str
) -> None:
    payload = RiskCoverage.model_validate(_profile()).model_dump()
    row = next(row for row in payload["candidateRows"] if row["applicability"] == applicability)
    row["disposition"] = disposition
    row["cost"] = {"closure": "none", "basis": "reclassified"}
    with pytest.raises(ValidationError, match="project the admitted evidence state"):
        RiskCoverage.model_validate(payload)


@pytest.mark.parametrize(
    "applicability,disposition,closure,message",
    [
        ("covered", "covered", "small", "no closure cost"),
        ("not-applicable", "not-applicable", "unknown", "no closure cost"),
        ("partial", "unresolved", "none", "no closure cost"),
        ("unknown", "confirmed-gap", "none", "no closure cost"),
        ("deferred", "confirmed-gap", "unknown", "sized closure cost"),
    ],
)
def test_open_risk_needs_a_closure_cost_and_a_confirmed_gap_is_sized(
    applicability: str, disposition: str, closure: str, message: str
) -> None:
    payload = RiskCoverage.model_validate(_profile()).model_dump()
    row = next(row for row in payload["candidateRows"] if row["applicability"] == applicability)
    row["disposition"] = disposition
    row["cost"] = {"closure": closure, "basis": "estimate"}
    with pytest.raises(ValidationError, match=message):
        RiskCoverage.model_validate(payload)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "foreign", "covered"])
def test_each_unnumbered_blueprint_section_keeps_one_disposition(mutation: str) -> None:
    payload = RiskCoverage.model_validate(_profile()).model_dump()
    sections = payload["sectionDispositions"]
    if mutation == "missing":
        sections.pop()
    elif mutation == "duplicate":
        sections[-1] = deepcopy(sections[0])
    elif mutation == "foreign":
        sections[-1]["section"] = "9.4"
    else:
        sections[0]["disposition"] = "covered"
    with pytest.raises(ValidationError):
        RiskCoverage.model_validate(payload)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "foreign", "family"])
def test_unknown_or_disappearing_blueprint_candidate_cannot_shrink_the_denominator(
    mutation: str,
) -> None:
    payload = RiskCoverage.model_validate(_profile()).model_dump()
    rows = payload["candidateRows"]
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows[-1] = deepcopy(rows[0])
    elif mutation == "foreign":
        rows[-1]["candidateId"] = "UNKNOWN01"
    else:
        rows[0]["family"] = "property"
    with pytest.raises(ValidationError):
        RiskCoverage.model_validate(payload)


def test_covered_candidate_requires_an_assertion_not_a_tool_name() -> None:
    payload = RiskCoverage.model_validate(_profile()).model_dump()
    payload["candidateRows"][0]["applicability"] = "covered"
    payload["candidateRows"][0]["bindings"] = []
    with pytest.raises(ValidationError, match="existing requirement assertion"):
        RiskCoverage.model_validate(payload)


@pytest.mark.parametrize(
    "source,selector",
    [
        ("def test_removed():\n    assert True\n", "test_expected"),
        ("def test_expected():\n    return 'assert result'\n", "test_expected"),
        ("def helper():\n    assert True\n", "helper"),
        (
            "def test_expected():\n    assert True\ndef test_expected():\n    assert False\n",
            "test_expected",
        ),
    ],
)
def test_python_assertion_selector_cannot_be_a_missing_duplicate_or_nonasserting_symbol(
    source: str,
    selector: str,
) -> None:
    with pytest.raises(ValueError):
        admit_assertion(source, "tests/example.py", selector)


def test_rejection_oracle_and_positive_assertion_are_admitted() -> None:
    admit_assertion("def test_value():\n    assert value == expected\n", "test.py", "test_value")
    admit_assertion(
        "def test_rejection():\n    with pytest.raises(ValueError):\n        parse(value)\n",
        "test.py",
        "test_rejection",
    )


@pytest.mark.parametrize("source", ["changed text", "expected assertion expected assertion"])
def test_nonpython_selector_must_be_exact_and_unambiguous(source: str) -> None:
    with pytest.raises(ValueError, match="absent or ambiguous"):
        admit_assertion(source, "test.ts", "expected assertion")


@pytest.mark.parametrize("mutation", ["source", "route", "selector"])
def test_source_drift_and_invented_proof_relations_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    from scripts import ci_matrix_risks as risks

    assert risks.check(ROOT)["status"] == "admitted"
    payload = RiskCoverage.model_validate(_profile()).model_dump()
    binding = next(row for row in payload["candidateRows"] if row["bindings"])["bindings"][0]
    if mutation == "source":
        binding["sourceSha256"] = "0" * 64
    elif mutation == "route":
        binding["commandIds"] = ["invented.passing-check"]
    else:
        binding["assertion"] = "test_nonexistent_risk_assertion"
    original = risks._source

    def source(root: Path, path: str) -> bytes:
        if path == risks.PROFILE_PATH.as_posix():
            return json.dumps(payload).encode()
        return original(root, path)

    monkeypatch.setattr(risks, "_source", source)
    with pytest.raises(ValueError):
        risks.check(ROOT)


def test_workflow_with_the_right_event_cannot_replace_the_command_execution_owner() -> None:
    from scripts.ci_matrix_risk_execution import admit_execution_binding

    with pytest.raises(ValueError, match="unadmitted execution workflow"):
        admit_execution_binding(
            ".github/workflows/api-contract.yml",
            ["python.test"],
            "scripts/tests/test_proofkit_requirements.py",
            {"python.test": ("pytest",)},
        )


@pytest.mark.parametrize(
    "command,path",
    [
        ("python.test", "new-tool/test_not_collected.py"),
        ("python.persistence-test", "backend/tests/unit/test_fake_persistence.py"),
        ("frontend.browser", "frontend/tests/unit.test.ts"),
        ("frontend.quality", "frontend/tests/browser/page.spec.ts"),
        ("runtime026.test", "backend/tests/unit/test_not_in_runtime_profile.py"),
    ],
)
def test_assertion_must_belong_to_the_named_native_command_collection(
    command: str, path: str
) -> None:
    from scripts.ci_matrix_risk_execution import admit_execution_binding

    with pytest.raises(ValueError, match="outside"):
        admit_execution_binding(
            ".github/workflows/python-persistence.yml",
            [command],
            path,
            {command: ("pytest", "backend/tests/unit/test_other.py")},
        )


@pytest.mark.parametrize(
    "command",
    ["lint", "lint:promises", "lint:promises:contract", "lint:transport:contract"],
)
def test_frontend_admission_requires_the_real_scan_and_both_static_oracles(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    from scripts import ci_matrix_risk_execution as execution

    source = (ROOT / ".github/workflows/python-persistence.yml").read_bytes()
    execution.native_execution_owner(ROOT, source)
    package = read_json_object(ROOT / "frontend/package.json")
    scripts = package["scripts"]
    assert isinstance(scripts, dict)
    scripts[command] = "true"
    original = read_json_object

    def substituted(path: Path) -> dict[str, object]:
        return package if path == ROOT / "frontend/package.json" else original(path)

    monkeypatch.setattr(execution, "read_json_object", substituted)
    with pytest.raises(ValueError, match="frontend execution chain"):
        execution.native_execution_owner(ROOT, source)
