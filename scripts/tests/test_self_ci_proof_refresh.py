from __future__ import annotations

import hashlib
import json
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from scripts import self_ci_generate as generator
from scripts.bounded_git import run_git
from scripts.ci_matrix_risks import check as check_risks
from scripts.self_ci_proof_refresh import (
    BUNDLED_DISPOSITION_PATH,
    DISPOSITION_PATH,
    RISK_PATH,
    ProofSources,
    entrypoint_projection,
    risk_projection,
)
from scripts.self_ci_source import (
    NATIVE_PATH,
    WORKFLOW_PATH,
    admit_native_source,
    workflow_value,
)

ROOT = Path(__file__).resolve().parents[2]
GROUPS = (
    "container.workflow-lint",
    "deployment.documents",
    "deployment.root-files",
    "generated.documents",
    "workflow.documents",
)


def _json(value: object) -> bytes:
    return (json.dumps(value, indent=2) + "\n").encode()


def _changed(sources: ProofSources, changes: dict[str, bytes]) -> ProofSources:
    return replace(sources, files=sources.files | changes, modes=dict(sources.modes))


def _rows(raw: bytes) -> dict[str, dict[str, object]]:
    return {row["sourceId"]: row for row in json.loads(raw)["sources"]}


def _expected_fingerprint(files: dict[str, bytes], row: dict[str, object]) -> str:
    members = row["memberIds"]
    assert isinstance(members, list)
    digests = {path: hashlib.sha256(files[path]).hexdigest() for path in members}
    if row["sourceId"] == "container.workflow-lint":
        return "sha256:" + digests["Dockerfile.workflow-lint"]
    canonical = json.dumps(digests, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


@pytest.fixture(scope="module")
def refreshed_repository(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[ProofSources, dict[str, bytes]]:
    original = ProofSources.capture(ROOT)
    root = tmp_path_factory.mktemp("proof-refresh")
    for path, content in original.files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(original.modes[path])
    run_git(root, ("init", "--quiet"))
    run_git(root, ("add", "--force", "."))
    sources, outputs = generator.render_proof_refresh(root)
    generator.write_proof_refresh(sources, outputs, refresh_proof_hashes=True)
    return ProofSources.capture(root), outputs


@pytest.fixture
def sources(
    refreshed_repository: tuple[ProofSources, dict[str, bytes]],
) -> ProofSources:
    baseline, _outputs = refreshed_repository
    return _changed(baseline, {})


@contextmanager
def _replace_file(root: Path, path: str, content: bytes | None) -> Iterator[None]:
    target = root / path
    original = target.read_bytes() if target.exists() else None
    mode = stat.S_IMODE(target.stat().st_mode) if original is not None else 0o644
    target.parent.mkdir(parents=True, exist_ok=True)
    if content is None:
        target.unlink()
    else:
        target.write_bytes(content)
    try:
        yield
    finally:
        if original is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(original)
            target.chmod(mode)


def test_workflow_only_change_refreshes_its_independent_group(
    sources: ProofSources,
) -> None:
    path = ".github/workflows/release-artifact.yml"
    changed = _changed(sources, {path: sources.read(path) + b"\n# changed workflow owner\n"})
    outputs = entrypoint_projection(changed, {})
    rows = _rows(outputs[DISPOSITION_PATH])
    before = _rows(sources.read(DISPOSITION_PATH))
    assert (
        rows["workflow.documents"]["currentTarget"] != before["workflow.documents"]["currentTarget"]
    )
    assert rows["workflow.documents"]["currentTarget"] == _expected_fingerprint(
        changed.files, rows["workflow.documents"]
    )
    assert rows["generated.documents"] == before["generated.documents"]
    assert outputs[DISPOSITION_PATH] == outputs[BUNDLED_DISPOSITION_PATH]


@pytest.mark.parametrize("source_id", GROUPS)
def test_each_fingerprint_has_an_independent_byte_oracle(
    sources: ProofSources, source_id: str
) -> None:
    row = _rows(sources.read(DISPOSITION_PATH))[source_id]
    members = row["memberIds"]
    assert isinstance(members, list) and members
    path = members[0]
    changed = _changed(sources, {path: sources.read(path) + b"\n"})
    actual = _rows(entrypoint_projection(changed, {})[DISPOSITION_PATH])[source_id]
    assert actual["currentTarget"] == _expected_fingerprint(changed.files, row)
    assert actual["currentTarget"] != row["currentTarget"]


def test_helper_only_change_refreshes_every_existing_whole_file_binding(
    sources: ProofSources,
) -> None:
    before = json.loads(sources.read(RISK_PATH))
    path = next(
        binding["witnessPath"]
        for row in before["candidateRows"]
        for binding in row["bindings"]
        if binding["witnessPath"].endswith(".py")
    )
    changed_bytes = sources.read(path) + b"\n\ndef _new_shared_helper():\n    return 42\n"
    after = json.loads(risk_projection(_changed(sources, {path: changed_bytes})))
    expected = json.loads(sources.read(RISK_PATH))
    replaced = 0
    for row in expected["candidateRows"]:
        for binding in row["bindings"]:
            if binding["witnessPath"] == path:
                binding["sourceSha256"] = hashlib.sha256(changed_bytes).hexdigest()
                replaced += 1
    assert replaced > 0 and after == expected and after != before
    assert all(
        binding["evidenceState"] == "configured-not-executed"
        for row in after["candidateRows"]
        for binding in row["bindings"]
    )


def test_execution_hash_refresh_does_not_rewrite_qualification_exceptions(
    sources: ProofSources,
) -> None:
    path = "scripts/ci_test_plan.py"
    original = sources.read(path)
    changed_bytes = original + b"\n# execution owner change\n"
    changed = _changed(sources, {path: changed_bytes})
    actual = json.loads(risk_projection(changed))
    expected = json.loads(sources.read(RISK_PATH))
    expected["executionInputs"][path] = hashlib.sha256(changed_bytes).hexdigest()
    assert actual == expected
    assert changed.read(path) == changed_bytes
    assert b"EXTERNALLY_QUALIFIED_MODULES" in original


@pytest.mark.parametrize("source_id", GROUPS)
def test_missing_fingerprint_class_is_not_silently_ignored(
    sources: ProofSources, source_id: str
) -> None:
    value = json.loads(sources.read(DISPOSITION_PATH))
    value["sources"] = [row for row in value["sources"] if row["sourceId"] != source_id]
    changed = _changed(
        sources,
        {DISPOSITION_PATH: _json(value), BUNDLED_DISPOSITION_PATH: _json(value)},
    )
    with pytest.raises(ValueError, match="fingerprint class"):
        entrypoint_projection(changed, {})


def test_stale_copy_can_only_receive_the_canonical_projection(
    sources: ProofSources,
) -> None:
    value = json.loads(sources.read(BUNDLED_DISPOSITION_PATH))
    next(row for row in value["sources"] if row["sourceId"] == "workflow.documents")[
        "currentTarget"
    ] = "sha256:" + "0" * 64
    changed = _changed(sources, {BUNDLED_DISPOSITION_PATH: _json(value)})
    outputs = entrypoint_projection(changed, {})
    assert outputs[DISPOSITION_PATH] == sources.read(DISPOSITION_PATH)
    assert outputs[BUNDLED_DISPOSITION_PATH] == sources.read(DISPOSITION_PATH)


@pytest.mark.parametrize(
    "mutation",
    ["member", "classification", "caller", "root", "duplicate-key", "unknown-hash"],
)
def test_refresh_cannot_repair_malformed_or_different_declarations(
    sources: ProofSources, mutation: str
) -> None:
    value = json.loads(sources.read(BUNDLED_DISPOSITION_PATH))
    row = next(row for row in value["sources"] if row["sourceId"] == "workflow.documents")
    if mutation == "member":
        row["memberIds"] = row["memberIds"][:-1]
    elif mutation == "classification":
        row["disposition"] = "unknown"
    elif mutation == "caller":
        row["callerId"] = "invented"
    elif mutation == "root":
        value["discovery"]["workflowDirectories"] = ["elsewhere"]
    elif mutation == "unknown-hash":
        row["currentTarget"] = "sha256:unknown"
    raw = _json(value)
    if mutation == "duplicate-key":
        raw = raw.replace(b'"discovery": {', b'"discovery": {}, "discovery": {', 1)
    with pytest.raises(ValueError):
        entrypoint_projection(_changed(sources, {BUNDLED_DISPOSITION_PATH: raw}), {})


@pytest.mark.parametrize("mutation", ["new", "missing", "ignored-new", "new-container"])
def test_new_or_missing_members_reject_before_generation_and_publication(
    sources: ProofSources, mutation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = ".github/workflows/undeclared.yml"
    content: bytes | None = b"name: unclassified\n"
    if mutation == "missing":
        path, content = ".github/workflows/release-artifact.yml", None
    elif mutation == "ignored-new":
        path = ".ci-coordinator/.ignored-owner"
    elif mutation == "new-container":
        path = "Dockerfile.unclassified"

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("invalid membership reached generation or publication")

    monkeypatch.setattr(generator, "render_self_ci", forbidden)
    monkeypatch.setattr(generator, "write_exact_file", forbidden)
    with ExitStack() as stack:
        if mutation == "ignored-new":
            exclude = ".git/info/exclude"
            stack.enter_context(
                _replace_file(
                    sources.root,
                    exclude,
                    (sources.root / exclude).read_bytes() + b"\n" + path.encode() + b"\n",
                )
            )
        stack.enter_context(_replace_file(sources.root, path, content))
        with pytest.raises((OSError, ValueError)):
            generator.render_proof_refresh(sources.root)


@pytest.mark.parametrize("mutation", ["selector", "command", "mode", "path", "state"])
def test_hash_refresh_cannot_admit_invalid_binding_metadata(
    sources: ProofSources, mutation: str
) -> None:
    value = json.loads(sources.read(RISK_PATH))
    binding = next(row for row in value["candidateRows"] if row["bindings"])["bindings"][0]
    if mutation == "selector":
        binding["assertion"] = "test_unowned_assertion"
    elif mutation == "command":
        binding["commandIds"] = ["invented.passing-command"]
    elif mutation == "mode":
        binding["executionModes"] = ["release"]
    elif mutation == "path":
        binding["witnessPath"] = "../foreign.py"
    else:
        binding["evidenceState"] = "passed"
    with pytest.raises(ValueError):
        risk_projection(_changed(sources, {RISK_PATH: _json(value)}))


def test_source_change_during_render_cannot_produce_publishable_outputs(
    sources: ProofSources, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = ".github/workflows/release-artifact.yml"
    original = generator.render_self_ci

    def changed(root: Path) -> generator.SelfCiArtifacts:
        result = original(root)
        (root / path).write_bytes(sources.read(path) + b"\n")
        return result

    monkeypatch.setattr(generator, "render_self_ci", changed)
    with (
        _replace_file(sources.root, path, sources.read(path)),
        pytest.raises(ValueError, match="sources or path/mode"),
    ):
        generator.render_proof_refresh(sources.root)


@pytest.mark.parametrize(
    "mutation", ["symlink", "oversized", "malformed-risk", "missing-execution-input"]
)
def test_unsafe_or_malformed_sources_fail_before_publication(
    sources: ProofSources, mutation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    writes: list[Path] = []
    monkeypatch.setattr(generator, "write_exact_file", lambda path, _content: writes.append(path))
    if mutation == "symlink":
        path = sources.root / ".github/workflows/symlink.yml"
        path.symlink_to(sources.root / NATIVE_PATH)
        try:
            with pytest.raises(ValueError):
                generator.render_proof_refresh(sources.root)
        finally:
            path.unlink()
    else:
        value = json.loads(sources.read(RISK_PATH))
        if mutation == "missing-execution-input":
            value["executionInputs"].pop(next(iter(value["executionInputs"])))
        content = _json(value) if mutation == "missing-execution-input" else b"{invalid"
        if mutation == "oversized":
            from scripts import self_ci_proof_refresh

            monkeypatch.setattr(self_ci_proof_refresh, "MAX_FILE_BYTES", 1)
        with _replace_file(sources.root, RISK_PATH, content), pytest.raises(ValueError):
            generator.render_proof_refresh(sources.root)
    assert writes == []


def test_source_change_between_render_and_write_rejects_before_first_write(
    sources: ProofSources,
    refreshed_repository: tuple[ProofSources, dict[str, bytes]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _baseline, outputs = refreshed_repository
    writes: list[Path] = []
    monkeypatch.setattr(generator, "write_exact_file", lambda path, _content: writes.append(path))
    path = ".github/workflows/release-artifact.yml"
    with (
        _replace_file(sources.root, path, sources.read(path) + b"\n"),
        pytest.raises(ValueError, match="sources or path/mode"),
    ):
        generator.write_proof_refresh(sources, outputs, refresh_proof_hashes=True)
    assert writes == []


def test_hash_refresh_requires_explicit_consent_before_any_write(
    sources: ProofSources, monkeypatch: pytest.MonkeyPatch
) -> None:
    outputs = {path: sources.read(path) for path in generator.REFRESH_OUTPUT_PATHS}
    outputs[DISPOSITION_PATH] += b"\n"
    writes: list[Path] = []
    monkeypatch.setattr(generator, "write_exact_file", lambda path, _content: writes.append(path))
    with pytest.raises(ValueError, match="--refresh-proof-hashes"):
        generator.write_proof_refresh(sources, outputs)
    assert writes == []


def test_writer_rejects_foreign_output_paths(sources: ProofSources) -> None:
    outputs = {path: sources.read(path) for path in generator.REFRESH_OUTPUT_PATHS}
    outputs["scripts/ci_test_plan.py"] = b"weakened exception"
    with pytest.raises(ValueError, match="output population"):
        generator.write_proof_refresh(sources, outputs, refresh_proof_hashes=True)


@pytest.mark.parametrize("noop_writer", [False, True])
def test_changed_witness_is_written_through_the_complete_proof_chain(
    sources: ProofSources, noop_writer: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    risk = json.loads(sources.read(RISK_PATH))
    witness = next(
        binding["witnessPath"]
        for row in risk["candidateRows"]
        for binding in row["bindings"]
        if binding["witnessPath"].endswith(".py")
    )
    payload = sources.read(witness) + b"\n\ndef _refresh_probe_helper():\n    return 42\n"
    changed_paths = {
        RISK_PATH,
        generator.INVENTORY_PATH,
        DISPOSITION_PATH,
        BUNDLED_DISPOSITION_PATH,
    }
    with ExitStack() as stack:
        stack.enter_context(_replace_file(sources.root, witness, payload))
        for path in sorted(changed_paths):
            stack.enter_context(_replace_file(sources.root, path, sources.read(path)))
        before, outputs = generator.render_proof_refresh(sources.root)
        assert {path for path, raw in outputs.items() if before.read(path) != raw} == changed_paths
        if noop_writer:
            monkeypatch.setattr(generator, "write_exact_file", lambda _path, _content: None)
            with pytest.raises(ValueError, match="sources or path/mode inventory changed"):
                generator.write_proof_refresh(before, outputs, refresh_proof_hashes=True)
            assert all(
                (sources.root / path).read_bytes() == sources.read(path) for path in changed_paths
            )
            return

        generator.write_proof_refresh(before, outputs, refresh_proof_hashes=True)

        for row in risk["candidateRows"]:
            for binding in row["bindings"]:
                if binding["witnessPath"] == witness:
                    binding["sourceSha256"] = hashlib.sha256(payload).hexdigest()
        assert json.loads((sources.root / RISK_PATH).read_bytes()) == risk
        inventory = json.loads((sources.root / generator.INVENTORY_PATH).read_bytes())
        inputs = {row["path"]: row["sha256"] for row in inventory["sourceInputs"]}
        assert (
            inputs[RISK_PATH] == hashlib.sha256((sources.root / RISK_PATH).read_bytes()).hexdigest()
        )
        published = {path: (sources.root / path).read_bytes() for path in sources.files}
        rows = _rows(published[DISPOSITION_PATH])
        for source_id in GROUPS:
            assert rows[source_id]["currentTarget"] == _expected_fingerprint(
                published, rows[source_id]
            )
        assert published[DISPOSITION_PATH] == published[BUNDLED_DISPOSITION_PATH]
        for path in changed_paths:
            assert published[path] == outputs[path]
            assert stat.S_IMODE((sources.root / path).stat().st_mode) == sources.modes[path]
        after, fixed_point = generator.render_proof_refresh(sources.root)
        assert fixed_point == outputs
        assert all(after.read(path) == raw for path, raw in fixed_point.items())
    sources.assert_current()


def test_full_refresh_is_deterministic_idempotent_and_keeps_native_population(
    refreshed_repository: tuple[ProofSources, dict[str, bytes]],
) -> None:
    before, expected = refreshed_repository
    after, actual = generator.render_proof_refresh(before.root)
    assert expected == actual
    assert all(after.read(path) == content for path, content in actual.items())
    generator.write_proof_refresh(after, actual, refresh_proof_hashes=True)
    before.assert_current()
    assert check_risks(before.root)["status"] == "admitted"
    native = admit_native_source(before.read(NATIVE_PATH))
    workflow = workflow_value(actual[WORKFLOW_PATH], WORKFLOW_PATH)
    for job_id, original in native.jobs.items():
        for field in (
            "steps",
            "strategy",
            "runs-on",
            "uses",
            "with",
            "permissions",
            "timeout-minutes",
        ):
            assert workflow["jobs"][job_id].get(field) == original.get(field), (
                job_id,
                field,
            )
    inventory = json.loads(actual[".ci-coordinator/self-ci-inventory.v1.json"])
    assert inventory["nativeJobIds"] == sorted(native.jobs)
    assert inventory["nativeAggregateResultJobs"] == list(native.gate_result_jobs)
    assert inventory["nativeAggregateOutputPredicates"] == [
        {
            "expression": native.requester_reason_expression,
            "equals": native.requester_reason,
        }
    ]
    inputs = {row["path"]: row["sha256"] for row in inventory["sourceInputs"]}
    assert inputs[RISK_PATH] == hashlib.sha256(actual[RISK_PATH]).hexdigest()
    assert not {DISPOSITION_PATH, BUNDLED_DISPOSITION_PATH}.intersection(inputs)
    rows = _rows(actual[DISPOSITION_PATH])
    for source_id in GROUPS:
        assert rows[source_id]["currentTarget"] == _expected_fingerprint(
            before.files, rows[source_id]
        )


@pytest.mark.parametrize("operation", ["--check", "--policy"])
def test_check_and_policy_cannot_accept_the_refresh_flag(
    operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.argv", ["self_ci_generate", operation, "--refresh-proof-hashes"])
    with pytest.raises(SystemExit) as error:
        generator.main()
    assert error.value.code == 2


def test_check_reports_stale_copy_without_refreshing(
    sources: ProofSources,
    refreshed_repository: tuple[ProofSources, dict[str, bytes]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _baseline, expected = refreshed_repository
    stale = _changed(
        sources,
        {BUNDLED_DISPOSITION_PATH: sources.read(BUNDLED_DISPOSITION_PATH) + b"\n"},
    )
    writes: list[Path] = []
    monkeypatch.setattr(generator, "render_proof_refresh", lambda _root: (stale, expected))
    monkeypatch.setattr(generator, "write_exact_file", lambda path, _content: writes.append(path))
    monkeypatch.setattr("sys.argv", ["self_ci_generate", "--check"])
    assert generator.main() == 1
    assert writes == []
