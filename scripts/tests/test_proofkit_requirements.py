from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from scripts import proofkit_requirements
from scripts.bounded_git import BoundedGitCommandError, BoundedGitError
from scripts.proofkit_requirements import (
    _parse_json_report,
    assert_required_tuple_relation_preserved,
    validate_required_binding_tuples,
)


def _bindings() -> list[dict[str, object]]:
    return [
        {
            "requirementId": "REQ-1",
            "witnessPath": "owner.py",
            "commandIds": ["test", "typecheck"],
        },
        {
            "requirementId": "REQ-2",
            "witnessPath": "owner.py",
            "commandIds": ["test"],
        },
    ]


def _owners() -> list[dict[str, object]]:
    return [
        {
            "requirementId": "REQ-1",
            "witnessPath": "owner.py",
            "requiredCommandIds": ["test", "typecheck"],
        }
    ]


def test_required_binding_tuple_validator_accepts_exact_closure() -> None:
    assert validate_required_binding_tuples(_bindings(), _owners()) == 2


def test_required_binding_tuple_validator_rejects_missing_exact_tuple() -> None:
    bindings = deepcopy(_bindings())
    bindings[0]["commandIds"] = ["test"]

    with pytest.raises(ValueError, match=r"REQ-1 \| owner\.py \| typecheck"):
        validate_required_binding_tuples(bindings, _owners())


@pytest.mark.parametrize(
    "mutation, message",
    [
        (
            lambda owners: owners.append(deepcopy(owners[0])),
            "owners must be unique and sorted",
        ),
        (
            lambda owners: owners[0].update(requiredCommandIds=["typecheck", "test"]),
            "command ids must be unique and sorted",
        ),
        (lambda owners: owners[0].update(extra=True), "must contain exactly"),
    ],
)
def test_required_binding_tuple_validator_rejects_noncanonical_contract(
    mutation: object, message: str
) -> None:
    owners = _owners()
    assert callable(mutation)
    mutation(owners)

    with pytest.raises(ValueError, match=message):
        validate_required_binding_tuples(_bindings(), owners)


def test_required_tuple_migration_preserves_relation_and_bounds_additions() -> None:
    current = deepcopy(_owners())
    current.append(
        {
            "requirementId": "REQ-2",
            "witnessPath": "added.py",
            "requiredCommandIds": ["test"],
        }
    )

    assert (
        assert_required_tuple_relation_preserved(
            _owners(), current, allowed_added_paths=["added.py"]
        )
        == 1
    )
    with pytest.raises(ValueError, match="introduced tuples for undeclared paths"):
        assert_required_tuple_relation_preserved(_owners(), current, allowed_added_paths=[])
    with pytest.raises(ValueError, match="removed legacy tuples"):
        assert_required_tuple_relation_preserved(current, _owners(), allowed_added_paths=[])


def test_optional_git_fallback_does_not_hide_execution_bound_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def reject_command(_root: Path, _args: object) -> str:
        raise BoundedGitCommandError("unknown ref")

    monkeypatch.setattr(proofkit_requirements, "_git", reject_command)
    assert proofkit_requirements._try_git(tmp_path, ("rev-parse", "missing")) is None

    def reject_execution(_root: Path, _args: object) -> str:
        raise BoundedGitError("output exceeded bound")

    monkeypatch.setattr(proofkit_requirements, "_git", reject_execution)
    with pytest.raises(BoundedGitError, match="output exceeded bound"):
        proofkit_requirements._try_git(tmp_path, ("rev-parse", "missing"))


@pytest.mark.parametrize(
    ("command", "report_kind"),
    (
        ("requirement-bindings", "proofkit.requirement-proof-bindings"),
        ("requirement-source-admission", "proofkit.requirement-source-admission"),
        ("requirement-source-transition", "proofkit.requirement-source-transition"),
    ),
)
def test_requirement_commands_require_their_exact_v1_report_contract(
    command: str, report_kind: str
) -> None:
    report = {
        "diagnostics": [],
        "nonClaims": [],
        "reportId": "ci-coordinator.test",
        "reportKind": report_kind,
        "ruleResults": [],
        "schemaVersion": 1,
        "state": "passed",
        "summary": {},
    }

    assert _parse_json_report(command, "test-input", json.dumps(report)) == report

    for mutation, diagnostic in (
        ({"schemaVersion": 2}, "schemaVersion must be 1"),
        ({"reportKind": "proofkit.foreign"}, "reportKind is invalid"),
        ({"unexpected": True}, "report keys differ"),
    ):
        invalid = {**report, **mutation}
        with pytest.raises(ValueError, match=diagnostic):
            _parse_json_report(command, "test-input", json.dumps(invalid))


@pytest.mark.parametrize(
    "command",
    (
        "requirement-bindings",
        "requirement-source-admission",
        "requirement-source-transition",
    ),
)
def test_requirement_commands_reject_duplicate_report_state(command: str) -> None:
    source = (
        '{"diagnostics":[],"nonClaims":[],"reportId":"ci-coordinator.test",'
        f'"reportKind":"{proofkit_requirements._PROOFKIT_REPORT_KINDS[command]}",'
        '"ruleResults":[],"schemaVersion":1,"state":"failed","state":"passed",'
        '"summary":{}}'
    )

    with pytest.raises(ValueError, match="duplicate key"):
        _parse_json_report(command, "test-input", source)
