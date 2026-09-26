from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path

import pytest

# isort: split
from scripts import proofkit_admission as admission
from scripts import proofkit_inputs as inputs
from scripts import proofkit_text_policy as composition
from scripts.bounded_git import BoundedGitResult, run_git
from scripts.proofkit_cli import ProofkitProcessResult, invoke_proofkit, resolve_proofkit_executable
from scripts.proofkit_common import JsonObject

REPORT_ID = "ci-coordinator.text-policy"
CAP = 33_554_432
POLICY = {
    "allowTab": True,
    "asciiOnly": True,
    "binarySuffixes": [
        ".avif",
        ".gif",
        ".gz",
        ".ico",
        ".jpeg",
        ".jpg",
        ".pdf",
        ".png",
        ".tgz",
        ".webp",
        ".zip",
    ],
    "rejectTrailingWhitespace": True,
    "requireFinalNewline": True,
}
CALLER_NON_CLAIMS = [
    "CI Coordinator supplies an explicit tracked and untracked file inventory.",
    "Text policy is not a repository-wide secret scanner.",
]
NATIVE_NON_CLAIMS = [
    "CI Coordinator supplies an explicit tracked and untracked file inventory.",
    "Text policy checks caller-provided file inventory only.",
    "Text policy does not discover git state, read repository files, own repository-specific "
    "documentation topology, decide proof freshness, approve merge, release, or rollout.",
    "Text policy is not a repository-wide secret scanner.",
]


def _wire(path: str, content: bytes) -> JsonObject:
    return {
        "contentBase64": base64.b64encode(content).decode("ascii"),
        "path": path,
        "state": "present",
    }


def _document(rows: Sequence[JsonObject], report_id: str = REPORT_ID) -> JsonObject:
    return {
        "schemaVersion": 1,
        "reportId": report_id,
        "nonClaims": list(CALLER_NON_CLAIMS),
        "policy": deepcopy(POLICY),
        "files": list(rows),
    }


def _encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _passed(report_id: str, count: int, binary: int = 0) -> JsonObject:
    return {
        "schemaVersion": 1,
        "reportKind": "proofkit.text-policy",
        "reportId": report_id,
        "state": "passed",
        "summary": {
            "admittedPolicy": deepcopy(POLICY),
            "binarySkippedFileCount": binary,
            "checkedTextFileCount": count - binary,
            "failureCount": 0,
            "inputFileCount": count,
            "missingSkippedFileCount": 0,
        },
        "diagnostics": [{"key": "failures", "value": []}],
        "ruleResults": [
            {
                "ruleId": "proofkit.text-policy.admitted-policy",
                "status": "passed",
                "message": "Caller-provided text files satisfy the admitted text policy.",
                "diagnostics": [{"key": "failureCount", "value": 0}],
            }
        ],
        "nonClaims": list(NATIVE_NON_CLAIMS),
    }


def _entry(path: str, content: bytes = b"ok\n") -> inputs.TextPolicyEntry:
    return inputs.TextPolicyEntry(
        path,
        0o100644,
        len(content),
        None,
        base64.b64encode(content).decode("ascii"),
        hashlib.sha256(content).hexdigest(),
    )


def _inventory(monkeypatch: pytest.MonkeyPatch, paths: list[str]) -> None:
    def read_git(
        root: Path, arguments: Sequence[str], *, timeout_seconds: float
    ) -> BoundedGitResult:
        assert root.is_dir()
        assert tuple(arguments) == ("ls-files", "--cached", "--others", "--exclude-standard", "-z")
        assert 0 < timeout_seconds <= 30
        return BoundedGitResult(0, "".join(f"{path}\0" for path in paths), "")

    monkeypatch.setattr(inputs, "run_git", read_git)


def _native_stub(
    monkeypatch: pytest.MonkeyPatch,
    expected: Sequence[tuple[Sequence[str], int]],
    *,
    mutate: Callable[[JsonObject], None] | None = None,
    after: Callable[[int], None] | None = None,
) -> list[JsonObject]:
    calls: list[JsonObject] = []

    def invoke(
        executable: str,
        command: str,
        args: Sequence[str],
        *,
        cwd: Path,
        input_text: str,
        timeout_seconds: float,
    ) -> ProofkitProcessResult:
        assert executable == "fixed-proofkit"
        assert command == "text-policy" and tuple(args) == ("--input", "-")
        assert cwd.is_dir() and 0 < timeout_seconds <= 120
        document = json.loads(input_text)
        index = len(calls)
        paths, binary = expected[index]
        report_id = REPORT_ID if len(expected) == 1 else f"{REPORT_ID}.batch-{index + 1}"
        assert document["reportId"] == report_id
        assert document["policy"] == POLICY
        assert document["nonClaims"] == CALLER_NON_CLAIMS
        assert [row["path"] for row in document["files"]] == list(paths)
        calls.append(
            {"input": document, "timeout": timeout_seconds, "bytes": len(input_text.encode())}
        )
        response = _passed(report_id, len(paths), binary)
        if mutate is not None:
            mutate(response)
        if after is not None:
            after(index)
        return ProofkitProcessResult(0, _encode(response), "")

    monkeypatch.setattr(composition, "invoke_proofkit", invoke)
    return calls


def test_snapshot_closes_git_membership_including_explicit_exclusions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = ["missing", "nul", "huge", "folder", "link", "dist/a", "empty", "a", ".png"]
    _inventory(monkeypatch, paths)
    for name, content in {
        "a": b"a\n",
        "empty": b"",
        ".png": b"not text",
        "nul": b"a\0b",
        "huge": b"x" * 1_000_001,
    }.items():
        (tmp_path / name).write_bytes(content)
    (tmp_path / "folder").mkdir()
    (tmp_path / "link").symlink_to("a")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist/a").write_bytes(b"ignored\n")
    entries = inputs.capture_text_policy_inventory(tmp_path, lambda: 120)
    assert [e.path for e in entries] == sorted(paths)
    assert {e.path: e.exclusion for e in entries if e.exclusion is not None} == {
        "dist/a": "dist",
        "folder": "directory",
        "huge": "over-size-limit",
        "link": "non-regular",
        "missing": "missing",
        "nul": "nul-content",
    }
    assert [e.row() for e in entries if e.exclusion is None] == [
        _wire(".png", b"not text"),
        _wire("a", b"a\n"),
        _wire("empty", b""),
    ]
    calls = _native_stub(monkeypatch, [([".png", "a", "empty"], 1)])
    report = composition.text_policy_report(tmp_path, proofkit_executable="fixed-proofkit")
    assert len(calls) == 1
    assert report["summary"]["inventoryFileCount"] == 9
    assert report["summary"]["excludedFileCount"] == 6
    assert report["summary"]["checkedTextFileCount"] == 2
    assert len(report["summary"]["excludedFiles"]) == 6


@pytest.mark.parametrize("paths", [["a", "a"], ["a\\b"]])
def test_raw_inventory_cannot_deduplicate_or_relabel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    paths: list[str],
) -> None:
    _inventory(monkeypatch, paths)
    with pytest.raises(ValueError, match=r"duplicate paths|noncanonical path"):
        inputs.capture_text_policy_inventory(tmp_path, lambda: 120)


@pytest.mark.parametrize("paths", [["b", "a"], ["a", "a"]])
def test_global_order_is_required_before_any_partition(paths: list[str]) -> None:
    with pytest.raises(ValueError, match="globally sorted and unique"):
        inputs.plan_text_policy_batches([_entry(p) for p in paths], lambda: 120)


@pytest.mark.parametrize("delta, count", [(-1, 2), (0, 1), (1, 1)])
def test_exact_utf8_envelope_boundary_includes_escapes_and_child_ids(
    delta: int, count: int
) -> None:
    files = [_entry('a".txt', b"a" * 400 + b"\n"), _entry("b\u00e9.txt", b"b" * 400 + b"\n")]
    limit = len(_encode(_document([e.row() for e in files])).encode("utf-8")) + delta
    batches = inputs.plan_text_policy_batches(files, lambda: 120, maximum_bytes=limit)
    assert len(batches) == count
    assert [(b.start, b.stop) for b in batches] == ([(0, 2)] if count == 1 else [(0, 1), (1, 2)])
    for index, batch in enumerate(batches, 1):
        expected_id = REPORT_ID if count == 1 else f"{REPORT_ID}.batch-{index}"
        encoded = _encode(
            _document([e.row() for e in files[batch.start : batch.stop]], expected_id)
        ).encode()
        assert batch.report_id == expected_id
        assert batch.input_bytes == len(encoded) <= limit


def test_maximal_contiguous_prefixes_do_not_degrade_to_one_file_calls() -> None:
    files = [_entry(p, b"a" * 400 + b"\n") for p in ["a", "b", "c"]]
    limit = len(_encode(_document([e.row() for e in files[:2]], f"{REPORT_ID}.batch-1")).encode())
    batches = inputs.plan_text_policy_batches(files, lambda: 120, maximum_bytes=limit)
    assert [(b.start, b.stop) for b in batches] == [(0, 2), (2, 3)]


@pytest.mark.parametrize("limit", [0, -1, True, CAP + 1])
def test_input_limit_cannot_be_raised_or_coerced(limit: int) -> None:
    with pytest.raises(ValueError, match="pinned CLI limit"):
        inputs.plan_text_policy_batches([], lambda: 120, maximum_bytes=limit)


def test_oversized_row_fails_instead_of_splitting_or_dropping() -> None:
    with pytest.raises(ValueError, match=r"row exceeds.*large"):
        inputs.plan_text_policy_batches(
            [_entry("large", b"x" * 2000)], lambda: 120, maximum_bytes=1000
        )


def test_empty_inventory_still_calls_native_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _inventory(monkeypatch, [])
    calls = _native_stub(monkeypatch, [([], 0)])
    result = admission.admission_report(
        "text-policy", repo_root=tmp_path, proofkit_executable="fixed-proofkit"
    )
    assert len(calls) == result["summary"]["admissionCount"] == 1
    assert result["summary"]["inputFileCount"] == result["summary"]["checkedTextFileCount"] == 0


@pytest.mark.parametrize("case", ["omitted", "duplicate", "overlap", "wrong-id", "empty"])
def test_partition_mutants_reject_before_native_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    _inventory(monkeypatch, ["a", "b"])
    for name in ["a", "b"]:
        (tmp_path / name).write_bytes(b"x" * 400 + b"\n")
    plan = inputs.plan_text_policy_batches

    def corrupt(*args: object, **kwargs: object) -> tuple[inputs.TextPolicyBatch, ...]:
        rows = plan(
            [_entry("a", b"x" * 400 + b"\n"), _entry("b", b"x" * 400 + b"\n")],
            lambda: 120,
            maximum_bytes=1200,
        )
        assert len(rows) == 2
        if case == "omitted":
            assert (rows[0].start, rows[0].stop) == (0, 1)
            literal_input = _document([_wire("a", b"x" * 400 + b"\n")], REPORT_ID)
            input_bytes = len(_encode(literal_input).encode("utf-8"))
            assert input_bytes == 1001
            return (replace(rows[0], report_id=REPORT_ID, input_bytes=input_bytes),)
        if case == "duplicate":
            return (rows[0], rows[0], rows[1])
        if case == "overlap":
            return (rows[0], replace(rows[1], start=0))
        if case == "wrong-id":
            return (replace(rows[0], report_id="foreign"), rows[1])
        return ()

    monkeypatch.setattr(composition, "plan_text_policy_batches", corrupt)
    # Removing only the tail guard must reach a valid launch, not a fixture error.
    calls = _native_stub(monkeypatch, [(["a"], 0)] if case == "omitted" else [])
    diagnostic = (
        r"^text-policy partition omits inventory rows$" if case == "omitted" else "partition"
    )
    with pytest.raises(ValueError, match=diagnostic):
        composition.text_policy_report(
            tmp_path, proofkit_executable="fixed-proofkit", maximum_input_bytes=1200
        )
    assert calls == []


def test_complete_partition_reaches_native_with_the_same_framing_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _inventory(monkeypatch, ["a", "b"])
    for name in ["a", "b"]:
        (tmp_path / name).write_bytes(b"x" * 400 + b"\n")
    calls = _native_stub(monkeypatch, [(["a"], 0), (["b"], 0)])
    report = composition.text_policy_report(
        tmp_path, proofkit_executable="fixed-proofkit", maximum_input_bytes=1200
    )
    assert len(calls) == report["summary"]["admissionCount"] == 2
    assert report["summary"]["inputFileCount"] == report["summary"]["checkedTextFileCount"] == 2
    assert [(r["start"], r["stop"]) for r in report["reports"]] == [(0, 1), (1, 2)]


def test_rendered_payload_must_match_its_planned_byte_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _inventory(monkeypatch, ["a"])
    (tmp_path / "a").write_bytes(b"ok\n")
    planned = inputs.plan_text_policy_batches([_entry("a")], lambda: 120)
    monkeypatch.setattr(
        composition,
        "plan_text_policy_batches",
        lambda *args, **kwargs: (replace(planned[0], input_bytes=planned[0].input_bytes - 1),),
    )
    calls = _native_stub(monkeypatch, [])
    with pytest.raises(ValueError, match="envelope size mismatch"):
        composition.text_policy_report(tmp_path, proofkit_executable="fixed-proofkit")
    assert calls == []


@pytest.mark.parametrize(
    "fault", ["nonzero", "malformed", "duplicate-key", "output-limit", "timeout", "cancelled"]
)
def test_a_terminal_child_failure_never_launches_the_next_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    _inventory(monkeypatch, ["a", "b", "c"])
    for path in ["a", "b", "c"]:
        (tmp_path / path).write_bytes(b"x" * 400 + b"\n")
    calls: list[str] = []
    process_error = RuntimeError(
        "timed out" if fault == "timeout" else "output exceeded 8388608 bytes"
    )
    cancellation = KeyboardInterrupt("cancelled")

    def invoke(
        executable: str,
        command: str,
        args: Sequence[str],
        *,
        cwd: Path,
        input_text: str,
        timeout_seconds: float,
    ) -> ProofkitProcessResult:
        assert executable == "fixed-proofkit" and command == "text-policy"
        assert tuple(args) == ("--input", "-") and cwd == tmp_path
        assert 0 < timeout_seconds <= 120
        document = json.loads(input_text)
        calls.append(document["reportId"])
        assert calls[-1] == f"{REPORT_ID}.batch-{len(calls)}"
        if len(calls) == 1:
            return ProofkitProcessResult(0, _encode(_passed(f"{REPORT_ID}.batch-1", 1)), "")
        assert len(calls) == 2
        if fault == "nonzero":
            return ProofkitProcessResult(7, "", "declared-child-failure")
        if fault == "malformed":
            return ProofkitProcessResult(0, "{", "")
        if fault == "duplicate-key":
            return ProofkitProcessResult(0, '{"state":"failed","state":"passed"}', "")
        if fault in {"output-limit", "timeout"}:
            raise process_error
        raise cancellation

    monkeypatch.setattr(composition, "invoke_proofkit", invoke)
    expected: type[BaseException] = (
        KeyboardInterrupt
        if fault == "cancelled"
        else (ValueError if fault in {"malformed", "duplicate-key"} else RuntimeError)
    )
    diagnostic = {
        "nonzero": r"batch-2.*declared-child-failure",
        "malformed": "did not emit JSON",
        "duplicate-key": "duplicate key",
        "output-limit": rf"^Proofkit text-policy failed \({re.escape(REPORT_ID)}\.batch-2\): "
        r"output exceeded 8388608 bytes$",
        "timeout": rf"^Proofkit text-policy failed \({re.escape(REPORT_ID)}\.batch-2\): timed out$",
        "cancelled": "cancelled",
    }[fault]
    with pytest.raises(expected, match=diagnostic) as failure:
        composition.text_policy_report(
            tmp_path, proofkit_executable="fixed-proofkit", maximum_input_bytes=1200
        )
    assert calls == [f"{REPORT_ID}.batch-1", f"{REPORT_ID}.batch-2"]
    if fault in {"output-limit", "timeout"}:
        assert failure.value.__cause__ is process_error
    elif fault == "cancelled":
        assert failure.value is cancellation
        assert failure.value.__cause__ is None


@pytest.mark.parametrize(
    "key,value",
    [
        ("schemaVersion", True),
        ("schemaVersion", 1.0),
        ("reportId", "foreign"),
        ("reportKind", "foreign"),
        ("state", "failed"),
        ("nonClaims", []),
        ("diagnostics", []),
        ("diagnostics", [{"key": "failures", "value": ["hidden"]}]),
        ("ruleResults", []),
        ("unexpected", None),
    ],
)
def test_forged_passed_root_is_not_admitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: object,
) -> None:
    _inventory(monkeypatch, ["a"])
    (tmp_path / "a").write_bytes(b"ok\n")
    _native_stub(monkeypatch, [(["a"], 0)], mutate=lambda report: report.update({key: value}))
    with pytest.raises(ValueError, match="exact pinned output"):
        composition.text_policy_report(tmp_path, proofkit_executable="fixed-proofkit")


@pytest.mark.parametrize(
    "key,value",
    [
        ("inputFileCount", 0),
        ("inputFileCount", True),
        ("inputFileCount", 1.0),
        ("checkedTextFileCount", 0),
        ("binarySkippedFileCount", 1),
        ("missingSkippedFileCount", 1),
        ("failureCount", False),
        ("failureCount", 1),
        ("admittedPolicy", {}),
    ],
)
def test_forged_summary_cannot_claim_checked_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: object,
) -> None:
    _inventory(monkeypatch, ["a"])
    (tmp_path / "a").write_bytes(b"ok\n")
    _native_stub(monkeypatch, [(["a"], 0)], mutate=lambda r: r["summary"].update({key: value}))
    with pytest.raises(ValueError, match="exact pinned output"):
        composition.text_policy_report(tmp_path, proofkit_executable="fixed-proofkit")


@pytest.mark.parametrize("case", ["id", "status", "failure", "false-zero", "policy-boolean"])
def test_nested_rule_and_policy_types_are_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    _inventory(monkeypatch, ["a"])
    (tmp_path / "a").write_bytes(b"ok\n")

    def mutate(report: JsonObject) -> None:
        rule = report["ruleResults"][0]
        if case == "id":
            rule["ruleId"] = "foreign"
        elif case == "status":
            rule["status"] = "skipped"
        elif case == "policy-boolean":
            report["summary"]["admittedPolicy"]["asciiOnly"] = 1
        else:
            rule["diagnostics"][0]["value"] = False if case == "false-zero" else 1

    _native_stub(monkeypatch, [(["a"], 0)], mutate=mutate)
    with pytest.raises(ValueError, match="exact pinned output"):
        composition.text_policy_report(tmp_path, proofkit_executable="fixed-proofkit")


@pytest.mark.parametrize(
    "change", ["add", "delete", "bytes", "mode", "nul", "large", "excluded-to-text"]
)
def test_source_drift_prevents_an_aggregate_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    paths = ["a", "nul"]
    _inventory(monkeypatch, paths)
    (tmp_path / "a").write_bytes(b"ok\n")
    (tmp_path / "nul").write_bytes(b"\0")

    def after(_index: int) -> None:
        if change == "add":
            paths.append("b")
            (tmp_path / "b").write_bytes(b"ok\n")
        elif change == "delete":
            (tmp_path / "a").unlink()
        elif change == "mode":
            (tmp_path / "a").chmod(0o755)
        elif change == "excluded-to-text":
            (tmp_path / "nul").write_bytes(b"ok\n")
        else:
            content = {"bytes": b"new\n", "nul": b"\0", "large": b"a" * 1_000_001}[change]
            (tmp_path / "a").write_bytes(content)

    calls = _native_stub(monkeypatch, [(["a"], 0)], after=after)
    with pytest.raises(ValueError, match="inventory changed"):
        composition.text_policy_report(tmp_path, proofkit_executable="fixed-proofkit")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "stage", ["resolve", "prepare", "first-call", "second-call", "final-check"]
)
def test_one_deadline_includes_preparation_and_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    now = [0.0]
    monkeypatch.setattr(composition, "monotonic", lambda: now[0])
    _inventory(monkeypatch, ["a", "b"])
    for path in ["a", "b"]:
        (tmp_path / path).write_bytes(b"x" * 400 + b"\n")
    capture = inputs.capture_text_policy_inventory
    captures = 0

    def snapshot(root: Path, remaining: Callable[[], float]) -> tuple[inputs.TextPolicyEntry, ...]:
        nonlocal captures
        captures += 1
        entries = capture(root, remaining)
        if (stage == "prepare" and captures == 1) or (stage == "final-check" and captures == 2):
            now[0] = 120
        return entries

    monkeypatch.setattr(composition, "capture_text_policy_inventory", snapshot)
    if stage == "resolve":

        def resolve(*args: object, **kwargs: object) -> str:
            now[0] = 120
            return "fixed-proofkit"

        monkeypatch.setattr(composition, "resolve_proofkit_executable", resolve)

    def after(index: int) -> None:
        if (stage == "first-call" and index == 0) or (stage == "second-call" and index == 1):
            now[0] = 120
        elif index == 0:
            now[0] = 17

    calls = _native_stub(monkeypatch, [(["a"], 0), (["b"], 0)], after=after)
    call_index = {"first-call": 1, "second-call": 2}.get(stage)
    diagnostic = (
        rf"^Proofkit text-policy failed \({re.escape(REPORT_ID)}\.batch-{call_index}\): "
        r"text-policy shared deadline exhausted$"
        if call_index is not None
        else r"^text-policy shared deadline exhausted$"
    )
    with pytest.raises(RuntimeError, match=diagnostic) as failure:
        composition.text_policy_report(
            tmp_path, proofkit_executable="fixed-proofkit", maximum_input_bytes=1200
        )
    assert (
        len(calls)
        == {
            "resolve": 0,
            "prepare": 0,
            "first-call": 1,
            "second-call": 2,
            "final-check": 2,
        }[stage]
    )
    if call_index is not None:
        assert isinstance(failure.value.__cause__, RuntimeError)
        assert str(failure.value.__cause__) == "text-policy shared deadline exhausted"
    else:
        assert failure.value.__cause__ is None
    if len(calls) == 2:
        assert [c["timeout"] for c in calls] == [120, 103]


def test_single_input_cli_remains_one_original_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "a").write_bytes(b"a\n")
    monkeypatch.setattr(inputs, "repo_files", lambda root: ["a"])
    expected = _document([{"contentBase64": "YQo=", "path": "a", "state": "present"}])
    assert inputs.text_policy_input(tmp_path) == expected
    monkeypatch.setattr(inputs, "text_policy_input", lambda root: expected)
    assert inputs.main(["text-policy"]) == 0
    assert json.loads(capsys.readouterr().out) == expected
    assert inputs.main(["text-policy", "unexpected"]) == 1
    assert capsys.readouterr().out == ""


def test_verify_dispatch_does_not_enter_text_composition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> JsonObject:
        raise AssertionError("verify entered text-policy composition")

    monkeypatch.setattr(admission, "text_policy_report", forbidden)
    monkeypatch.setattr(admission, "repo_profile_input", lambda root: {})
    monkeypatch.setattr(
        admission, "_run_with_input", lambda exe, cmd, value, root: {"command": cmd}
    )
    monkeypatch.setattr(admission, "_run_with_path", lambda exe, cmd, value, root: {"command": cmd})
    report = admission.admission_report(
        "verify", repo_root=tmp_path, proofkit_executable="fixed-proofkit"
    )
    assert report["summary"] == {
        "admissionCount": 2,
        "commands": ["repo-profile-admission", "witness-plan"],
    }


def _repository(root: Path, files: dict[str, bytes]) -> None:
    run_git(root, ("init", "--quiet"))
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def test_native_whole_and_forced_batches_have_independent_literal_counts(tmp_path: Path) -> None:
    assert version("agentic-proofkit") == "0.14.18"
    files = {
        ".png": b"not text",
        "a.txt": b"a\tb\n",
        "empty.txt": b"",
        "icon.\u0130CO": b"not text",
        "image.PNG": b"not text",
    }
    _repository(tmp_path, files)
    executable = resolve_proofkit_executable()
    whole = invoke_proofkit(
        executable,
        "text-policy",
        ("--input", "-"),
        cwd=tmp_path,
        input_text=_encode(_document([_wire(p, files[p]) for p in sorted(files)])),
    )
    assert whole.returncode == 0, whole.stderr
    assert json.loads(whole.stdout) == _passed(REPORT_ID, 5, 3)
    single_limit = max(
        len(_encode(_document([_wire(p, data)], f"{REPORT_ID}.batch-1")).encode())
        for p, data in files.items()
    )
    split = composition.text_policy_report(
        tmp_path, proofkit_executable=executable, maximum_input_bytes=single_limit
    )
    assert split["summary"]["admissionCount"] == 5
    assert split["summary"]["inputFileCount"] == 5
    assert split["summary"]["checkedTextFileCount"] == 2
    assert split["summary"]["binarySkippedFileCount"] == 3


@pytest.mark.parametrize(
    "bad,diagnostic",
    [
        (b"\xff\n", "not valid UTF-8"),
        (b"\x63\x61\x66\xc3\xa9\n", "non-ASCII"),
        (b"no newline", "missing final newline"),
        (b"space \n", "trailing whitespace"),
    ],
)
def test_native_violation_in_last_batch_cannot_be_hidden(
    tmp_path: Path, bad: bytes, diagnostic: str
) -> None:
    assert version("agentic-proofkit") == "0.14.18"
    _repository(tmp_path, {"a.txt": b"ok\n", "z.txt": bad})
    limit = max(
        len(_encode(_document([_wire(name, data)], f"{REPORT_ID}.batch-1")).encode())
        for name, data in {"a.txt": b"ok\n", "z.txt": bad}.items()
    )
    with pytest.raises(RuntimeError, match=rf"(?s)batch-2.*{diagnostic}"):
        composition.text_policy_report(tmp_path, maximum_input_bytes=limit)


def test_native_actual_32_mib_counterfactual_and_complete_batches(tmp_path: Path) -> None:
    assert version("agentic-proofkit") == "0.14.18"
    content = b"a" * 999_999 + b"\n"
    _repository(tmp_path, {f"{index:02d}.txt": content for index in range(26)})
    original = inputs.text_policy_input(tmp_path)
    payload = _encode(original)
    assert len(payload.encode()) > CAP
    executable = resolve_proofkit_executable()
    rejected = invoke_proofkit(
        executable, "text-policy", ("--input", "-"), cwd=tmp_path, input_text=payload
    )
    assert rejected.returncode != 0
    assert "invalid JSON input: exceeds resource limit" in rejected.stderr
    result = admission.admission_report(
        "text-policy", repo_root=tmp_path, proofkit_executable=executable
    )
    assert result["state"] == "passed"
    assert result["summary"]["admissionCount"] == 2
    assert result["summary"]["inputFileCount"] == result["summary"]["checkedTextFileCount"] == 26
    assert [(r["start"], r["stop"]) for r in result["reports"]] == [(0, 25), (25, 26)]
    assert all(r["inputBytes"] <= CAP for r in result["reports"])
