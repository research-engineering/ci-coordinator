"""Toolchain falsifiers for the existing GitHub scripts-test owner."""

from __future__ import annotations

import io
import json
import re
import shutil
import tomllib
from pathlib import Path

import pytest
from scripts.dev_environment.toolchain import check_toolchain, run

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE = "docs/specs/ci-coordinator-runtime/python-runtime-profile.v1.json"
BUNDLED_PROFILE = (
    "backend/src/ci_coordinator/runtime_settings/resources/python-runtime-profile.v1.json"
)
NATIVE_INPUTS = (
    "mise.toml",
    "mise.lock",
    "package.json",
    "frontend/package.json",
    "backend/pyproject.toml",
    "backend/uv.lock",
    PROFILE,
    BUNDLED_PROFILE,
    "Dockerfile",
    "docker/development/backend.Dockerfile",
    "frontend/Dockerfile.dev",
    ".devcontainer/Dockerfile",
    ".devcontainer/devcontainer.json",
    ".github/actions/secret-scan/scanner.py",
)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    # Copy actual native owners, including every current workflow. Do not generate
    # a mutually consistent fixture from the checker relationship table.
    paths = [REPO_ROOT / path for path in NATIVE_INPUTS]
    paths.extend(sorted((REPO_ROOT / ".github/workflows").glob("*.yml")))
    paths.extend(sorted((REPO_ROOT / ".github/workflows").glob("*.yaml")))
    for source in paths:
        target = tmp_path / source.relative_to(REPO_ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return tmp_path


def _tools(root: Path) -> dict[str, str]:
    return dict(tomllib.loads((root / "mise.toml").read_text())["tools"])


def _replace(root: Path, path: str, old: str, new: str, *, count: int = -1) -> None:
    target = root / path
    source = target.read_text()
    assert old in source, f"native fixture declaration changed: {path}: {old}"
    target.write_text(source.replace(old, new, count), encoding="utf-8")


def _set_json(root: Path, path: str, key: str, value: object) -> None:
    target = root / path
    document = json.loads(target.read_text())
    document[key] = value
    target.write_text(json.dumps(document), encoding="utf-8")


def test_current_native_declarations_pass_without_certifying_artifacts() -> None:
    report = check_toolchain(REPO_ROOT)

    assert report.state == "passed", report.issues
    assert set(NATIVE_INPUTS) <= report.inputs.keys()
    assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in report.inputs.values())
    assert report.images
    assert {image.digest_identity for image in report.images} == {"not_verified"}
    assert report.projection()["evidence"] == "static_declarations_only"
    assert report.projection()["digestIdentity"] == "not_verified"


@pytest.mark.parametrize("reference", ["scratch:latest", "example/scratch", "scratchish"])
def test_only_the_builtin_empty_root_is_exempt_from_image_pins(
    repository: Path, reference: str
) -> None:
    _replace(repository, "Dockerfile", "FROM scratch", f"FROM {reference}")
    report = check_toolchain(repository)
    assert any(
        issue.path == "Dockerfile" and issue.reason == "unpinned_image" for issue in report.issues
    )


@pytest.mark.parametrize(
    ("path", "tool"),
    [
        ("mise.lock", "python"),
        ("mise.lock", "node"),
        ("mise.lock", "uv"),
        ("mise.lock", "pnpm"),
        ("mise.lock", "gitleaks"),
        (".github/actions/secret-scan/scanner.py", "gitleaks"),
        ("package.json", "node"),
        ("package.json", "pnpm"),
        ("frontend/package.json", "node"),
        ("frontend/package.json", "pnpm"),
        ("backend/pyproject.toml", "python"),
        ("backend/uv.lock", "python"),
        (PROFILE, "python"),
        (BUNDLED_PROFILE, "python"),
        ("Dockerfile", "uv"),
        ("Dockerfile", "node"),
        ("Dockerfile", "python"),
        ("Dockerfile", "pnpm"),
        ("docker/development/backend.Dockerfile", "python"),
        ("docker/development/backend.Dockerfile", "uv"),
        ("frontend/Dockerfile.dev", "node"),
        ("frontend/Dockerfile.dev", "pnpm"),
    ],
)
def test_one_stale_consumer_is_rejected(repository: Path, path: str, tool: str) -> None:
    _replace(repository, path, _tools(repository)[tool], "99.88.77")

    report = check_toolchain(repository)

    assert report.state == "failed"
    assert any(issue.path == path and issue.reason == "mismatch" for issue in report.issues)


@pytest.mark.parametrize("value", ['"24"', '"latest"', '["24.21.0"]', '"^24.21.0"'])
def test_mise_must_own_one_exact_version(repository: Path, value: str) -> None:
    _replace(repository, "mise.toml", f'node = "{_tools(repository)["node"]}"', f"node = {value}")

    report = check_toolchain(repository)

    assert report.state == "failed"
    assert any(
        issue.selector == "tools.node" and issue.reason == "non_exact_version"
        for issue in report.issues
    )


@pytest.mark.parametrize("change", ["missing_tool", "missing_admission", "masked_tool_failure"])
def test_full_install_cannot_omit_exact_scanner_admission(repository: Path, change: str) -> None:
    if change == "missing_tool":
        _replace(repository, "mise.toml", "python uv node pnpm gitleaks", "python uv node pnpm")
    elif change == "masked_tool_failure":
        _replace(repository, "mise.toml", "$(gitleaks version) &&", "$(gitleaks version);")
    else:
        path = repository / "mise.toml"
        path.write_text(
            "\n".join(
                line for line in path.read_text().splitlines() if "scanner_version=" not in line
            )
            + "\n",
            encoding="utf-8",
        )

    report = check_toolchain(repository)

    assert any(issue.selector == "tasks.install.run" for issue in report.issues)


@pytest.mark.parametrize(
    "scope,tools", [("backend", "python uv"), ("frontend", "python node pnpm")]
)
def test_scanner_does_not_widen_selective_install(repository: Path, scope: str, tools: str) -> None:
    _replace(
        repository,
        "mise.toml",
        f'mise install --locked {tools}"',
        f'mise install --locked {tools} gitleaks"',
    )

    report = check_toolchain(repository)

    assert any(issue.selector == f"tasks.install:{scope}.run" for issue in report.issues)


def test_declared_missing_tools_cannot_enable_implicit_installation(repository: Path) -> None:
    _replace(repository, "mise.toml", "auto_install = false", "auto_install = true")

    report = check_toolchain(repository)

    assert any(issue.selector == "settings.auto_install" for issue in report.issues)


@pytest.mark.parametrize("change", ["missing_platform", "missing_checksum", "foreign_archive"])
def test_scanner_lock_retains_the_supported_native_artifacts(repository: Path, change: str) -> None:
    path = repository / "mise.lock"
    text = path.read_text()
    header = '[tools.gitleaks."platforms.linux-arm64"]'
    start = text.index(header)
    end = text.index("\n[", start + len(header))
    block = text[start:end]
    if change == "missing_platform":
        replacement = ""
    elif change == "missing_checksum":
        replacement = "\n".join(
            line for line in block.splitlines() if not line.startswith("checksum")
        )
    else:
        replacement = block.replace("linux_arm64.tar.gz", "linux_x64.tar.gz")
    path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")

    report = check_toolchain(repository)

    assert any(issue.selector.startswith("tools.gitleaks.platforms") for issue in report.issues)


def test_scanner_version_requires_one_literal_owner_assignment(repository: Path) -> None:
    path = repository / ".github/actions/secret-scan/scanner.py"
    with path.open("a", encoding="utf-8") as target:
        target.write('\nVERSION = "8.30.1"\n')

    report = check_toolchain(repository)

    assert any(
        issue.selector == "VERSION" and issue.reason == "invalid_pin" for issue in report.issues
    )


def test_runtime_minor_targets_are_projections_not_patch_equality(repository: Path) -> None:
    minor = _tools(repository)["python"].rsplit(".", 1)[0]
    _replace(
        repository,
        "backend/pyproject.toml",
        f'python_version = "{minor}"',
        'python_version = "3.1"',
    )

    report = check_toolchain(repository)

    assert any(issue.selector == "tool.mypy.python_version" for issue in report.issues)


def test_all_release_stages_are_checked(repository: Path) -> None:
    path = repository / "Dockerfile"
    source = path.read_text()
    version = _tools(repository)["python"]
    declaration = next(
        line for line in source.splitlines() if line.startswith(f"FROM python:{version}")
    )
    external = declaration.split(" AS ", 1)[0].replace(f"python:{version}", "python:99.88.77")
    path.write_text(source + "\n" + external + " AS unexpected-runtime\n", encoding="utf-8")

    report = check_toolchain(repository)

    assert any(issue.path == "Dockerfile" and issue.reason == "mismatch" for issue in report.issues)


def test_internal_debug_stages_do_not_introduce_external_pins(repository: Path) -> None:
    path = repository / "docker/development/backend.Dockerfile"
    with path.open("a", encoding="utf-8") as output:
        output.write("\nFROM application AS extra-debug\nFROM extra-debug AS extra-development\n")

    report = check_toolchain(repository)

    assert report.state == "passed", report.issues
    assert not any(image.reference in {"application", "extra-debug"} for image in report.images)


@pytest.mark.parametrize("replacement", ["", "@sha256:abc", "@sha256:" + "z" * 64])
def test_tag_alone_or_malformed_digest_is_not_admitted(repository: Path, replacement: str) -> None:
    path = repository / "frontend/Dockerfile.dev"
    path.write_text(
        re.sub(r"@sha256:[0-9a-f]{64}", replacement, path.read_text()), encoding="utf-8"
    )

    report = check_toolchain(repository)

    assert any(
        issue.path == "frontend/Dockerfile.dev" and issue.reason == "unpinned_image"
        for issue in report.issues
    )


def test_equal_tags_with_different_digests_do_not_claim_content_identity(repository: Path) -> None:
    path = repository / "frontend/Dockerfile.dev"
    path.write_text(
        re.sub(r"@sha256:[0-9a-f]{64}", "@sha256:" + "0" * 64, path.read_text()), encoding="utf-8"
    )

    report = check_toolchain(repository)

    assert report.state == "passed", report.issues
    node_images = [image for image in report.images if image.tool == "node"]
    assert len({image.digest for image in node_images}) > 1
    assert {image.digest_identity for image in node_images} == {"not_verified"}
    assert report.projection()["digestIdentity"] == "not_verified"


def test_external_copy_source_is_checked(repository: Path) -> None:
    path = repository / "docker/development/backend.Dockerfile"
    with path.open("a", encoding="utf-8") as output:
        output.write(
            "\nCOPY --from=ghcr.io/astral-sh/uv:99.88.77@sha256:"
            + "0" * 64
            + " /uv /usr/local/bin/uv\n"
        )

    report = check_toolchain(repository)

    assert any(
        issue.path == "docker/development/backend.Dockerfile" and issue.reason == "mismatch"
        for issue in report.issues
    )


def test_comments_cannot_supply_missing_corepack_pin(repository: Path) -> None:
    version = _tools(repository)["pnpm"]
    _replace(repository, "frontend/Dockerfile.dev", f"corepack prepare pnpm@{version}", "true")
    path = repository / "frontend/Dockerfile.dev"
    with path.open("a", encoding="utf-8") as output:
        output.write(f"\n# RUN corepack prepare pnpm@{version} --activate\n")

    report = check_toolchain(repository)

    assert any(
        issue.selector == "pnpm" and issue.reason == "missing_declaration"
        for issue in report.issues
    )


def test_variable_image_is_rejected_even_when_default_matches(repository: Path) -> None:
    version = _tools(repository)["node"]
    _replace(repository, "frontend/Dockerfile.dev", f"node:{version}", "node:${NODE_VERSION}")

    report = check_toolchain(repository)

    assert any(issue.reason == "unsupported_image" for issue in report.issues)


def test_devcontainer_cannot_override_the_mise_version(repository: Path) -> None:
    _set_json(
        repository,
        ".devcontainer/devcontainer.json",
        "build",
        {"dockerfile": "Dockerfile", "context": "..", "args": {"MISE_VERSION": "1.2.3"}},
    )

    report = check_toolchain(repository)

    assert any(issue.selector == "build.args.MISE_VERSION" for issue in report.issues)


def test_devcontainer_mise_pin_tracks_repository_minimum(repository: Path) -> None:
    config = tomllib.loads((repository / "mise.toml").read_text())
    _replace(
        repository,
        ".devcontainer/Dockerfile",
        f"MISE_VERSION={config['min_version']}",
        "MISE_VERSION=1.2.3",
    )

    report = check_toolchain(repository)

    assert any(
        issue.path == ".devcontainer/Dockerfile" and issue.reason == "mismatch"
        for issue in report.issues
    )


@pytest.mark.parametrize(
    ("action", "key"),
    [
        ("actions/setup-python", "python-version"),
        ("actions/setup-node", "node-version"),
        ("astral-sh/setup-uv", "version"),
        ("pnpm/action-setup", "version"),
    ],
)
def test_new_workflow_setup_steps_are_discovered(repository: Path, action: str, key: str) -> None:
    path = repository / ".github/workflows/new-host-native.yaml"
    path.write_text(
        f"name: New host\non: push\njobs:\n  native:\n    runs-on: macos-15\n"
        f"    steps:\n      - uses: {action}@{'a' * 40}\n"
        f"        with:\n          {key}: '99.88.77'\n",
        encoding="utf-8",
    )

    report = check_toolchain(repository)

    assert any(
        issue.path.endswith("new-host-native.yaml")
        and issue.selector == f"jobs.native.steps[0].with.{key}"
        for issue in report.issues
    )


def test_action_release_and_node_type_versions_are_independent(repository: Path) -> None:
    path = repository / ".github/workflows/unrelated-action.yml"
    path.write_text(
        "name: Other\non: push\njobs:\n  other:\n    runs-on: ubuntu-24.04\n"
        "    steps:\n      - uses: actions/checkout@v99.88.77\n",
        encoding="utf-8",
    )
    _set_json(repository, "frontend/package.json", "devDependencies", {"@types/node": "99.88.77"})

    report = check_toolchain(repository)

    assert report.state == "passed", report.issues


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("package.json", '{"engines": {}, "engines": {}}'),
        ("backend/uv.lock", 'requires-python = "unterminated'),
        (".github/workflows/broken.yml", "jobs: [unterminated"),
    ],
)
def test_malformed_native_input_fails_closed(repository: Path, path: str, source: str) -> None:
    (repository / path).write_text(source, encoding="utf-8")

    report = check_toolchain(repository)

    assert any(issue.path == path and issue.reason == "invalid_source" for issue in report.issues)


def test_missing_owner_is_reported_without_hiding_other_drift(repository: Path) -> None:
    (repository / "mise.lock").unlink()
    _set_json(repository, "package.json", "packageManager", "pnpm@99.88.77")
    output = io.StringIO()

    status = run([], repo_root=repository, stdout=output)
    report = json.loads(output.getvalue())

    assert status == 1
    assert report["state"] == "failed"
    assert {issue["path"] for issue in report["issues"]} >= {"mise.lock", "package.json"}


def test_root_adapter_does_not_cache_an_earlier_worktree_read(repository: Path) -> None:
    first = io.StringIO()
    assert run([], repo_root=repository, stdout=first) == 0
    _set_json(repository, "package.json", "packageManager", "pnpm@99.88.77")
    second = io.StringIO()

    assert run(["--format", "human"], repo_root=repository, stdout=second) == 1
    assert "packageManager" in second.getvalue()
    assert "unverified" in second.getvalue()
    assert json.loads(first.getvalue())["state"] == "passed"


def test_concurrent_edit_invalidates_the_observation(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = repository / "package.json"
    original_read = Path.read_bytes
    changed = False

    def edit_after_first_read(path: Path) -> bytes:
        nonlocal changed
        raw = original_read(path)
        if path == target and not changed:
            changed = True
            document = json.loads(raw)
            document["packageManager"] = "pnpm@99.88.77"
            path.write_text(json.dumps(document), encoding="utf-8")
        return raw

    monkeypatch.setattr(Path, "read_bytes", edit_after_first_read)

    report = check_toolchain(repository)

    assert report.state == "failed"
    assert any(
        issue.path == "package.json" and issue.reason == "input_changed" for issue in report.issues
    )
