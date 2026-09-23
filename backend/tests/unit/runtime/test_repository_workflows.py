from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def test_repository_workflow_actions_have_one_immutable_revision() -> None:
    revisions_by_action: dict[str, set[str]] = {}
    for path in _repository_workflow_paths():
        workflow = _read_workflow(path)
        for job in _mapping(workflow["jobs"]).values():
            job_mapping = _mapping(job)
            references = [
                job_mapping.get("uses"),
                *(step.get("uses") for step in _sequence(job_mapping.get("steps", []))),
            ]
            for uses in references:
                if not isinstance(uses, str) or uses.startswith(("./", "$/")):
                    continue
                _record_action_revision(revisions_by_action, uses)

    assert {
        action: tuple(sorted(revisions))
        for action, revisions in revisions_by_action.items()
        if len(revisions) != 1
    } == {}


def test_action_revision_identity_is_repository_case_insensitive() -> None:
    revisions_by_action: dict[str, set[str]] = {}

    _record_action_revision(
        revisions_by_action,
        f"actions/checkout@{'a' * 40}",
    )
    _record_action_revision(
        revisions_by_action,
        f"Actions/Checkout@{'b' * 40}",
    )

    assert revisions_by_action == {
        "actions/checkout": {"a" * 40, "b" * 40},
    }


def _record_action_revision(
    revisions_by_action: dict[str, set[str]],
    uses: str,
) -> None:
    action, separator, revision = uses.rpartition("@")
    assert separator == "@"
    assert re.fullmatch(r"[0-9a-f]{40}", revision)
    revisions_by_action.setdefault(_action_identity(action), set()).add(revision)


def _action_identity(action: str) -> str:
    owner, repository, *subpath = action.split("/")
    return "/".join((owner.lower(), repository.lower(), *subpath))


def _repository_workflow_paths() -> tuple[Path, ...]:
    root_workflows = (_REPOSITORY_ROOT / ".github/workflows").glob("*")
    fixture_workflows = (_REPOSITORY_ROOT / "fixtures").rglob("*")
    return tuple(
        sorted(
            (
                path
                for path in (*root_workflows, *fixture_workflows)
                if path.is_file()
                and path.suffix in {".yaml", ".yml"}
                and path.parent.name == "workflows"
                and path.parent.parent.name == ".github"
            ),
            key=Path.as_posix,
        )
    )


def _read_workflow(path: Path) -> dict[str, Any]:
    parser = YAML(typ="safe")
    return _mapping(parser.load(path.read_text(encoding="utf-8")))


def _mapping(value: object) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


def _sequence(value: object) -> list[dict[str, Any]]:
    assert isinstance(value, list)
    assert all(isinstance(item, dict) for item in value)
    return value
