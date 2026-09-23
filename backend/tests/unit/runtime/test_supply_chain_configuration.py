from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from ruamel.yaml import YAML
from scripts.workflow_lint import commands as workflow_lint_commands

_DEPENDENCY_REVIEW_ACTION = (
    "actions/dependency-review-action@a1d282b36b6f3519aa1f3fc636f609c47dddb294"
)
_ACTIONLINT_IMAGE = (
    "docker.io/rhysd/actionlint:1.7.12@"
    "sha256:b1934ee5f1c509618f2508e6eb47ee0d3520686341fec936f3b79331f9315667"
)
_EXPECTED_UPDATE_ROOTS = {
    ("docker", "/"),
    ("github-actions", "/"),
    ("npm", "/"),
    ("uv", "/backend"),
    ("uv", "/tooling/quality"),
    ("gomod", "/tooling/actionlint"),
}


def test_dependency_proposals_are_paused_without_repository_merge_authority() -> None:
    document = _yaml_mapping(_repo_path(".github/dependabot.yml"))
    updates = cast(list[object], document["updates"])
    admitted = [cast(dict[str, object], update) for update in updates]

    assert len(admitted) == len(_EXPECTED_UPDATE_ROOTS)
    assert {
        (cast(str, update["package-ecosystem"]), cast(str, update["directory"]))
        for update in admitted
    } == _EXPECTED_UPDATE_ROOTS
    python_updates = [update for update in admitted if update["package-ecosystem"] == "uv"]
    assert {key: value for key, value in python_updates[0].items() if key != "directory"} == {
        key: value for key, value in python_updates[1].items() if key != "directory"
    }
    assert all(update["open-pull-requests-limit"] == 0 for update in admitted)
    assert all(
        cast(dict[str, object], update["cooldown"])["default-days"] == 7 for update in admitted
    )
    assert all(
        cast(dict[str, object], update["schedule"])["interval"] == "weekly" for update in admitted
    )
    assert all(
        cast(dict[str, object], update["schedule"])["timezone"] == "Europe/Berlin"
        for update in admitted
    )
    assert all(
        all(
            cast(dict[str, object], group)["update-types"] == ["minor", "patch"]
            for group in cast(dict[str, object], update["groups"]).values()
        )
        for update in admitted
    )
    assert all("target-branch" not in update for update in admitted)
    expected_ignores = {
        "docker": [
            {"dependency-name": "node", "versions": [">=25"]},
            {"dependency-name": "python", "versions": [">=3.14"]},
        ],
        "npm": [{"dependency-name": "@types/node", "versions": [">=25"]}],
    }
    for ecosystem, ignores in expected_ignores.items():
        update = next(item for item in admitted if item["package-ecosystem"] == ecosystem)
        assert update["ignore"] == ignores

    workflow_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(_repo_path(".github/workflows").glob("*.yml"))
    )
    assert all(
        forbidden not in workflow_text
        for forbidden in (
            "contents: write",
            "enablePullRequestAutoMerge",
            "gh pr merge",
            "mergePullRequest",
            "pull-requests: write",
        )
    )


def test_provider_workflow_reviews_dependency_diffs_with_an_immutable_action() -> None:
    document = _yaml_mapping(_repo_path(".github/workflows/python-persistence.yml"))
    jobs = cast(dict[str, object], document["jobs"])
    quality = cast(dict[str, object], jobs["repository-quality"])
    steps = cast(list[object], quality["steps"])
    review = next(
        cast(dict[str, object], step)
        for step in steps
        if cast(dict[str, object], step).get("name") == "Review dependency changes"
    )

    assert review["uses"] == _DEPENDENCY_REVIEW_ACTION
    assert review["if"] == "github.event_name == 'pull_request'"
    assert cast(dict[str, object], review["with"])["fail-on-severity"] == "high"


def test_workflow_linters_are_exact_and_part_of_the_provider_quality_gate() -> None:
    dockerfile = _repo_path("Dockerfile.workflow-lint").read_text(encoding="utf-8")
    workflow = _yaml_mapping(_repo_path(".github/workflows/python-persistence.yml"))
    jobs = cast(dict[str, object], workflow["jobs"])
    quality = cast(dict[str, object], jobs["repository-quality"])
    steps = [cast(dict[str, object], step) for step in cast(list[object], quality["steps"])]
    commands = workflow_lint_commands(_repo_path("."))

    assert [line for line in dockerfile.splitlines() if line.startswith("FROM ")] == [
        "FROM docker.io/library/golang:1.27.1-alpine@"
        "sha256:4cb7ac979db5fcc41cae44b2227ba5ab8a51e8807f40d9ba4dee20a0ad960b5b AS build",
        f"FROM {_ACTIONLINT_IMAGE}",
    ]
    assert "go test -mod=readonly ./..." in dockerfile
    assert "COPY --from=build /actionlint /usr/local/bin/actionlint" in dockerfile
    assert commands[-1].argv == (
        str(_repo_path("backend/.venv/bin/zizmor")),
        "--offline",
        "--persona",
        "pedantic",
        ".github",
        "fixtures/native-target-repository/.github",
        "fixtures/target-repository/.github",
    )
    assert (
        next(step for step in steps if step.get("name") == "Lint GitHub workflow contracts")["run"]
        == "backend/.venv/bin/python -m scripts.workflow_lint"
    )


def test_standalone_shell_lint_inventory_has_no_unowned_source() -> None:
    from scripts.bounded_git import run_git
    from scripts.workflow_lint import SHELL_SOURCES, shell_source_paths

    root = _repo_path(".")
    tracked = run_git(root, ("ls-files", "-z")).stdout
    assert set(shell_source_paths(root, tracked.rstrip("\0").split("\0"))) == {
        ".github/actions/secret-scan/install.sh",
        ".github/actions/secret-scan/run.sh",
        ".devcontainer/post-create.sh",
        ".githooks/pre-push",
        "docker/development/secret-entrypoint.sh",
        "docker/runtime/assemble.sh",
        "docker/runtime/install.sh",
        "docker/runtime/security/build.sh",
    }
    assert SHELL_SOURCES == (
        (".github/actions/secret-scan/install.sh", "bash"),
        (".github/actions/secret-scan/run.sh", "bash"),
        (".devcontainer/post-create.sh", "bash"),
        (".githooks/pre-push", "sh"),
        ("docker/development/secret-entrypoint.sh", "sh"),
        ("docker/runtime/assemble.sh", "bash"),
        ("docker/runtime/install.sh", "sh"),
        ("docker/runtime/security/build.sh", "bash"),
    )


@pytest.mark.parametrize(
    ("dockerfile_path", "patch_copy"),
    [
        ("Dockerfile", "COPY patches ./patches"),
        ("frontend/Dockerfile.dev", "COPY --chown=node:node patches ./patches"),
    ],
)
def test_ui_build_copies_dependency_patches_before_frozen_install(
    dockerfile_path: str,
    patch_copy: str,
) -> None:
    dockerfile = _repo_path(dockerfile_path).read_text(encoding="utf-8")
    workspace = _yaml_mapping(_repo_path("pnpm-workspace.yaml"))
    patches = cast(dict[str, object], workspace["patchedDependencies"])
    frozen_install = "pnpm install --frozen-lockfile"

    assert patches == {"minimatch@5.1.9": "patches/minimatch@5.1.9.patch"}
    assert all(_repo_path(cast(str, path)).is_file() for path in patches.values())
    assert dockerfile.index(patch_copy) < dockerfile.index(frozen_install)


def test_operator_ui_build_can_use_only_provider_supplied_node_proxy_settings() -> None:
    dockerfile = _repo_path("Dockerfile").read_text(encoding="utf-8")
    operator_stage, runtime_stages = dockerfile.split(
        "FROM python:3.13.15-slim-trixie@",
        maxsplit=1,
    )

    assert "NODE_USE_ENV_PROXY=1" in operator_stage
    assert "NODE_USE_ENV_PROXY" not in runtime_stages
    assert "HTTP_PROXY" not in dockerfile
    assert "HTTPS_PROXY" not in dockerfile


def test_image_build_executes_the_build_identity_renderer_once() -> None:
    dockerfile = _repo_path("Dockerfile").read_text(encoding="utf-8")

    assert "python -m ci_coordinator.runtime_settings.build_identity" not in dockerfile
    assert "from ci_coordinator.runtime_settings.build_identity import main; main()" in dockerfile


def _yaml_mapping(path: Path) -> dict[str, object]:
    value = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    assert type(value) is dict
    return cast(dict[str, object], value)


def _repo_path(relative: str) -> Path:
    return Path(__file__).resolve().parents[4] / relative
