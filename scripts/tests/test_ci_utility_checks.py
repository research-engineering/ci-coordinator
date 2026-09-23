from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from scripts import ci_utility_checks as checks
from scripts import ci_utility_inventory as inventory
from scripts.bounded_git import BoundedGitResult
from scripts.bounded_process import CommandResult


def _write(root: Path, name: str, content: str = "fixture\n") -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in (
        "tooling/quality/yamllint.yaml",
        "tooling/quality/hadolint.yaml",
        "tooling/quality/codespell.ini",
        "nested/compose.debug.yml",
        ".github/dependabot.yml",
        "Dockerfile",
        "nested/Dockerfile.debug",
        "nested/backend.Dockerfile",
        "tooling/first/go.mod",
        "tooling/first/main.go",
        "tooling/first/main_test.go",
        "tooling/second/go.mod",
        "tooling/second/sub/package.go",
        "docs/old-note.md",
    ):
        _write(tmp_path, name)
    _write(tmp_path, "tooling/quality/hadolint-exceptions.json", "[]\n")
    _write(tmp_path, "tooling/quality/spelling-words.dic", "disjointness\nunparseable\n")
    _write(tmp_path, "tooling/quality/pyproject.toml", '[project]\nname = "fixture"\n')
    _write(
        tmp_path,
        "contracts/example.schema.json",
        '{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object"}\n',
    )
    _write(
        tmp_path,
        "frontend/biome.json",
        '{"$schema":"https://biomejs.dev/schemas/2.0.0/schema.json"}\n',
    )
    names = tuple(
        sorted(
            path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()
        )
    )
    monkeypatch.setattr(checks, "repository_paths", lambda _root: names)
    monkeypatch.setattr(inventory, "repository_paths", lambda _root: names)
    return tmp_path


def test_git_inventory_includes_cached_and_nonignored_paths_and_deduplicates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    observed = []

    def git(root: Path, argv: tuple[str, ...], **options: object) -> BoundedGitResult:
        observed.append((root, argv, options))
        return BoundedGitResult(0, "z.yml\0a.yml\0z.yml\0", "")

    monkeypatch.setattr(inventory, "run_git", git)
    assert inventory.repository_paths(tmp_path) == ("a.yml", "z.yml")
    assert observed[0][1] == ("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    assert observed[0][2] == {"decode_errors": "surrogateescape"}


@pytest.mark.parametrize(
    "output", ["", "a.yml", "../a.yml\0", "/a.yml\0", "a\n.yml\0", ".\0", "a/../b.yml\0"]
)
def test_git_inventory_rejects_empty_truncated_or_unsupported_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, output: str
) -> None:
    monkeypatch.setattr(
        inventory, "run_git", lambda *_args, **_kwargs: BoundedGitResult(0, output, "")
    )
    with pytest.raises(ValueError):
        inventory.repository_paths(tmp_path)


def test_yaml_covers_hidden_nested_and_new_configuration(repository: Path) -> None:
    command = checks.commands(repository, "yaml")[-1]
    assert command.argv[1:6] == (
        "--strict",
        "--format",
        "parsable",
        "--config-file",
        "tooling/quality/yamllint.yaml",
    )
    assert ".github/dependabot.yml" in command.argv
    assert "nested/compose.debug.yml" in command.argv
    assert "tooling/quality/hadolint.yaml" in command.argv


@pytest.mark.parametrize(
    "kind", ["leaf-symlink", "parent-symlink", "missing", "directory", "binary"]
)
def test_inputs_reject_each_isolated_filesystem_boundary(repository: Path, kind: str) -> None:
    source = repository / "nested/compose.debug.yml"
    if kind == "parent-symlink":
        parent = source.parent
        parent.rename(repository / "original")
        parent.symlink_to(repository / "original", target_is_directory=True)
    else:
        source.unlink()
        if kind == "leaf-symlink":
            source.symlink_to(repository / "Dockerfile")
        elif kind == "directory":
            source.mkdir()
        elif kind == "binary":
            source.write_bytes(b"a\0b")
    with pytest.raises((OSError, ValueError)):
        checks.commands(repository, "yaml")


def test_empty_selection_and_unknown_check_cannot_pass(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError, match="unsupported"):
        checks.commands(repository, "arbitrary-command")
    monkeypatch.setattr(checks, "repository_paths", lambda _root: ("Dockerfile",))
    with pytest.raises(ValueError, match="no applicable"):
        checks.commands(repository, "yaml")


def test_input_bounds_fail_without_truncating_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "first.yaml", "one\n")
    _write(tmp_path, "second.yaml", "two\n")
    monkeypatch.setattr(inventory, "MAX_TOTAL_SOURCE_BYTES", 7)
    with pytest.raises(ValueError, match="aggregate"):
        inventory.admitted_sources(tmp_path, ("first.yaml", "second.yaml"))
    monkeypatch.setattr(inventory, "MAX_SOURCE_BYTES", 3)
    with pytest.raises(ValueError, match="exceeds"):
        inventory.admitted_sources(tmp_path, ("first.yaml",))


def test_schema_commands_use_offline_bundles_and_metaschemas(repository: Path) -> None:
    planned = checks.commands(repository, "schemas")
    assert {
        command.argv[1:3] for command in planned[1:] if command.argv[1] == "--builtin-schema"
    } == {("--builtin-schema", "vendor.dependabot"), ("--builtin-schema", "vendor.compose-spec")}
    meta = next(command for command in planned if "--check-metaschema" in command.argv)
    assert "contracts/example.schema.json" in meta.argv
    assert not any("frontend/biome.json" in command.argv for command in planned)


def test_arbitrary_schema_dialect_is_rejected(repository: Path) -> None:
    _write(
        repository,
        "contracts/example.schema.json",
        '{"$schema":"https://untrusted.invalid/schema"}',
    )
    with pytest.raises(ValueError, match="offline metaschema"):
        checks.commands(repository, "schemas")


def test_devcontainer_core_vendor_rejects_drift_and_nonlocal_ref(tmp_path: Path) -> None:
    source = '{"$ref":"#/definitions/a","definitions":{"a":{"type":"object"}}}'
    _write(tmp_path, inventory.DEVCONTAINER_SCHEMA, source)
    provenance = "tooling/quality/schemas/devcontainers/provenance.json"
    _write(
        tmp_path, provenance, json.dumps({"sha256": hashlib.sha256(source.encode()).hexdigest()})
    )
    inventory.admit_devcontainer_schema(tmp_path)
    _write(tmp_path, inventory.DEVCONTAINER_SCHEMA, source + " ")
    with pytest.raises(ValueError, match="provenance"):
        inventory.admit_devcontainer_schema(tmp_path)
    source = '{"$ref":"https://untrusted.invalid/schema"}'
    _write(tmp_path, inventory.DEVCONTAINER_SCHEMA, source)
    _write(
        tmp_path, provenance, json.dumps({"sha256": hashlib.sha256(source.encode()).hexdigest()})
    )
    with pytest.raises(ValueError, match="nonlocal"):
        inventory.admit_devcontainer_schema(tmp_path)


def test_docker_inventory_checks_all_filenames_with_fixed_builder(repository: Path) -> None:
    planned = checks.commands(repository, "build-checks")
    assert planned[1].output_contract == "buildkit"
    assert {command.argv[-2] for command in planned[2:]} == {
        "Dockerfile",
        "nested/Dockerfile.debug",
        "nested/backend.Dockerfile",
    }
    assert all(
        "--check" in command.argv and "BUILDKIT_DOCKERFILE_CHECK=error=true" in command.argv
        for command in planned[2:]
    )
    assert all(
        command.argv[command.argv.index("--builder") + 1] == "ci-utility-checks"
        for command in planned[2:]
    )


@pytest.mark.parametrize(
    "nodes",
    [
        [],
        [{"Status": "stopped", "Version": "v0.33.0"}],
        [{"Status": "running", "Version": "v0.32.0"}],
        [{"Status": "running"}],
    ],
)
def test_buildkit_pin_and_readiness_are_independent(nodes: object, tmp_path: Path) -> None:
    command = checks.UtilityCommand(("docker",), tmp_path, "buildkit")
    checks.admit_output(
        command,
        '{"Name":"ci-utility-checks","Driver":"docker-container","Nodes":[{"Status":"running","Version":"v0.33.0"}]}',
    )
    with pytest.raises(ValueError):
        checks.admit_output(
            command,
            json.dumps({"Name": "ci-utility-checks", "Driver": "docker-container", "Nodes": nodes}),
        )


def test_buildkit_inventory_requires_one_exact_builder_and_no_error(tmp_path: Path) -> None:
    command = checks.UtilityCommand(("docker",), tmp_path, "buildkit")
    correct = {
        "Name": "ci-utility-checks",
        "Driver": "docker-container",
        "Nodes": [{"Status": "running", "Version": "v0.33.0"}],
    }
    unrelated = {"Name": "default", "Driver": "docker", "Nodes": []}
    checks.admit_output(command, json.dumps(unrelated) + "\n" + json.dumps(correct))
    for rows in (
        [],
        [unrelated],
        [correct, correct],
        [{**correct, "Err": "provider unavailable"}],
        [{**correct, "Driver": "docker"}],
    ):
        with pytest.raises(ValueError):
            checks.admit_output(command, "\n".join(json.dumps(row) for row in rows))


def test_go_commands_cover_each_module_and_include_test_sources(repository: Path) -> None:
    for check_id in ("go-vet", "staticcheck", "govulncheck"):
        planned = checks.commands(repository, check_id)
        actual = [
            command for command in planned if command.output_contract in {"status", "govulncheck"}
        ]
        assert {command.cwd for command in actual} == {
            repository / "tooling/first",
            repository / "tooling/second",
        }
        assert all(command.argv[-1] == "./..." for command in actual)
    assert "-tests=true" in checks.commands(repository, "staticcheck")[-1].argv
    assert "-test" in checks.commands(repository, "govulncheck")[-1].argv
    assert "-format=text" in checks.commands(repository, "govulncheck")[-1].argv
    assert checks.commands(repository, "govulncheck")[-1].output_contract == "govulncheck"
    assert "tooling/first/main_test.go" in checks.commands(repository, "gofmt")[-1].argv


@pytest.mark.parametrize("check_id", ["gofmt", "go-vet", "staticcheck", "govulncheck"])
def test_every_go_check_plans_native_selection_before_analysis(
    repository: Path, check_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("pure command planning executed a native tool")

    monkeypatch.setattr(checks, "spawn", forbidden)
    planned = checks.commands(repository, check_id)
    assert planned[0].argv == ("go", "version")
    assert [command.output_contract for command in planned[1:3]] == ["go-inputs", "go-inputs"]
    assert planned[1].expected_go_files == ("main.go", "main_test.go")
    assert planned[2].expected_go_files == ("sub/package.go",)
    assert all(
        command.argv[1:5] == ("list", "-e", "-find", "-mod=readonly") for command in planned[1:3]
    )


def _go_package(root: Path, **updates: object) -> dict[str, object]:
    return {
        "Dir": str(root),
        "GoFiles": ["main.go"],
        "TestGoFiles": ["main_test.go"],
        "XTestGoFiles": ["external_test.go"],
        "CgoFiles": None,
        "IgnoredGoFiles": None,
        "InvalidGoFiles": None,
        "Error": None,
        "Incomplete": False,
        **updates,
    }


def test_native_go_admission_covers_internal_external_tests_and_multiple_packages(
    tmp_path: Path,
) -> None:
    expected = ("main.go", "main_test.go", "external_test.go", "sub/other.go")
    output = (
        json.dumps(_go_package(tmp_path))
        + "\n"
        + json.dumps(
            _go_package(tmp_path / "sub", GoFiles=["other.go"], TestGoFiles=None, XTestGoFiles=None)
        )
    )
    inventory.admit_go_effective_inputs(tmp_path, expected, output)


@pytest.mark.parametrize(
    "package,expected",
    [
        ({"GoFiles": ["main.go"]}, ("main.go",)),
        ({"TestGoFiles": ["main_test.go"]}, ("main_test.go",)),
        (
            {"GoFiles": ["main.go"], "XTestGoFiles": ["external_test.go"]},
            ("main.go", "external_test.go"),
        ),
    ],
    ids=["native-omits-zero-values", "native-test-only", "native-external-test"],
)
def test_native_go_omitempty_fields_preserve_exact_effective_cohort(
    tmp_path: Path, package: dict[str, object], expected: tuple[str, ...]
) -> None:
    inventory.admit_go_effective_inputs(
        tmp_path, expected, json.dumps({"Dir": str(tmp_path), **package})
    )


@pytest.mark.parametrize(
    "package",
    [
        {},
        {"GoFiles": ["main.go"]},
        {"Dir": ".", "GoFiles": ["main.go"]},
        {"Error": {"Err": "bad import"}},
        {"Incomplete": True},
        {"Incomplete": None},
        {"Incomplete": 0},
        {"IgnoredGoFiles": ["main.go"]},
        {"CgoFiles": ["main.go"]},
        {"InvalidGoFiles": ["main.go"]},
        {"TestGoFiles": ["main_test.go"]},
    ],
    ids=[
        "missing-source",
        "missing-dir",
        "relative-dir",
        "error",
        "incomplete",
        "null-flag",
        "numeric-flag",
        "ignored",
        "cgo",
        "invalid",
        "missing-production-file",
    ],
)
def test_native_go_optional_zero_defaults_do_not_hide_missing_or_excluded_sources(
    tmp_path: Path, package: dict[str, object]
) -> None:
    record = {"Dir": str(tmp_path), **package} if "GoFiles" not in package else package
    with pytest.raises(ValueError):
        inventory.admit_go_effective_inputs(tmp_path, ("main.go",), json.dumps(record))


@pytest.mark.parametrize(
    "updates",
    [
        {"IgnoredGoFiles": ["native.go"]},
        {"CgoFiles": ["native.go"]},
        {"InvalidGoFiles": ["native.go"]},
        {"Error": {"Err": "import C in test unsupported"}},
        {"Incomplete": True},
        {"GoFiles": []},
        {"GoFiles": ["main.go", "extra.go"]},
        {"GoFiles": ["main.go", "main.go"]},
        {"GoFiles": ["../main.go"]},
        {"GoFiles": "main.go"},
        {"Dir": "/outside"},
    ],
    ids=[
        "ignored-cgo",
        "active-cgo",
        "invalid",
        "native-error",
        "incomplete",
        "missing",
        "extra",
        "duplicate",
        "escape",
        "wrong-type",
        "foreign-dir",
    ],
)
def test_native_go_admission_rejects_each_incomplete_input_operand(
    tmp_path: Path, updates: dict[str, object]
) -> None:
    with pytest.raises(ValueError):
        inventory.admit_go_effective_inputs(
            tmp_path,
            ("main.go", "main_test.go", "external_test.go"),
            json.dumps(_go_package(tmp_path, **updates)),
        )


@pytest.mark.parametrize(
    "mode", ["empty", "partial", "duplicate-package", "duplicate-key", "malformed"]
)
def test_native_go_inventory_rejects_incomplete_or_ambiguous_streams(
    tmp_path: Path, mode: str
) -> None:
    output = json.dumps(_go_package(tmp_path))
    if mode == "empty":
        output = ""
    elif mode == "partial":
        output = json.dumps({"Dir": str(tmp_path), "GoFiles": ["main.go"]})
    elif mode == "duplicate-package":
        output += "\n" + output
    elif mode == "duplicate-key":
        output = output[:-1] + ', "GoFiles": ["main.go"]}'
    else:
        output += "broken"
    with pytest.raises(ValueError):
        inventory.admit_go_effective_inputs(
            tmp_path, ("main.go", "main_test.go", "external_test.go"), output
        )


def test_nested_go_modules_have_disjoint_expected_native_cohorts() -> None:
    names = ("go.mod", "root.go", "nested/go.mod", "nested/child.go")
    assert inventory.go_module_sources(names, ".") == ("root.go",)
    assert inventory.go_module_sources(names, "nested") == ("child.go",)


@pytest.mark.parametrize("check_id", ["gofmt", "go-vet", "staticcheck", "govulncheck"])
def test_native_cgo_refusal_stops_every_analyzer(
    repository: Path, check_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    def spawn(binary: str, argv: tuple[str, ...], **_options: object) -> CommandResult:
        calls.append((binary, *argv))
        if argv == ("version",):
            return CommandResult(0, "go version go1.27.1 linux/amd64\n", "")
        return CommandResult(
            0,
            json.dumps(_go_package(repository / "tooling/first", IgnoredGoFiles=["native.go"])),
            "",
        )

    monkeypatch.setattr(checks, "spawn", spawn)
    with pytest.raises(ValueError, match="IgnoredGoFiles"):
        checks.run(repository, check_id, {"PATH": "/usr/bin:/bin"})
    assert len(calls) == 2 and calls[-1][:2] == ("go", "list")


@pytest.mark.parametrize(
    "name,source",
    [
        ("orphan/main.go", "package main"),
        ("tooling/first/testdata/a.go", "package a"),
        ("tooling/first/platform_windows.go", "package main"),
        ("tooling/first/tagged.go", "//go:build custom\npackage main"),
    ],
)
def test_go_discovery_rejects_uncovered_source_configurations(
    repository: Path, name: str, source: str
) -> None:
    names = inventory.repository_paths(repository)
    _write(repository, name, source)
    with pytest.raises(ValueError):
        inventory.go_modules(repository, (*names, name))


def test_gofmt_nonempty_output_is_failure_even_with_successful_status(tmp_path: Path) -> None:
    command = checks.UtilityCommand(("gofmt",), tmp_path, "empty")
    checks.admit_output(command, "")
    with pytest.raises(ValueError, match="formatting"):
        checks.admit_output(command, "main.go\n")


_GOVULNCHECK_UNCALLED = (
    "=== Symbol Results ===\n\n"
    "No vulnerabilities found.\n\n"
    "Your code is affected by 0 vulnerabilities.\n"
    "This scan also found 1 vulnerability in packages you import and 0\n"
    "vulnerabilities in modules you require, but your code doesn't appear to call\n"
    "these vulnerabilities.\n"
    "Use '-show verbose' for more details.\n"
)


@pytest.mark.parametrize(
    "output",
    [
        "No vulnerabilities found.\n",
        _GOVULNCHECK_UNCALLED,
        _GOVULNCHECK_UNCALLED.replace(
            "1 vulnerability in packages you import and 0",
            "0 vulnerabilities in packages you import and 2",
        ),
    ],
)
def test_govulncheck_accepts_complete_clean_symbol_results(tmp_path: Path, output: str) -> None:
    command = checks.UtilityCommand(("govulncheck",), tmp_path, "govulncheck")
    checks.admit_output(command, output)


@pytest.mark.parametrize(
    "output",
    [
        "",
        " \n",
        "{}\n",
        "Usage: govulncheck [flags] [patterns]\n",
        "Scanner: govulncheck@v1.8.0\n",
        "No vulnerabilities",
        "No vulnerabilities found.\nNo vulnerabilities found.\n",
        "Usage: govulncheck [flags] [patterns]\nNo vulnerabilities found.\n",
        "No vulnerabilities found.\nfailed to load packages\n",
        _GOVULNCHECK_UNCALLED.replace("affected by 0", "affected by 1"),
        _GOVULNCHECK_UNCALLED.replace("Use '-show verbose' for more details.\n", ""),
        _GOVULNCHECK_UNCALLED.replace("1 vulnerability in", "0 vulnerabilities in"),
        _GOVULNCHECK_UNCALLED.replace("1 vulnerability in", "1 vulnerabilities in"),
    ],
)
def test_govulncheck_rejects_missing_malformed_or_incomplete_results(
    tmp_path: Path, output: str
) -> None:
    command = checks.UtilityCommand(("govulncheck",), tmp_path, "govulncheck")
    with pytest.raises(ValueError, match="complete clean symbol-scan"):
        checks.admit_output(command, output)


def test_zero_exit_govulncheck_without_report_fails_the_utility_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = checks.UtilityCommand(("govulncheck",), tmp_path, "govulncheck")
    monkeypatch.setattr(checks, "commands", lambda *_args: (command,))
    monkeypatch.setattr(checks, "spawn", lambda *_args, **_kwargs: CommandResult(0, "", ""))
    with pytest.raises(ValueError, match="complete clean symbol-scan"):
        checks.run(tmp_path, "govulncheck", {"PATH": "/usr/bin:/bin"})


def test_govulncheck_findings_exit_cannot_be_overridden_by_clean_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = checks.UtilityCommand(("govulncheck",), tmp_path, "govulncheck")
    monkeypatch.setattr(checks, "commands", lambda *_args: (command,))
    monkeypatch.setattr(
        checks,
        "spawn",
        lambda *_args, **_kwargs: CommandResult(3, "No vulnerabilities found.\n", ""),
    )
    assert checks.run(tmp_path, "govulncheck", {"PATH": "/usr/bin:/bin"}) == 3


def test_spelling_keeps_historical_docs_and_skips_only_declared_generated_assets(
    repository: Path,
) -> None:
    commands = checks.commands(repository, "spelling")
    assert any(str(repository / "docs/old-note.md") in command.argv for command in commands)
    assert all(command.cwd == repository / "tooling/quality" for command in commands[1:])
    assert all(
        command.argv[command.argv.index("--ignore-words") + 1]
        == str(repository / "tooling/quality/spelling-words.dic")
        for command in commands[1:]
    )
    assert inventory.spelling_exclusion("docs/old-note.md") is None
    assert inventory.spelling_exclusion("frontend/src/new.ts") is None
    assert inventory.spelling_exclusion("pnpm-lock.yaml") == "generated dependency resolution data"
    assert inventory.spelling_exclusion("font.woff2") == "binary asset"


def test_editor_dictionary_reuses_the_ci_vocabulary() -> None:
    root = Path(__file__).resolve().parents[2]
    configuration = json.loads((root / "cspell.json").read_text(encoding="utf-8"))
    assert configuration == {
        "version": "0.2",
        "dictionaryDefinitions": [
            {
                "name": "ci-coordinator-terms",
                "path": "./tooling/quality/spelling-words.dic",
                "addWords": True,
            }
        ],
        "dictionaries": ["ci-coordinator-terms"],
    }
    words = (root / "tooling/quality/spelling-words.dic").read_text(encoding="utf-8").splitlines()
    assert words and words == sorted(set(words))
    assert all(word.isascii() and word.isalpha() and word == word.lower() for word in words)
    assert not (root / "tooling/quality/spelling-words.txt").exists()


def test_spelling_exception_matches_one_whole_line_in_one_exact_file(tmp_path: Path) -> None:
    name = "scripts/dev_environment/log_transport.py"
    excluded = "tooling/quality/spelling/http-header.txt"
    _write(tmp_path, name, '    "connection",\nneighbor_with_typo\n')
    _write(tmp_path, excluded, '    "connection",\n')
    assert inventory.spelling_line_file(tmp_path, name) == excluded
    assert "neighbor_with_typo" not in (tmp_path / excluded).read_text()
    assert inventory.spelling_line_file(tmp_path, "different/log_transport.py") is None
    _write(tmp_path, name, '    "connection",\n    "connection",\n')
    with pytest.raises(ValueError, match="widened"):
        inventory.spelling_line_file(tmp_path, name)
    _write(tmp_path, name, '    "changed",\n')
    with pytest.raises(ValueError, match="stale"):
        inventory.spelling_line_file(tmp_path, name)


def test_compose_overlay_exclusion_has_exact_path_and_native_owner(tmp_path: Path) -> None:
    _write(
        tmp_path, "docker/development/compose.debug.yaml", "services: {app: {develop: !reset {}}}"
    )
    _write(tmp_path, "compose.yaml", "services: {}")
    _write(tmp_path, "other/compose.debug.yaml", "services: {}")
    groups = dict(
        inventory.schema_groups(
            tmp_path,
            ("docker/development/compose.debug.yaml", "compose.yaml", "other/compose.debug.yaml"),
        )
    )
    assert groups["vendor.compose-spec"] == ("compose.yaml", "other/compose.debug.yaml")
    assert (
        "debug_witness.py" in inventory.SCHEMA_EXCLUSIONS["docker/development/compose.debug.yaml"]
    )


def test_pinned_cli_version_output_includes_click_version_label(repository: Path) -> None:
    command = checks.commands(repository, "schemas")[0]
    checks.admit_output(command, "check-jsonschema, version 0.38.0\n")
    with pytest.raises(ValueError, match="version"):
        checks.admit_output(command, "check-jsonschema, version 0.37.0\n")


def test_environment_drops_ambient_tool_configuration_and_secrets() -> None:
    environment = checks.project_environment(
        {
            "PATH": "/usr/bin:/bin",
            "HOME": "/home/runner",
            "GITHUB_TOKEN": "secret",
            "PYTHONPATH": "/evil",
            "GIT_DIR": "/evil",
            "HADOLINT_NOFAIL": "1",
            "GOFLAGS": "-tags=omit",
            "GOTOOLCHAIN": "auto",
            "GOVULNDB": "https://untrusted.invalid",
            "BUILDX_BUILDER": "other",
        }
    )
    assert environment["GOFLAGS"] == "-mod=readonly"
    assert environment["GOTOOLCHAIN"] == "local"
    assert environment["GOWORK"] == "off"
    assert environment["GOOS"] == "linux"
    assert (
        not {
            "GITHUB_TOKEN",
            "PYTHONPATH",
            "GIT_DIR",
            "HADOLINT_NOFAIL",
            "GOVULNDB",
            "BUILDX_BUILDER",
        }
        & environment.keys()
    )


@pytest.mark.parametrize("path", ["", ".:/bin", "/bin:"])
def test_relative_or_empty_executable_search_path_is_rejected(path: str) -> None:
    with pytest.raises(ValueError, match="PATH"):
        checks.project_environment({"PATH": path})


def test_exact_hadolint_exception_is_source_bound_and_cannot_hide_another_warning(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "Dockerfile", "USER node\n")
    allowance = {
        "file": "Dockerfile",
        "line": 1,
        "level": "info",
        "code": "DL3066",
        "source": "USER node",
        "reason": "Pinned image defines the account.",
        "sha256": hashlib.sha256(b"USER node\n").hexdigest(),
    }
    _write(tmp_path, "tooling/quality/hadolint-exceptions.json", json.dumps([allowance]))
    diagnostic = {
        key: value for key, value in allowance.items() if key not in {"source", "reason", "sha256"}
    }
    diagnostic.update(column=1, message="Non-numeric user-id")
    command = checks.UtilityCommand(("hadolint",), tmp_path, "hadolint")
    checks.admit_output(command, json.dumps([diagnostic]))
    with pytest.raises(ValueError, match="unadmitted"):
        checks.admit_output(command, json.dumps([diagnostic, diagnostic]))
    _write(tmp_path, "Dockerfile", "USER arbitrary\n")
    with pytest.raises(ValueError, match="stale"):
        checks.admit_output(command, json.dumps([diagnostic]))


def test_hadolint_native_no_fail_mode_does_not_admit_a_parser_error(repository: Path) -> None:
    command = checks.commands(repository, "hadolint")[-1]
    assert "--no-fail" in command.argv
    error = {
        "code": "DL1000",
        "column": 1,
        "file": "Dockerfile",
        "level": "error",
        "line": 1,
        "message": "unexpected Dockerfile instruction",
    }
    with pytest.raises(ValueError, match="unadmitted Hadolint diagnostic"):
        checks.admit_output(command, json.dumps([error]))


@pytest.mark.parametrize(
    "result",
    [
        CommandResult(7, "", "failed"),
        CommandResult(None, "", "", error="timeout"),
        CommandResult(-15, "", ""),
    ],
)
def test_runner_preserves_failure_and_stops_before_later_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: CommandResult
) -> None:
    observed = []
    monkeypatch.setattr(
        checks,
        "commands",
        lambda *_args: (
            checks.UtilityCommand(("first",), tmp_path),
            checks.UtilityCommand(("second",), tmp_path),
        ),
    )

    def spawn(binary: str, argv: object, **options: object) -> CommandResult:
        observed.append((binary, argv, options))
        return result

    monkeypatch.setattr(checks, "spawn", spawn)
    assert checks.run(tmp_path, "yaml", {"PATH": "/bin"}) != 0
    assert len(observed) == 1
    assert observed[0][2]["max_buffer"] == 16 * 1024 * 1024
    assert observed[0][2]["timeout_seconds"] == 600.0


def test_version_contract_rejects_drift_and_accepts_pinned_output(tmp_path: Path) -> None:
    command = checks.UtilityCommand(
        ("yamllint", "--version"), tmp_path, "version", r"yamllint 1\.38\.0"
    )
    checks.admit_output(command, "yamllint 1.38.0\n")
    with pytest.raises(ValueError, match="version"):
        checks.admit_output(command, "yamllint 1.39.0\n")
