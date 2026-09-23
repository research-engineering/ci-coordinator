from __future__ import annotations

import copy
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from ci_coordinator.consumer_contract_lab import bootstrap, git_source
from ci_coordinator.consumer_contract_lab.codec import (
    ConsumerLabAdmissionError,
    parse_consumer_lab_profile,
    parse_scenario_corpus,
)
from ci_coordinator.consumer_contract_lab.model import (
    ChangeStatus,
    ConsumerLabProfile,
    ConsumerLabScenario,
    ConsumerRepository,
    EventName,
    ExpectedMode,
    ExpectedOutcome,
    FileBinding,
    GitSourceSnapshot,
    ManifestEntry,
    ScenarioChange,
    ScenarioCorpus,
    SourceKind,
)
from ci_coordinator.consumer_contract_lab.process import run_bounded
from ci_coordinator.kernel import canonical_json

_COMMIT = "a" * 40
_DIGEST = "b" * 64
_REPOSITORY = ConsumerRepository(1, 2, "example-org", "consumer", "master")
_POLICY = FileBinding("dynamic-ci-policy.v1.json", _DIGEST)
_CORPUS = FileBinding("local-lab-scenarios.v1.json", "c" * 64)
_CHANGE = ScenarioChange("src/service.py", "modified", None)
_OUTCOME = ExpectedOutcome("selected", ("test",))
_SCENARIO = ConsumerLabScenario("source-change", "pull_request", (_CHANGE,), _OUTCOME)
_PROFILE = ConsumerLabProfile(
    "native-consumer",
    _COMMIT,
    _REPOSITORY,
    ".github/workflows/full-check.yml",
    ("pull_request",),
    ".ci-coordinator",
    _POLICY,
    _CORPUS,
    (),
)
_SCENARIO_CORPUS = ScenarioCorpus("native-consumer", (_SCENARIO,))

type _Factory = Callable[[], object]
type _JsonPath = tuple[str | int, ...]


def _invalid_model_cases() -> tuple[tuple[str, _Factory], ...]:
    many_bindings = tuple(FileBinding(f"inputs/{index}.json", _DIGEST) for index in range(33))
    many_changes = (_CHANGE,) * 1_001
    many_scenarios = (_SCENARIO,) * 129
    many_jobs = tuple(f"job-{index}" for index in range(65))
    return (
        ("binding-path", lambda: FileBinding("../escape", _DIGEST)),
        ("binding-digest", lambda: FileBinding("input.json", "B" * 64)),
        ("installation-type", lambda: replace(_REPOSITORY, installation_id=True)),
        ("repository-id-bound", lambda: replace(_REPOSITORY, repository_id=0)),
        ("owner-shape", lambda: replace(_REPOSITORY, owner="-invalid")),
        ("repository-shape", lambda: replace(_REPOSITORY, name="")),
        ("default-branch-text", lambda: replace(_REPOSITORY, default_branch="\x00")),
        ("default-branch-git", lambda: replace(_REPOSITORY, default_branch="HEAD")),
        ("profile-id", lambda: replace(_PROFILE, profile_id="INVALID")),
        ("coordinator-commit", lambda: replace(_PROFILE, expected_coordinator_commit="A" * 40)),
        (
            "repository-type",
            lambda: replace(_PROFILE, repository=cast(ConsumerRepository, object())),
        ),
        ("workflow-path", lambda: replace(_PROFILE, workflow_path="full-check.yml")),
        (
            "event-surface-type",
            lambda: replace(
                _PROFILE,
                event_surface=cast(tuple[EventName, ...], ["pull_request"]),
            ),
        ),
        ("event-surface-empty", lambda: replace(_PROFILE, event_surface=())),
        (
            "event-surface-duplicate",
            lambda: replace(_PROFILE, event_surface=("pull_request", "pull_request")),
        ),
        (
            "event-surface-value",
            lambda: replace(
                _PROFILE,
                event_surface=(cast(EventName, "workflow_dispatch"),),
            ),
        ),
        (
            "artifacts-directory",
            lambda: replace(_PROFILE, target_artifacts_directory="../artifacts"),
        ),
        (
            "dynamic-policy-type",
            lambda: replace(_PROFILE, dynamic_policy=cast(FileBinding, object())),
        ),
        (
            "scenario-corpus-type",
            lambda: replace(_PROFILE, scenario_corpus=cast(FileBinding, object())),
        ),
        (
            "target-bindings-type",
            lambda: replace(
                _PROFILE,
                target_bindings=cast(tuple[FileBinding, ...], []),
            ),
        ),
        ("target-bindings-bound", lambda: replace(_PROFILE, target_bindings=many_bindings)),
        (
            "target-bindings-canonical",
            lambda: replace(
                _PROFILE,
                target_bindings=(
                    FileBinding("inputs/z.json", _DIGEST),
                    FileBinding("inputs/a.json", _DIGEST),
                ),
            ),
        ),
        ("target-bindings-duplicate", lambda: replace(_PROFILE, target_bindings=(_POLICY,))),
        ("change-path", lambda: replace(_CHANGE, path="../service.py")),
        (
            "change-status",
            lambda: replace(_CHANGE, status=cast(ChangeStatus, "deleted")),
        ),
        ("previous-path", lambda: replace(_CHANGE, previous_path="../old.py")),
        (
            "renamed-previous-path",
            lambda: ScenarioChange("src/service.py", "renamed", None),
        ),
        (
            "modified-previous-path",
            lambda: replace(_CHANGE, previous_path="src/old.py"),
        ),
        (
            "outcome-mode",
            lambda: replace(_OUTCOME, mode=cast(ExpectedMode, "unknown")),
        ),
        (
            "selected-jobs-type",
            lambda: replace(_OUTCOME, selected_jobs=cast(tuple[str, ...], ["test"])),
        ),
        ("selected-jobs-bound", lambda: replace(_OUTCOME, selected_jobs=many_jobs)),
        ("selected-job-shape", lambda: replace(_OUTCOME, selected_jobs=("invalid job",))),
        ("selected-job-order", lambda: replace(_OUTCOME, selected_jobs=("test", "lint"))),
        ("selected-mode-empty", lambda: replace(_OUTCOME, selected_jobs=())),
        ("fallback-mode-nonempty", lambda: ExpectedOutcome("fallback", ("test",))),
        ("scenario-id", lambda: replace(_SCENARIO, scenario_id="INVALID")),
        (
            "scenario-event",
            lambda: replace(_SCENARIO, event_name=cast(EventName, "workflow_dispatch")),
        ),
        (
            "scenario-changes-type",
            lambda: replace(
                _SCENARIO,
                changes=cast(tuple[ScenarioChange, ...], []),
            ),
        ),
        ("scenario-changes-bound", lambda: replace(_SCENARIO, changes=many_changes)),
        (
            "scenario-change-type",
            lambda: replace(
                _SCENARIO,
                changes=(cast(ScenarioChange, object()),),
            ),
        ),
        ("scenario-changes-duplicate", lambda: replace(_SCENARIO, changes=(_CHANGE, _CHANGE))),
        (
            "scenario-outcome-type",
            lambda: replace(
                _SCENARIO,
                expected_outcome=cast(ExpectedOutcome, object()),
            ),
        ),
        ("corpus-id", lambda: replace(_SCENARIO_CORPUS, profile_id="INVALID")),
        (
            "corpus-scenarios-type",
            lambda: replace(
                _SCENARIO_CORPUS,
                scenarios=cast(tuple[ConsumerLabScenario, ...], []),
            ),
        ),
        ("corpus-scenarios-empty", lambda: replace(_SCENARIO_CORPUS, scenarios=())),
        ("corpus-scenarios-bound", lambda: replace(_SCENARIO_CORPUS, scenarios=many_scenarios)),
        (
            "corpus-scenario-type",
            lambda: replace(
                _SCENARIO_CORPUS,
                scenarios=(cast(ConsumerLabScenario, object()),),
            ),
        ),
        (
            "corpus-scenarios-duplicate",
            lambda: replace(_SCENARIO_CORPUS, scenarios=(_SCENARIO, _SCENARIO)),
        ),
        ("snapshot-head", lambda: GitSourceSnapshot("A" * 40, (), "commit")),
        (
            "snapshot-paths-type",
            lambda: replace(
                GitSourceSnapshot(_COMMIT, (), "commit"),
                dirty_paths=cast(tuple[str, ...], []),
            ),
        ),
        (
            "snapshot-paths-canonical",
            lambda: GitSourceSnapshot(_COMMIT, ("b.py", "a.py"), "worktree"),
        ),
        ("snapshot-paths-safe", lambda: GitSourceSnapshot(_COMMIT, ("../a.py",), "worktree")),
        (
            "snapshot-kind",
            lambda: GitSourceSnapshot(_COMMIT, (), cast(SourceKind, "unknown")),
        ),
        ("snapshot-commit-dirty", lambda: GitSourceSnapshot(_COMMIT, ("a.py",), "commit")),
        ("snapshot-worktree-clean", lambda: GitSourceSnapshot(_COMMIT, (), "worktree")),
        ("manifest-path", lambda: ManifestEntry("../input", _DIGEST, 1)),
        ("manifest-digest", lambda: ManifestEntry("input", "B" * 64, 1)),
        ("manifest-size-type", lambda: ManifestEntry("input", _DIGEST, True)),
        ("manifest-size-negative", lambda: ManifestEntry("input", _DIGEST, -1)),
    )


_INVALID_MODEL_CASES = _invalid_model_cases()


@pytest.mark.parametrize(
    "factory",
    [factory for _, factory in _INVALID_MODEL_CASES],
    ids=[case_id for case_id, _ in _INVALID_MODEL_CASES],
)
def test_consumer_contract_models_reject_values_outside_the_finite_language(
    factory: _Factory,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory()


@pytest.mark.parametrize(
    ("kind", "path", "value"),
    [
        ("profile", ("schemaVersion",), "unsupported"),
        ("profile", ("repository", "installationId"), "1"),
        ("profile", ("eventSurface",), "pull_request"),
        ("profile", ("eventSurface", 0), 1),
        ("corpus", ("schemaVersion",), "unsupported"),
        ("corpus", ("scenarios",), {}),
        ("corpus", ("scenarios", 0, "eventName"), "workflow_dispatch"),
        ("corpus", ("scenarios", 0, "expectedOutcome", "mode"), "unknown"),
        ("corpus", ("scenarios", 0, "changes", 0, "status"), "deleted"),
    ],
)
def test_consumer_contract_codecs_reject_structural_and_algebraic_escape(
    kind: str,
    path: _JsonPath,
    value: object,
) -> None:
    document = _PROFILE.to_mapping() if kind == "profile" else _SCENARIO_CORPUS.to_mapping()
    content = _mutated_json(document, path, value)
    parser = parse_consumer_lab_profile if kind == "profile" else parse_scenario_corpus

    with pytest.raises(ConsumerLabAdmissionError):
        parser(content)


@pytest.mark.parametrize(
    "content",
    [
        b"not-terminated",
        b"malformed\x00",
        b"100644 blob " + b"a" * 40 + b"\t\xff\x00",
        b"100644 blob invalid\tbackend/src/ci_coordinator/a.py\x00",
        b"040000 tree " + b"a" * 40 + b"\tbackend/src/ci_coordinator/a.py\x00",
    ],
)
def test_git_tree_admission_rejects_malformed_or_special_entries(content: bytes) -> None:
    with pytest.raises(git_source.GitSourceError):
        git_source._parse_tree_blob_ids(content, require_regular=True)


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"malformed\x00",
        b"100644 blob " + b"a" * 40 + b" x\tbackend/src/ci_coordinator/a.py\x00",
        b"040000 tree " + b"a" * 40 + b" 1\tbackend/src/ci_coordinator/a.py\x00",
        b"100644 blob " + b"a" * 40 + b" 16777217\tbackend/src/ci_coordinator/a.py\x00",
        (b"100644 blob " + b"a" * 40 + b" 1\tbackend/src/ci_coordinator/a.py\x00") * 2,
    ],
)
def test_bootstrap_rejects_untrusted_package_inventory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    content: bytes,
) -> None:
    monkeypatch.setattr(bootstrap, "_git", lambda *_args, **_kwargs: content)

    with pytest.raises(bootstrap.ConsumerLabBootstrapError):
        bootstrap._package_blobs(tmp_path, _COMMIT)


@pytest.mark.parametrize("content", [b"\xff", b"not-a-commit\n"])
def test_bootstrap_rejects_noncanonical_commit_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    content: bytes,
) -> None:
    monkeypatch.setattr(bootstrap, "_git", lambda *_args, **_kwargs: content)

    with pytest.raises(bootstrap.ConsumerLabBootstrapError):
        bootstrap._commit(tmp_path)


def test_bootstrap_main_classifies_unavailable_repository(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = bootstrap.main(
        (
            "--coordinator-root",
            str(tmp_path / "missing"),
            "--target-root",
            str(tmp_path),
            "--profile",
            "profile.json",
            "--output",
            str(tmp_path / "receipt.json"),
        )
    )

    assert result == 2
    assert capsys.readouterr().err == (
        '{"code":"consumer_contract_lab_bootstrap_failed","detail":"ConsumerLabBootstrapError"}\n'
    )


@pytest.mark.parametrize(
    ("command", "max_output_bytes", "timeout_seconds"),
    [
        ("", 1, 1.0),
        (sys.executable, 0, 1.0),
        (sys.executable, True, 1.0),
        (sys.executable, 1, 0.0),
        (sys.executable, 1, float("nan")),
    ],
)
def test_bounded_process_rejects_invalid_resource_contract(
    tmp_path: Path,
    command: str,
    max_output_bytes: int,
    timeout_seconds: float,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        run_bounded(
            command,
            (),
            cwd=tmp_path,
            max_output_bytes=max_output_bytes,
            timeout_seconds=timeout_seconds,
            env={},
        )


def test_bounded_process_classifies_spawn_failure(tmp_path: Path) -> None:
    result = run_bounded(
        str(tmp_path / "missing-executable"),
        (),
        cwd=tmp_path,
        max_output_bytes=1,
        timeout_seconds=1,
        env={},
    )

    assert result.status is None
    assert result.error == "missing-executable: ENOENT"


@pytest.mark.parametrize(
    ("relative", "content"),
    [
        ("../unsafe", None),
        ("empty", b""),
        ("nul", b"\x00"),
    ],
)
def test_regular_contract_reader_rejects_unsafe_or_nontext_evidence(
    tmp_path: Path,
    relative: str,
    content: bytes | None,
) -> None:
    if content is not None:
        (tmp_path / relative).write_bytes(content)

    with pytest.raises(git_source.GitSourceError):
        git_source.read_regular(tmp_path, relative)


def _mutated_json(document: dict[str, object], path: _JsonPath, value: object) -> bytes:
    mutated = copy.deepcopy(document)
    cursor: object = mutated
    for component in path[:-1]:
        if type(cursor) is dict and type(component) is str:
            cursor = cast(dict[str, object], cursor)[component]
        elif type(cursor) is list and type(component) is int:
            cursor = cast(list[object], cursor)[component]
        else:
            raise AssertionError("JSON mutation path does not match the document")
    leaf = path[-1]
    if type(cursor) is dict and type(leaf) is str:
        cast(dict[str, object], cursor)[leaf] = value
    elif type(cursor) is list and type(leaf) is int:
        cast(list[object], cursor)[leaf] = value
    else:
        raise AssertionError("JSON mutation leaf does not match the document")
    return canonical_json(mutated) + b"\n"
