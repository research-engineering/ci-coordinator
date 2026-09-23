from __future__ import annotations

import gzip
import hashlib
import os
from pathlib import Path

import pytest

from . import diagnostics
from .conftest import pytest_runtest_makereport


def test_exact_case_round_trip_and_duplicate_have_one_identity(tmp_path: Path) -> None:
    record = b'{"source":"synthetic policy","phase":"coverage"}'
    first = diagnostics.retain_case(record, tmp_path)
    assert diagnostics.retain_case(record, tmp_path) == first
    paths = tuple(tmp_path.iterdir())
    assert len(paths) == 1
    assert gzip.decompress(paths[0].read_bytes()) == record
    assert hashlib.sha256(record).hexdigest() in first


@pytest.mark.parametrize("bound", ["MAX_CASE_BYTES", "MAX_RETAINED_BYTES", "MAX_CASE_FILES"])
def test_excess_is_explicitly_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bound: str
) -> None:
    monkeypatch.setattr(diagnostics, bound, 0)
    assert "incomplete" in diagnostics.retain_case(b"case", tmp_path)
    assert not tuple(tmp_path.iterdir())


def test_storage_failure_does_not_replace_the_primary_failure(tmp_path: Path) -> None:
    directory = tmp_path / "not-a-directory"
    directory.write_text("occupied")
    assert diagnostics.retain_case(b"case", directory) == (
        "Exact-case storage unavailable; reproduction evidence incomplete"
    )


def test_partial_artifact_is_not_admitted_and_atomic_retry_repairs_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = b'{"case":"repeat"}'
    path = tmp_path / f"case-{hashlib.sha256(record).hexdigest()}.json.gz"
    path.write_bytes(b"partial-gzip")
    replace = os.replace

    def fail_replace(*_args: object) -> None:
        raise OSError("controlled storage failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    assert "incomplete" in diagnostics.retain_case(record, tmp_path)
    assert tuple(tmp_path.iterdir()) == (path,)
    assert path.read_bytes() == b"partial-gzip"
    monkeypatch.setattr(os, "replace", replace)
    assert "incomplete" not in diagnostics.retain_case(record, tmp_path)
    assert gzip.decompress(path.read_bytes()) == record


def test_report_hook_preserves_original_failure_when_artifacts_cannot_be_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    directory = tmp_path / "occupied"
    directory.write_text("not a directory")
    monkeypatch.setenv("CI_COORDINATOR_API_ARTIFACTS", str(directory))
    report = pytest.TestReport(
        nodeid=request.node.nodeid,
        location=(str(request.node.path), 1, "failure"),
        keywords={},
        outcome="failed",
        longrepr="original failure",
        when="call",
    )
    hook = pytest_runtest_makereport(
        request.node, pytest.CallInfo.from_call(lambda: None, when="call")
    )
    next(hook)
    with pytest.raises(StopIteration) as completed:
        hook.send(report)
    assert completed.value.value is report
    assert report.failed and report.longrepr == "original failure"
    assert report.sections == [("API diagnostics", "Storage unavailable; evidence incomplete")]
