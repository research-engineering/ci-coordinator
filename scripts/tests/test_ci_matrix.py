from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError
from ruamel.yaml import YAML
from scripts.bounded_git import BoundedGitResult
from scripts.ci_matrix_contract import (
    MatrixProfile,
    admit_execution_results,
    admit_utility_workflow,
)
from scripts.ci_matrix_inputs import frontend_inputs, input_dispositions, python_inputs
from scripts.ci_matrix_inventory import classify_paths
from scripts.ci_utility_checks import UtilityCommand
from scripts.ci_utility_inventory import repository_paths
from scripts.proofkit_common import read_json_object

ROOT = Path(__file__).resolve().parents[2]


def _profile() -> dict[str, object]:
    return read_json_object(ROOT / "proofkit/ci-matrix.v1.json")


def _result(command: str, *, code: int | None = 0, error: str | None = None) -> dict[str, object]:
    return {
        "commandId": command,
        "argvSha256": "a" * 64,
        "elapsedMilliseconds": 1,
        "exitCode": code,
        "status": "passed" if code == 0 and error is None else "failed",
        "processError": error,
    }


def test_native_and_new_language_surfaces_are_classified_without_silent_default() -> None:
    profile = MatrixProfile.model_validate(_profile())
    actual = classify_paths(
        profile,
        (
            "backend/tests/falsifier.py",
            ".github/workflows/new-job.yml",
            "tooling/other-helper/extra.go",
            "new-runtime/Dockerfile",
        ),
    )
    assert actual["backend/tests/falsifier.py"] == ("python",)
    assert actual[".github/workflows/new-job.yml"] == ("yaml",)
    assert actual["tooling/other-helper/extra.go"] == ("go",)
    assert actual["new-runtime/Dockerfile"] == ("docker",)
    with pytest.raises(ValueError, match="no admitted check surface"):
        classify_paths(profile, ("new-provider/main.tf",))


def test_current_input_universe_has_predicate_specific_native_or_owned_dispositions() -> None:
    paths = repository_paths(ROOT)
    rows = input_dispositions(ROOT, MatrixProfile.model_validate(_profile()), paths)

    assert set(rows) == set(paths)
    assert rows["scripts/ci_matrix.py"].command_id == "python.lint"
    assert rows["frontend/src/App.tsx"].command_id == "frontend.quality"
    assert rows["proofkit/ci-matrix.v1.json"].command_id == "repository.json"
    assert rows["frontend/src/api/generated.ts"].role == "generated"
    assert rows["frontend/src/api/generated.ts"].command_id == "frontend.contract-types"
    fixture = rows["fixtures/native-target-repository/.ci-coordinator/ci-coordinator.cjs"]
    assert (fixture.role, fixture.command_id) == (
        "fixture",
        "native-target-artifacts.check",
    )
    assert rows["tooling/quality/uv.lock"].command_id == "dependency.audit"
    assert rows["docker/runtime/verify.py"].command_id == "python.lint"
    assert rows["docker/runtime/security/build.sh"].command_id == "workflow.lint"
    assert rows["docker/runtime/security/check_zlib.c"].command_id == "container.smoke"
    assert rows["docker/runtime/ubuntu-snapshot.conf"].command_id == "container.smoke"
    assert rows["docs/images/repository-catalog.png"].predicate.endswith(
        "image pixels are not analyzed"
    )


@pytest.mark.parametrize(
    "path",
    [
        "new-tool/example.py",
        "backend/dist/ignored.py",
        "new-tool/settings.toml",
        "backend/src/unowned.cjs",
        "docker/runtime/security/unowned.patch",
        "fixtures/new-negative/example.py",
        "frontend/openapi/ignored.ts",
        "new-assets/unconsumed.png",
    ],
)
def test_surface_assignment_cannot_substitute_for_native_input_ownership(
    path: str,
) -> None:
    profile = MatrixProfile.model_validate(_profile())
    assert path in classify_paths(profile, (path,))
    with pytest.raises(ValueError, match="no effective input owner"):
        input_dispositions(ROOT, profile, (*repository_paths(ROOT), path))


def test_python_native_root_removal_cannot_retain_matrix_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = "backend/src/ci_coordinator/runtime/__main__.py"
    assert path in python_inputs(ROOT, (path,))
    monkeypatch.setattr("scripts.ci_matrix_inputs.PYTHON_QUALITY_TARGETS", ("../scripts",))
    with pytest.raises(ValueError, match="no effective input owner"):
        input_dispositions(ROOT, MatrixProfile.model_validate(_profile()), repository_paths(ROOT))


def test_explicit_hidden_python_root_does_not_admit_hidden_descendants() -> None:
    owner = ".github/actions/secret-scan/entrypoint.py"
    hidden = ".github/actions/secret-scan/.cache/ignored.py"
    outside = ".github/actions/unowned/entrypoint.py"
    assert python_inputs(ROOT, (owner, hidden, outside)) == {owner}


@pytest.mark.parametrize("path", ["backend/nested/pyproject.toml", "frontend/nested/biome.json"])
def test_nested_native_configuration_requires_a_new_discovery_contract(
    path: str,
) -> None:
    with pytest.raises(ValueError, match="configuration needs admission"):
        input_dispositions(
            ROOT,
            MatrixProfile.model_validate(_profile()),
            (*repository_paths(ROOT), path),
        )


def test_missing_native_yaml_operand_cannot_be_replaced_by_surface_glob(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.ci_utility_checks import commands as utility_commands

    missing = "config.example.yaml"

    def changed_commands(root: Path, check: str) -> tuple[UtilityCommand, ...]:
        return tuple(
            replace(
                command,
                argv=tuple(argument for argument in command.argv if argument != missing),
            )
            for command in utility_commands(root, check)
        )

    monkeypatch.setattr("scripts.ci_matrix_inputs.utility_commands", changed_commands)
    with pytest.raises(ValueError, match=f"no effective input owner for {missing}"):
        input_dispositions(ROOT, MatrixProfile.model_validate(_profile()), repository_paths(ROOT))


def test_generated_frontend_input_is_excluded_from_biome_and_owned_by_regeneration() -> None:
    path = "frontend/src/api/generated.ts"
    assert path not in frontend_inputs(ROOT, (path,))
    rows = input_dispositions(
        ROOT, MatrixProfile.model_validate(_profile()), repository_paths(ROOT)
    )
    assert rows[path].command_id == "frontend.contract-types"


@pytest.mark.parametrize(
    ("section", "setting"),
    [
        ("tool.ruff", 'include = ["tests/**/*.py"]'),
        ("tool.ruff", 'exclude = ["src/ci_coordinator/runtime/__main__.py"]'),
        ("tool.ruff", 'extend-exclude = ["src/ci_coordinator/runtime/__main__.py"]'),
        ("tool.ruff", 'extend = "other.toml"'),
        ("tool.ruff", 'extension = { py = "ipynb" }'),
        ("tool.ruff", "force-exclude = true"),
        ("tool.ruff", "respect-gitignore = false"),
        ("tool.ruff.lint", 'exclude = ["src/ci_coordinator/runtime/__main__.py"]'),
        ("tool.ruff.lint", 'ignore = ["ALL"]'),
        ("tool.ruff.lint", 'extend-ignore = ["ALL"]'),
        ("tool.ruff.lint", 'extend-per-file-ignores = { "src/**" = ["ALL"] }'),
        ("tool.ruff.lint.per-file-ignores", '"src/**" = ["ALL"]'),
    ],
)
def test_real_ruff_configuration_cannot_silently_remove_admitted_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, section: str, setting: str
) -> None:
    for name in ("backend/pyproject.toml", "scripts/python_witness.py"):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    monkeypatch.setattr(
        "scripts.ci_matrix_inputs.run_git",
        lambda *_args, **_kwargs: BoundedGitResult(0, "", ""),
    )
    path = "backend/src/ci_coordinator/runtime/__main__.py"
    assert path in python_inputs(tmp_path, (path,))
    config = tmp_path / "backend/pyproject.toml"
    original = config.read_text()
    assert original.count(f"[{section}]\n") == 1
    config.write_text(original.replace(f"[{section}]\n", f"[{section}]\n{setting}\n"))

    with pytest.raises(ValueError, match="Python discovery configuration"):
        python_inputs(tmp_path, (path,))


def test_python_cli_override_cannot_inherit_root_membership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("backend/pyproject.toml", "scripts/python_witness.py"):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    monkeypatch.setattr(
        "scripts.ci_matrix_inputs.run_git",
        lambda *_args, **_kwargs: BoundedGitResult(0, "", ""),
    )
    path = "backend/src/ci_coordinator/runtime/__main__.py"
    assert path in python_inputs(tmp_path, (path,))
    witness = tmp_path / "scripts/python_witness.py"
    witness.write_text(
        witness.read_text().replace(
            '("check", *PYTHON_QUALITY_TARGETS)',
            '("check", "--exclude", "src/**", *PYTHON_QUALITY_TARGETS)',
        )
    )
    with pytest.raises(ValueError, match="Python discovery command"):
        python_inputs(tmp_path, (path,))


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("files", "maxSize", 1),
        ("files", "ignoreUnknown", True),
        ("files", "experimentalScannerIgnores", ["src"]),
        ("linter", "includes", ["tests/**"]),
        ("linter", "enabled", False),
        ("formatter", "includes", ["tests/**"]),
        ("formatter", "enabled", False),
        ("assist", "includes", ["tests/**"]),
        ("assist", "enabled", False),
        ("root", "overrides", [{"includes": ["**"], "linter": {"enabled": False}}]),
        ("root", "extends", ["./other.json"]),
        ("root", "vcs", {"enabled": True, "useIgnoreFile": True}),
        ("root", "javascript", {"linter": {"enabled": False}}),
        ("root", "root", False),
    ],
)
def test_real_biome_configuration_requires_closed_discovery_dialect(
    tmp_path: Path, section: str, key: str, value: object
) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    for name in ("biome.json", "package.json"):
        (frontend / name).write_bytes((ROOT / "frontend" / name).read_bytes())
    path = "frontend/probe.js"
    (tmp_path / path).write_text("export const probe = 1;\n")
    assert path in frontend_inputs(tmp_path, (path,))
    config = frontend / "biome.json"
    raw = json.loads(config.read_text())
    (raw if section == "root" else raw[section])[key] = value
    config.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="frontend discovery"):
        frontend_inputs(tmp_path, (path,))


def test_biome_default_size_limit_is_part_of_input_admission(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    for name in ("biome.json", "package.json"):
        (frontend / name).write_bytes((ROOT / "frontend" / name).read_bytes())
    path = "frontend/probe.js"
    (tmp_path / path).write_bytes(b" " * 1_048_576)
    assert path in frontend_inputs(tmp_path, (path,))
    (tmp_path / path).write_bytes(b" " * 1_048_577)
    with pytest.raises(ValueError):
        frontend_inputs(tmp_path, (path,))


def test_font_basename_collision_does_not_inherit_another_css_input() -> None:
    path = "frontend/src/assets/fonts/unowned/lato-latin-400-normal.woff2"
    with pytest.raises(ValueError, match="font has no admitted CSS consumer"):
        input_dispositions(
            ROOT,
            MatrixProfile.model_validate(_profile()),
            (*repository_paths(ROOT), path),
        )


def test_commented_font_url_invalidates_reviewed_consumer_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import ci_matrix_inputs
    from scripts.repository_paths import read_repository_regular_file

    owner_path = Path("frontend/src/styles/tokens.css")
    literal = b'url("../assets/fonts/lato-latin-400-normal.woff2")'
    source = (ROOT / owner_path).read_bytes()
    assert source.count(literal) == 1
    changed = source.replace(literal, b"/* " + literal + b" */")

    def read(root: Path, path: Path, context: str, *, maximum_bytes: int) -> bytes:
        return (
            changed
            if path == owner_path
            else read_repository_regular_file(root, path, context, maximum_bytes=maximum_bytes)
        )

    monkeypatch.setattr(ci_matrix_inputs, "read_repository_regular_file", read)
    with pytest.raises(ValueError, match="font has no admitted CSS consumer"):
        input_dispositions(ROOT, MatrixProfile.model_validate(_profile()), repository_paths(ROOT))


def test_patch_comment_does_not_supply_a_package_manager_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import ci_matrix_inputs

    original = ci_matrix_inputs._text
    path = "patches/unowned.patch"

    def read(root: Path, name: str) -> str:
        value = original(root, name)
        return value + f"\n# {path}\n" if name == "pnpm-workspace.yaml" else value

    monkeypatch.setattr(ci_matrix_inputs, "_text", read)
    with pytest.raises(ValueError, match="unowned package patch"):
        input_dispositions(
            ROOT,
            MatrixProfile.model_validate(_profile()),
            (*repository_paths(ROOT), path),
        )


@pytest.mark.parametrize(
    "paths", [(), ("file.py", "file.py"), ("../outside.py",), ("/outside/file.py",)]
)
def test_empty_duplicate_and_escaping_input_universes_fail(
    paths: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError):
        classify_paths(MatrixProfile.model_validate(_profile()), paths)


def test_repeated_command_cannot_replace_a_missing_utility_result() -> None:
    expected = ["utility.yaml", "utility.schemas"]
    good = [_result(name) for name in expected]
    assert [row.commandId for row in admit_execution_results(expected, good)] == expected
    for invalid in (
        good[:1],
        [good[0], good[0]],
        [*good, _result("utility.spelling")],
        [],
    ):
        with pytest.raises(ValueError, match="exactly match"):
            admit_execution_results(expected, invalid)


@pytest.mark.parametrize("code,error", [(1, None), (None, "timeout"), (0, "output exceeded bound")])
def test_nonzero_incomplete_or_truncated_process_cannot_become_green(
    code: int | None, error: str | None
) -> None:
    result = _result("utility.yaml", code=code, error=error)
    assert admit_execution_results(["utility.yaml"], [result])[0].status == "failed"
    result["status"] = "passed"
    with pytest.raises(ValidationError, match="differs from the process"):
        admit_execution_results(["utility.yaml"], [result])


def test_matrix_requires_every_utility_in_exactly_one_group() -> None:
    good = MatrixProfile.model_validate(_profile())
    raw = good.model_dump()
    removed = deepcopy(raw)
    removed["utilityGroups"][0]["commandIds"].pop()
    with pytest.raises(ValidationError, match="partition every utility"):
        MatrixProfile.model_validate(removed)
    repeated = deepcopy(raw)
    repeated["utilityGroups"][1]["commandIds"].append(repeated["utilityGroups"][0]["commandIds"][0])
    with pytest.raises(ValidationError, match="exactly one execution group"):
        MatrixProfile.model_validate(repeated)


def test_unknown_command_reference_and_duplicate_command_declarations_fail() -> None:
    raw = MatrixProfile.model_validate(_profile()).model_dump()
    unknown = deepcopy(raw)
    unknown["surfaces"][0]["commandIds"].append("missing.command")
    with pytest.raises(ValidationError, match="unknown command"):
        MatrixProfile.model_validate(unknown)
    repeated = deepcopy(raw)
    repeated["commands"].append(repeated["commands"][0])
    with pytest.raises(ValidationError, match="identities must be unique"):
        MatrixProfile.model_validate(repeated)


@pytest.mark.parametrize("operand", ["dependency", "assertion", "expression", "invocation", "skip"])
def test_each_native_utility_gate_operand_is_required_independently(
    operand: str,
) -> None:
    profile = MatrixProfile.model_validate(_profile())
    source = (ROOT / profile.nativeWorkflow).read_text()
    workflow = YAML(typ="safe", pure=True).load(source)
    admit_utility_workflow(profile, workflow)
    jobs = workflow["jobs"]
    group = profile.utilityGroups[0]
    gate = jobs["pull-request-gate"]
    variable = "UTILITY_" + group.id.upper().replace("-", "_") + "_RESULT"
    if operand == "dependency":
        gate["needs"].remove(group.jobId)
    elif operand == "assertion":
        gate["steps"][0]["run"] = gate["steps"][0]["run"].replace(
            f'test "${{{variable}}}" = success', "true"
        )
    elif operand == "expression":
        gate["steps"][0]["env"][variable] = "success"
    elif operand == "invocation":
        for step in jobs[group.jobId]["steps"]:
            if "scripts.ci_matrix run" in step.get("run", ""):
                step["continue-on-error"] = True
    else:
        jobs[group.jobId]["if"] = "false"
    with pytest.raises(ValueError):
        admit_utility_workflow(profile, workflow)
