from __future__ import annotations

import copy
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from scripts.bounded_process import CommandResult
from scripts.mutation import mutation_evidence, mutation_suite_runner
from scripts.mutation.detached_worktree_lifecycle import CleanupResult, DetachedWorktreeLifecycle
from scripts.mutation.mutation_evidence import EvidenceCapture, allocate_capture, capture_phase
from scripts.mutation.mutation_manifest import Mutant, MutationManifest

_SOURCE = "backend/src/ci_coordinator/consumer_contract_lab/process.py"
_XML = (
    b'<testsuites><testsuite tests="1" errors="0" failures="0" skipped="0">'
    b'<testcase file="test.py" classname="test" name="test_owned" /></testsuite></testsuites>'
)
_FAILED = _XML.replace(b'failures="0"', b'failures="1"').replace(
    b" />", b"><failure>assert actual == expected</failure></testcase>"
)
_IDS = (
    "leaf-fd",
    "image-fd",
    "historical-gate",
    "stop-bound",
    "fresh-channel",
    "scope-restore",
    "cleanup-cut",
    "empty-only",
    "quiescence-fact",
)


def _inputs(root: Path) -> dict[str, Any]:
    worktree = root / "worktree"
    source = worktree / _SOURCE
    source.parent.mkdir(parents=True)
    source.write_bytes(b"before\n")
    report = worktree / ".mutation-witness/pytest-report.xml"
    report.parent.mkdir()
    report.write_bytes(_XML)
    return {
        "worktree": worktree,
        "mutant_id": "leaf-fd",
        "phase": "baseline",
        "source_file": _SOURCE,
        "source_digest": hashlib.sha256(b"before\n").hexdigest(),
        "patch_digest": "c" * 64,
        "command": ["python", "-m", "pytest", "test.py::test_owned"],
        "report_digest": hashlib.sha256(_XML).hexdigest(),
        "stdout": "passed\n",
        "stderr": "",
        "exit_code": 0,
    }


def test_phase_capture_binds_exact_files_without_success_output_duplication(tmp_path: Path) -> None:
    arguments = _inputs(tmp_path)
    capture = allocate_capture(tmp_path, "a" * 40, "b" * 64)
    result = capture_phase(capture, **arguments)
    assert result["state"] == "captured"
    assert result["sourceRevision"] == "a" * 40
    assert result["manifestDigest"] == "b" * 64
    assert result["patchDigest"] == "c" * 64
    assert result["invocationId"] == capture.invocation_id
    assert result["sourceSha256"] == arguments["source_digest"]
    directory = tmp_path / capture.relative_directory / "leaf-fd/baseline"
    assert (directory / "pytest.xml").read_bytes() == _XML
    record = json.loads((directory / "capture.json").read_bytes())
    assert record["phase"] == "baseline" and record["mutantId"] == "leaf-fd"
    assert record["invocationId"] == capture.invocation_id
    assert record["command"] == arguments["command"]
    assert record["output"]["stdout"] == {
        "bytes": 7,
        "sha256": hashlib.sha256(b"passed\n").hexdigest(),
        "retained": False,
    }
    assert not (directory / "stdout.txt").exists()
    assert record["files"] == [
        {
            "path": (capture.relative_directory / "leaf-fd/baseline/pytest.xml").as_posix(),
            "bytes": len(_XML),
            "sha256": hashlib.sha256(_XML).hexdigest(),
        }
    ]
    assert (directory / "pytest.xml").stat().st_mode & 0o777 == 0o600
    assert capture_phase(capture, **arguments)["state"] == "unqualified"
    assert (directory / "pytest.xml").read_bytes() == _XML


def test_concurrent_invocations_allocate_distinct_owned_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    barrier = Barrier(2)
    original = uuid4

    def concurrent_uuid() -> UUID:
        barrier.wait(timeout=10)
        return original()

    monkeypatch.setattr(mutation_evidence, "uuid4", concurrent_uuid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        contexts = list(
            pool.map(lambda _: allocate_capture(tmp_path, "a" * 40, "b" * 64), range(2))
        )
    assert len({context.invocation_id for context in contexts}) == 2
    for context in contexts:
        assert re.fullmatch(r"[0-9a-f]{32}", context.invocation_id)
        assert context.relative_directory.parent == Path(
            ".ci-native/mutations/python-managed-lifecycle"
        )
        directory = tmp_path / context.relative_directory
        assert directory.is_dir() and not directory.is_symlink()
        assert directory.stat().st_mode & 0o777 == 0o700


def test_invocation_collision_preserves_the_first_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = _inputs(tmp_path)
    nonce = uuid4()
    monkeypatch.setattr(mutation_evidence, "uuid4", lambda: nonce)
    first = allocate_capture(tmp_path, "a" * 40, "b" * 64)
    assert capture_phase(first, **arguments)["state"] == "captured"
    record = tmp_path / first.relative_directory / "leaf-fd/baseline/capture.json"
    before = record.read_bytes()
    with pytest.raises(FileExistsError):
        allocate_capture(tmp_path, "d" * 40, "b" * 64)
    assert record.read_bytes() == before


@pytest.mark.parametrize(
    "fault",
    [
        "id",
        "phase",
        "source-path",
        "source-digest",
        "report-digest",
        "revision",
        "manifest",
        "invocation",
        "missing-invocation",
        "missing",
        "overflow",
        "source-symlink",
        "report-symlink",
        "output-overflow",
        "parent-symlink",
    ],
)
def test_capture_rejects_identity_path_missing_and_overflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    arguments = _inputs(tmp_path)
    capture = allocate_capture(tmp_path, "a" * 40, "b" * 64)
    revision, manifest = "a" * 40, "b" * 64
    invocation = capture.invocation_id
    report = arguments["worktree"] / ".mutation-witness/pytest-report.xml"
    if fault == "id":
        arguments["mutant_id"] = "../outside"
    elif fault == "phase":
        arguments["phase"] = "../../outside"
    elif fault == "source-path":
        arguments["source_file"] = "../outside"
    elif fault == "source-digest":
        arguments["source_digest"] = "f" * 64
    elif fault == "report-digest":
        arguments["report_digest"] = "f" * 64
    elif fault == "revision":
        revision = "../outside"
    elif fault == "manifest":
        manifest = "invalid"
    elif fault == "invocation":
        invocation = "../outside"
    elif fault == "missing-invocation":
        (tmp_path / capture.relative_directory).rmdir()
    elif fault == "missing":
        report.unlink()
    elif fault == "overflow":
        monkeypatch.setattr(mutation_evidence, "_MAXIMUM_REPORT_BYTES", len(_XML) - 1)
    elif fault == "source-symlink":
        source = arguments["worktree"] / _SOURCE
        source.unlink()
        source.symlink_to(report)
    elif fault == "report-symlink":
        report.unlink()
        report.symlink_to(arguments["worktree"] / _SOURCE)
    elif fault == "output-overflow":
        monkeypatch.setattr(mutation_evidence, "DEFAULT_MAX_BUFFER_BYTES", 6)
    else:
        outside = tmp_path / "outside"
        outside.mkdir()
        (tmp_path / ".ci-native").rename(tmp_path / "retained-capture-root")
        (tmp_path / ".ci-native").symlink_to(outside, target_is_directory=True)
    result = capture_phase(
        replace(
            capture, source_revision=revision, manifest_digest=manifest, invocation_id=invocation
        ),
        **arguments,
    )
    assert result["state"] == "unqualified"
    assert not list(tmp_path.rglob("capture.json"))


def test_capture_does_not_swallow_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments = _inputs(tmp_path)
    capture = allocate_capture(tmp_path, "a" * 40, "b" * 64)
    primary = KeyboardInterrupt("owned cancellation")

    def cancelled(*_args: object, **_kwargs: object) -> bytes:
        raise primary

    monkeypatch.setattr(mutation_evidence, "read_repository_regular_file", cancelled)
    with pytest.raises(KeyboardInterrupt) as caught:
        capture_phase(capture, **arguments)
    assert caught.value is primary


@pytest.mark.parametrize("retain", [False, True])
def test_runner_captures_each_phase_before_overwrite_and_patch_restoration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retain: bool,
) -> None:
    arguments = _inputs(tmp_path)
    worktree = arguments["worktree"]
    calls: list[bytes] = []
    target = worktree / _SOURCE
    report = worktree / ".mutation-witness/pytest-report.xml"
    capture = allocate_capture(tmp_path, "a" * 40, "b" * 64) if retain else None
    relative = capture.relative_directory if capture else mutation_evidence.EVIDENCE_ROOT
    directory = tmp_path / relative / "leaf-fd"

    class Lifecycle:
        def __init__(self) -> None:
            self.worktree = worktree

        def run(self, *_args: object, **_kwargs: object) -> CommandResult:
            calls.append(target.read_bytes())
            if len(calls) == 2 and retain:
                assert (directory / "baseline/pytest.xml").read_bytes() == _XML
            report.write_bytes(_XML if len(calls) == 1 else _FAILED)
            return CommandResult(0 if len(calls) == 1 else 1, "assert actual == expected\n", "")

        def assert_running(self) -> None:
            pass

    if not retain:

        def forbidden(*_args: object, **_kwargs: object) -> dict[str, object]:
            pytest.fail("default-off capture changed execution")

        monkeypatch.setattr(mutation_suite_runner, "capture_phase", forbidden)
    mutant: Mutant = {
        "id": "leaf-fd",
        "file": _SOURCE,
        "operator": "fixture",
        "original": "before",
        "replacement": "after",
        "witnessId": "fixture",
        "requirementIds": ["fixture"],
        "command": ["python", "-m", "pytest", "test.py::test_owned"],
    }
    manifest: MutationManifest = {
        "mutants": [mutant],
        "expectedKilled": 1,
        "expectedMutantIds": ["leaf-fd"],
        "timeoutMs": 1000,
        "outerTimeoutMs": 62000,
    }
    result = mutation_suite_runner._run_mutant(
        cast(DetachedWorktreeLifecycle, Lifecycle()),
        manifest,
        mutant,
        capture=capture,
    )
    assert result["status"] == "killed" and target.read_bytes() == b"before\n"
    assert calls == [b"before\n", b"after\n"]
    if retain:
        report.unlink()
        assert (directory / "baseline/pytest.xml").read_bytes() == _XML
        assert (directory / "mutant/pytest.xml").read_bytes() == _FAILED
        retained = json.loads((directory / "mutant/capture.json").read_bytes())
        assert retained["sourceSha256"] == hashlib.sha256(b"after\n").hexdigest()
        assert retained["patchDigest"] == result["patchDigest"]
        assert (directory / "mutant/stdout.txt").read_bytes() == b"assert actual == expected\n"
    else:
        assert "retainedEvidence" not in result
        assert "retainedEvidence" not in cast(dict[str, object], result["baseline"])
        assert not (tmp_path / ".ci-native").exists()


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "missing",
        "duplicate",
        "wrong-id",
        "wrong-phase",
        "unqualified",
        "foreign-invocation",
        "foreign-source",
        "foreign-manifest",
        "missing-context",
    ],
)
def test_retention_gate_requires_all_exact_id_phase_pairs(fault: str) -> None:
    capture = EvidenceCapture(Path("."), "a" * 40, "b" * 64, "d" * 32)
    identity = {"invocationId": "d" * 32, "sourceRevision": "a" * 40, "manifestDigest": "b" * 64}
    rows: list[dict[str, object]] = [
        {
            "id": item,
            "baseline": {
                "retainedEvidence": {
                    "state": "captured",
                    "mutantId": item,
                    "phase": "baseline",
                    **identity,
                }
            },
            "retainedEvidence": {
                "state": "captured",
                "mutantId": item,
                "phase": "mutant",
                **identity,
            },
        }
        for item in _IDS
    ]
    if fault == "missing":
        rows.pop()
    elif fault == "duplicate":
        rows[-1] = copy.deepcopy(rows[0])
    elif fault not in {"none", "missing-context"}:
        evidence = cast(dict[str, object], rows[-1]["retainedEvidence"])
        evidence[
            {
                "wrong-id": "mutantId",
                "wrong-phase": "phase",
                "unqualified": "state",
                "foreign-invocation": "invocationId",
                "foreign-source": "sourceRevision",
                "foreign-manifest": "manifestDigest",
            }[fault]
        ] = "e" * (32 if fault == "foreign-invocation" else 40 if fault == "foreign-source" else 64)
    result = mutation_suite_runner._retention_summary(
        rows,
        None if fault == "missing-context" else capture,
    )
    assert result["state"] == ("complete" if fault == "none" else "unqualified")
    assert result["expectedPhases"] == 18
    assert result["invocationId"] == (None if fault == "missing-context" else "d" * 32)


def _write_suite_manifest(root: Path) -> None:
    manifest_path = root / mutation_evidence.MANIFEST_PATH
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "expectedKilled": 9,
                "expectedMutantIds": list(_IDS),
                "timeoutMs": 1000,
                "outerTimeoutMs": 78000,
                "mutants": [
                    {
                        "id": item,
                        "file": _SOURCE,
                        "operator": "fixture",
                        "original": "before",
                        "replacement": "after",
                        "witnessId": item,
                        "requirementIds": ["fixture"],
                        "command": ["python", "-m", "pytest", "test.py::test_owned"],
                    }
                    for item in _IDS
                ],
            }
        )
    )


@pytest.mark.parametrize("second_revision", ["a" * 40, "d" * 40], ids=["same-head", "new-head"])
def test_two_complete_suite_invocations_keep_all_phases_separate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    second_revision: str,
) -> None:
    arguments = _inputs(tmp_path)
    worktree = arguments["worktree"]
    _write_suite_manifest(worktree)
    suite_root = tmp_path / ".ci-native/mutations/python-managed-lifecycle"
    legacy_record = suite_root / "leaf-fd/baseline/capture.json"
    legacy_record.parent.mkdir(parents=True)
    legacy_record.write_bytes(b"prior retained invocation\n")
    revision = "a" * 40
    phases: list[bytes] = []
    cleanup: list[str] = []

    class Lifecycle:
        received_signal = None

        def __init__(self, **_kwargs: object) -> None:
            self.worktree = worktree

        def install_signal_handlers(self) -> None:
            pass

        def git(self, _arguments: object) -> CommandResult:
            return CommandResult(0, revision, "")

        def add_detached_worktree(self, observed_revision: str) -> None:
            assert observed_revision == revision

        def assert_running(self) -> None:
            pass

        def run(self, *_args: object, **_kwargs: object) -> CommandResult:
            source = (worktree / _SOURCE).read_bytes()
            phases.append(source)
            baseline = source == b"before\n"
            assert baseline or source == b"after\n"
            (worktree / ".mutation-witness/pytest-report.xml").write_bytes(
                _XML if baseline else _FAILED
            )
            return CommandResult(0 if baseline else 1, "assert actual == expected\n", "")

        def cleanup(self) -> CleanupResult:
            cleanup.append("cleanup")
            return CleanupResult("passed", "removed")

        def dispose_signal_handlers(self) -> None:
            cleanup.append("dispose")

    monkeypatch.setattr(mutation_suite_runner, "DetachedWorktreeLifecycle", Lifecycle)
    monkeypatch.setattr(
        mutation_suite_runner, "_assert_authority_matches_head", lambda *_args: None
    )
    config = mutation_suite_runner.MutationSuiteConfig(
        dependencies=(),
        manifest_relative_path=mutation_evidence.MANIFEST_PATH,
        report_id="fixture",
        temp_prefix="fixture-",
        retain_pytest_evidence=True,
    )
    invocations: list[str] = []
    first_files: dict[Path, bytes] = {}
    for revision in ("a" * 40, second_revision):
        report = mutation_suite_runner.run_mutation_suite(config, repo_root=tmp_path)
        assert report["state"] == "passed", report
        retained = cast(dict[str, object], report["retainedEvidence"])
        assert retained["state"] == "complete" and retained["capturedPhases"] == 18
        directory = tmp_path / cast(str, retained["invocationPath"])
        assert directory.parent == tmp_path / ".ci-native/mutations/python-managed-lifecycle"
        records = list(directory.glob("*/*/capture.json"))
        assert len(records) == 18
        assert len(list(directory.glob("*/*/pytest.xml"))) == 18
        for path in records:
            record = json.loads(path.read_bytes())
            assert record["invocationId"] == retained["invocationId"]
            assert record["sourceRevision"] == revision
            assert record["manifestDigest"] == report["manifestDigest"]
        assert all(path.read_bytes() == content for path, content in first_files.items())
        assert legacy_record.read_bytes() == b"prior retained invocation\n"
        if not first_files:
            first_files = {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
        invocations.append(cast(str, retained["invocationId"]))
    assert len(set(invocations)) == 2
    assert sorted(
        p.name for p in suite_root.iterdir() if re.fullmatch(r"[0-9a-f]{32}", p.name)
    ) == sorted(invocations)
    assert phases == [b"before\n", b"after\n"] * 18
    assert cleanup == ["cleanup", "dispose", "cleanup", "dispose"]
    assert (worktree / _SOURCE).read_bytes() == b"before\n"


@pytest.mark.parametrize("retain", [False, True])
def test_requested_capture_failure_suppresses_suite_success_without_reclassifying_kills(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retain: bool,
) -> None:
    _write_suite_manifest(tmp_path)
    source = tmp_path / _SOURCE
    source.parent.mkdir(parents=True)
    source.write_text("before")
    cleanup: list[str] = []

    class Lifecycle:
        received_signal = None

        def __init__(self, **_kwargs: object) -> None:
            self.worktree = tmp_path

        def install_signal_handlers(self) -> None:
            pass

        def git(self, _arguments: object) -> CommandResult:
            return CommandResult(0, "a" * 40, "")

        def add_detached_worktree(self, _revision: str) -> None:
            pass

        def assert_running(self) -> None:
            pass

        def cleanup(self) -> CleanupResult:
            cleanup.append("cleanup")
            return CleanupResult("passed", "removed")

        def dispose_signal_handlers(self) -> None:
            cleanup.append("dispose")

    def witness(
        _lifecycle: object, _manifest: object, mutant: Mutant, **_kwargs: object
    ) -> dict[str, object]:
        return {
            "id": mutant["id"],
            "status": "killed",
            "retainedEvidence": {
                "state": "unqualified",
                "phase": "mutant",
            },
        }

    monkeypatch.setattr(mutation_suite_runner, "DetachedWorktreeLifecycle", Lifecycle)
    monkeypatch.setattr(
        mutation_suite_runner, "_assert_authority_matches_head", lambda *_args: None
    )
    monkeypatch.setattr(mutation_suite_runner, "_run_mutant", witness)
    report = mutation_suite_runner.run_mutation_suite(
        mutation_suite_runner.MutationSuiteConfig(
            dependencies=(),
            manifest_relative_path=mutation_evidence.MANIFEST_PATH,
            report_id="fixture",
            temp_prefix="fixture-",
            retain_pytest_evidence=retain,
        ),
        repo_root=tmp_path,
    )
    assert report["state"] == ("failed" if retain else "passed")
    assert report["summary"] == {
        "expectedKilled": 9,
        "invalidCount": 0,
        "killedCount": 9,
        "survivedCount": 0,
        "totalCount": 9,
    }
    assert ("retainedEvidence" in report) is retain
    assert cleanup == ["cleanup", "dispose"]
