from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import py_compile
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from consumer_contract_lab_support import WORKFLOW_PATH, dynamic_policy

from ci_coordinator.consumer_contract_lab import git_source, source_epoch
from ci_coordinator.consumer_contract_lab.model import (
    ConsumerLabProfile,
    ConsumerLabScenario,
    ConsumerRepository,
    ExpectedOutcome,
    FileBinding,
    ScenarioChange,
    ScenarioCorpus,
)
from ci_coordinator.consumer_contract_lab.source_epoch import (
    ConsumerContractSourceError,
    prepare_consumer_contract,
)
from ci_coordinator.kernel import canonical_json
from ci_coordinator.target_artifacts import (
    parse_target_artifacts_source,
    render_target_artifacts,
)

_ROOT = Path(__file__).resolve().parents[4]
_FIXTURE = _ROOT / "fixtures/native-target-repository"
_PROFILE_PATH = ".ci-coordinator/local-lab-profile.v1.json"
_POLICY_PATH = ".ci-coordinator/dynamic-ci-policy.v1.json"
_SCENARIOS_PATH = ".ci-coordinator/local-lab-scenarios.v1.json"
_UNREACHABLE_WORKFLOW_PATH = ".github/workflows/unreachable-consumer-lab.yml"
_UNREACHABLE_WORKFLOW = b"""name: Unreachable Consumer Lab Fixture

"on":
  workflow_call:

jobs:
  noop:
    runs-on: ubuntu-24.04
    steps:
      - run: "true"
"""


@dataclass(frozen=True, slots=True)
class _LabEpoch:
    coordinator_root: Path
    target_root: Path
    coordinator_commit: str


def test_cli_round_trips_clean_source_epochs_to_one_stable_receipt(
    epoch: _LabEpoch, tmp_path: Path
) -> None:
    output = tmp_path / "consumer-lab-receipt.json"

    first = _run_cli(epoch, output)
    assert first.returncode == 0, first.stderr
    first_receipt = output.read_bytes()
    second = _run_cli(epoch, output)

    assert first.returncode == second.returncode == 0
    assert first.stderr == second.stderr == ""
    assert first.stdout == second.stdout
    assert output.read_bytes() == first_receipt
    report = json.loads(first.stdout)
    receipt = json.loads(first_receipt)
    assert report == {
        "code": "consumer_contract_lab_passed",
        "output": str(output),
        "providerEnforcement": False,
        "receiptId": receipt["receiptId"],
        "scenarioCount": 2,
        "targetJobsExecuted": False,
    }
    assert receipt["coordinatorSource"]["sourceKind"] == "commit"
    assert receipt["targetSource"]["sourceKind"] == "commit"
    assert receipt["executionAuthority"] == {
        "coordinate": receipt["targetSource"]["head"],
        "kind": "target-git-commit",
        "providerEvidence": False,
    }
    assert receipt["providerEnforcement"] is False
    assert receipt["targetJobsExecuted"] is False
    assert [
        (result["scenarioId"], result["mode"], result["selectedJobs"])
        for result in receipt["scenarioResults"]
    ] == [
        ("fallback-workflow", "fallback", []),
        ("selected-source", "selected", ["lint", "test"]),
    ]


def test_cli_ignores_unchecked_worktree_bytecode_and_executes_commit_image(
    epoch: _LabEpoch,
    tmp_path: Path,
) -> None:
    malicious = tmp_path / "malicious-cli.py"
    malicious.write_text("raise RuntimeError('unchecked bytecode executed')\n", encoding="utf-8")
    cli_source = epoch.coordinator_root / "backend/src/ci_coordinator/consumer_contract_lab/cli.py"
    cache_path = importlib.util.cache_from_source(str(cli_source))
    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
    py_compile.compile(
        str(malicious),
        cfile=cache_path,
        doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )

    completed = _run_cli(epoch, tmp_path / "unchecked-bytecode-receipt.json")

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "mutation",
    [
        "artifact-source",
        "coordinator-head",
        "corpus-digest",
        "generated-adapter",
        "lock-shape",
        "profile-revision",
    ],
)
def test_cli_rejects_source_epoch_mutations(
    epoch: _LabEpoch, tmp_path: Path, mutation: str
) -> None:
    if mutation == "artifact-source":
        source_path = epoch.target_root / ".ci-coordinator/target-artifacts-source.v1.json"
        source_path.write_bytes(b"{}\n")
        lock_path = epoch.target_root / ".ci-coordinator/target-artifacts.lock.v1.json"
        lock = json.loads(lock_path.read_bytes())
        lock["source"]["sha256"] = hashlib.sha256(source_path.read_bytes()).hexdigest()
        lock_path.write_bytes(canonical_json(lock) + b"\n")
    elif mutation == "coordinator-head":
        _commit(epoch.coordinator_root, "changed coordinator revision", allow_empty=True)
    elif mutation == "corpus-digest":
        path = epoch.target_root / _SCENARIOS_PATH
        path.write_bytes(path.read_bytes() + b"\n")
    elif mutation == "generated-adapter":
        path = epoch.target_root / ".ci-coordinator/ci-coordinator.cjs"
        path.write_bytes(path.read_bytes() + b"\n")
    elif mutation == "lock-shape":
        path = epoch.target_root / ".ci-coordinator/target-artifacts.lock.v1.json"
        lock = json.loads(path.read_bytes())
        lock["source"] = {}
        path.write_bytes(canonical_json(lock) + b"\n")
    elif mutation == "profile-revision":
        path = epoch.target_root / _PROFILE_PATH
        profile = json.loads(path.read_bytes())
        profile["expectedCoordinatorCommit"] = "0" * 40
        path.write_bytes(canonical_json(profile) + b"\n")
    else:
        raise AssertionError("unhandled source-epoch mutation")

    completed = _run_cli(epoch, tmp_path / f"{mutation}.json")

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert json.loads(completed.stderr) == {
        "code": "consumer_contract_lab_failed",
        "detail": "ConsumerContractSourceError",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "execution-topology",
        "gate-signal",
        "plan-control",
        "plan-request-ref",
        "workflow-closure",
    ],
)
def test_cli_rejects_coherently_resealed_workflow_authority_drift(
    epoch: _LabEpoch,
    tmp_path: Path,
    mutation: str,
) -> None:
    _reseal_workflow_authority_mutation(epoch, mutation)

    completed = _run_cli(epoch, tmp_path / f"workflow-{mutation}.json")

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert json.loads(completed.stderr) == {
        "code": "consumer_contract_lab_failed",
        "detail": "ConsumerContractSourceError",
    }


def test_post_execution_source_check_rejects_changed_contract_bytes(
    epoch: _LabEpoch, tmp_path: Path
) -> None:
    script = "\n".join(
        (
            "import sys",
            "from pathlib import Path",
            "from ci_coordinator.consumer_contract_lab.source_epoch import (",
            "    ConsumerContractSourceError,",
            "    assert_consumer_contract_unchanged,",
            "    prepare_consumer_contract,",
            ")",
            "coordinator, target = map(Path, sys.argv[1:])",
            "contract = prepare_consumer_contract(",
            "    coordinator_root=coordinator,",
            f"    coordinator_commit={epoch.coordinator_commit!r},",
            "    coordinator_package_root=coordinator / 'backend/src/ci_coordinator',",
            "    target_root=target,",
            f"    profile_path=Path({_PROFILE_PATH!r}),",
            ")",
            f"path = target / {_POLICY_PATH!r}",
            "path.write_bytes(path.read_bytes() + b' ')",
            "try:",
            "    assert_consumer_contract_unchanged(contract)",
            "except ConsumerContractSourceError:",
            "    pass",
            "else:",
            "    raise SystemExit('changed contract bytes were accepted')",
        )
    )

    completed = subprocess.run(
        (
            sys.executable,
            "-c",
            script,
            str(epoch.coordinator_root),
            str(epoch.target_root),
        ),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env=_python_environment(epoch),
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("index_flag", ["--assume-unchanged", "--skip-worktree"])
def test_cli_uses_commit_image_despite_worktree_changes_hidden_by_index_flags(
    epoch: _LabEpoch,
    tmp_path: Path,
    index_flag: str,
) -> None:
    relative = "backend/src/ci_coordinator/consumer_contract_lab/model.py"
    _git(epoch.coordinator_root, "update-index", index_flag, relative)
    path = epoch.coordinator_root / relative
    path.write_bytes(path.read_bytes() + b"\n")

    output = tmp_path / "hidden-coordinator-change.json"
    completed = _run_cli(epoch, output)

    assert completed.returncode == 0, completed.stderr
    assert json.loads(output.read_bytes())["coordinatorSource"]["head"] == (
        epoch.coordinator_commit
    )


@pytest.mark.parametrize("index_flag", ["--assume-unchanged", "--skip-worktree"])
def test_post_execution_source_check_rejects_hidden_coordinator_changes(
    epoch: _LabEpoch,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    index_flag: str,
) -> None:
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(source_epoch, "require_loaded_source", lambda *_args: None)
    contract = prepare_consumer_contract(
        coordinator_root=epoch.coordinator_root,
        coordinator_commit=epoch.coordinator_commit,
        coordinator_package_root=epoch.coordinator_root / "backend/src/ci_coordinator",
        target_root=epoch.target_root,
        profile_path=Path(_PROFILE_PATH),
    )
    relative = "backend/src/ci_coordinator/consumer_contract_lab/model.py"
    _git(epoch.coordinator_root, "update-index", index_flag, relative)
    path = epoch.coordinator_root / relative
    path.write_bytes(path.read_bytes() + b"\n")

    with pytest.raises(ConsumerContractSourceError, match="coordinator package bytes"):
        source_epoch.assert_consumer_contract_unchanged(contract)


def test_source_epoch_rejects_bytes_changed_after_parsing(
    epoch: _LabEpoch,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_read = source_epoch._read_regular
    changed = False

    def read_then_change(root: Path, relative: str, *, opaque: bool = False) -> bytes:
        nonlocal changed
        content = original_read(root, relative, opaque=opaque)
        if root == epoch.target_root and relative == _PROFILE_PATH and not changed:
            profile = json.loads(content)
            profile["repository"]["defaultBranch"] = "release"
            (root / relative).write_bytes(canonical_json(profile) + b"\n")
            changed = True
        return content

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(source_epoch, "require_loaded_source", lambda *_args: None)
    monkeypatch.setattr(source_epoch, "_read_regular", read_then_change)

    with pytest.raises(ConsumerContractSourceError, match="changed before source-epoch"):
        prepare_consumer_contract(
            coordinator_root=epoch.coordinator_root,
            coordinator_commit=epoch.coordinator_commit,
            coordinator_package_root=epoch.coordinator_root / "backend/src/ci_coordinator",
            target_root=epoch.target_root,
            profile_path=Path(_PROFILE_PATH),
        )


def test_git_snapshot_rejects_head_change_during_inspection(
    epoch: _LabEpoch,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_git = git_source._git
    changed = False

    def inspect_then_change(root: Path, arguments: tuple[str, ...]) -> bytes:
        nonlocal changed
        content = original_git(root, arguments)
        if (
            root == epoch.target_root
            and arguments == ("rev-parse", "--verify", "HEAD^{commit}")
            and not changed
        ):
            _commit(root, "change target revision during inspection", allow_empty=True)
            changed = True
        return content

    monkeypatch.setattr(git_source, "_git", inspect_then_change)

    with pytest.raises(git_source.GitSourceError, match="head changed"):
        git_source.git_snapshot(
            epoch.target_root,
            relevant_files=(
                (
                    _PROFILE_PATH,
                    (epoch.target_root / _PROFILE_PATH).read_bytes(),
                ),
            ),
        )


def test_ignored_contract_file_marks_target_as_worktree(epoch: _LabEpoch, tmp_path: Path) -> None:
    relative = ".ci-coordinator/ignored-input.json"
    content = b'{"kind":"ignored-contract-input"}\n'
    (epoch.target_root / ".git/info/exclude").write_text(f"{relative}\n", encoding="utf-8")
    _write(epoch.target_root / relative, content)
    profile_path = epoch.target_root / _PROFILE_PATH
    profile = json.loads(profile_path.read_bytes())
    profile["targetBindings"].append(
        {"path": relative, "sha256": hashlib.sha256(content).hexdigest()}
    )
    profile["targetBindings"].sort(key=lambda item: item["path"].encode("utf-16-be"))
    profile_path.write_bytes(canonical_json(profile) + b"\n")
    _commit(epoch.target_root, "bind ignored contract input")
    output = tmp_path / "ignored-receipt.json"

    completed = _run_cli(epoch, output)

    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(output.read_bytes())
    assert receipt["targetSource"] == {
        "dirtyPaths": [relative],
        "head": _git(epoch.target_root, "rev-parse", "--verify", "HEAD^{commit}").strip(),
        "sourceKind": "worktree",
    }
    assert receipt["executionAuthority"]["kind"] == "sealed-worktree-manifest"
    assert receipt["executionAuthority"]["providerEvidence"] is False
    assert len(receipt["executionAuthority"]["coordinate"]) == 40
    assert receipt["executionAuthority"]["coordinate"] != receipt["targetSource"]["head"]


@pytest.mark.parametrize("content", [b"", b"\x00"])
def test_opaque_target_binding_admits_the_complete_bounded_byte_domain(
    epoch: _LabEpoch,
    tmp_path: Path,
    content: bytes,
) -> None:
    relative = ".ci-coordinator/opaque-input.bin"
    _write(epoch.target_root / relative, content)
    profile_path = epoch.target_root / _PROFILE_PATH
    profile = json.loads(profile_path.read_bytes())
    profile["targetBindings"].append(
        {"path": relative, "sha256": hashlib.sha256(content).hexdigest()}
    )
    profile["targetBindings"].sort(key=lambda item: item["path"].encode("utf-16-be"))
    profile_path.write_bytes(canonical_json(profile) + b"\n")
    _commit(epoch.target_root, "bind opaque contract input")
    output = tmp_path / "opaque-receipt.json"

    completed = _run_cli(epoch, output)

    assert completed.returncode == 0, completed.stderr
    manifest = json.loads(output.read_bytes())["contractManifest"]
    opaque_entry = next(entry for entry in manifest if entry["path"] == relative)
    assert opaque_entry == {
        "path": relative,
        "sha256": hashlib.sha256(content).hexdigest(),
        "sizeBytes": len(content),
    }


def test_unrelated_worktree_file_is_outside_contract_source_status(
    epoch: _LabEpoch, tmp_path: Path
) -> None:
    _write(epoch.target_root / "local-notes.txt", b"not a contract input\n")
    output = tmp_path / "unrelated-worktree-file.json"

    completed = _run_cli(epoch, output)

    assert completed.returncode == 0, completed.stderr
    assert json.loads(output.read_bytes())["targetSource"] == {
        "dirtyPaths": [],
        "head": _git(epoch.target_root, "rev-parse", "--verify", "HEAD^{commit}").strip(),
        "sourceKind": "commit",
    }


@pytest.mark.parametrize("default_branch", ["\x00", "HEAD"])
def test_cli_classifies_invalid_default_branch(
    epoch: _LabEpoch,
    tmp_path: Path,
    default_branch: str,
) -> None:
    profile_path = epoch.target_root / _PROFILE_PATH
    profile = json.loads(profile_path.read_bytes())
    profile["repository"]["defaultBranch"] = default_branch
    profile_path.write_bytes(canonical_json(profile) + b"\n")

    completed = _run_cli(epoch, tmp_path / "invalid-branch.json")

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert json.loads(completed.stderr) == {
        "code": "consumer_contract_lab_failed",
        "detail": "ConsumerLabAdmissionError",
    }


@pytest.fixture(scope="module")
def epoch_seed(tmp_path_factory: pytest.TempPathFactory) -> _LabEpoch:
    return _build_epoch(tmp_path_factory.mktemp("consumer-lab-seed"))


@pytest.fixture
def epoch(epoch_seed: _LabEpoch, tmp_path: Path) -> _LabEpoch:
    return _copy_epoch(epoch_seed, tmp_path)


def _copy_epoch(seed: _LabEpoch, root: Path) -> _LabEpoch:
    coordinator = shutil.copytree(seed.coordinator_root, root / "coordinator")
    target = shutil.copytree(seed.target_root, root / "target")
    return _LabEpoch(Path(coordinator), Path(target), seed.coordinator_commit)


def test_epoch_copies_keep_independent_git_and_worktree_state(
    epoch: _LabEpoch,
    epoch_seed: _LabEpoch,
    tmp_path: Path,
) -> None:
    sibling = _copy_epoch(epoch_seed, tmp_path / "sibling")
    for changed, original, other in (
        (epoch.coordinator_root, epoch_seed.coordinator_root, sibling.coordinator_root),
        (epoch.target_root, epoch_seed.target_root, sibling.target_root),
    ):
        before = (original / ".git/config").read_bytes()
        (changed / ".git/config").write_bytes(before + b"\n# isolated mutation\n")
        (changed / "untracked").write_text("isolated", encoding="ascii")
        assert (original / ".git/config").read_bytes() == before
        assert (other / ".git/config").read_bytes() == before
        assert not (original / "untracked").exists()
        assert not (other / "untracked").exists()
        assert (changed / ".git/HEAD").read_bytes() == (other / ".git/HEAD").read_bytes()


def _build_epoch(root: Path) -> _LabEpoch:
    coordinator = root / "coordinator"
    package = coordinator / "backend/src/ci_coordinator"
    shutil.copytree(
        _ROOT / "backend/src/ci_coordinator",
        package,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    coordinator_commit = _initialize_repository(coordinator, "consumer lab coordinator")

    target = root / "target"
    workflow = (_FIXTURE / WORKFLOW_PATH).read_bytes()
    source_mapping = json.loads(
        (_FIXTURE / ".ci-coordinator/target-artifacts-source.v1.json").read_bytes()
    )
    source_mapping["generator"]["version"] = coordinator_commit
    source_mapping["adapterWorkflowFiles"][0]["sha256"] = hashlib.sha256(workflow).hexdigest()
    source_mapping["dependencyGraph"]["nodes"] = [
        {"path": "docs/guide.md", "dependents": [], "riskClasses": ["docs"]},
        {"path": "src/service.py", "dependents": [], "riskClasses": ["source"]},
    ]
    source_content = canonical_json(source_mapping) + b"\n"
    source = parse_target_artifacts_source(source_content)
    rendered = render_target_artifacts(source)

    _write(target / WORKFLOW_PATH, workflow)
    _write(target / ".ci-coordinator/target-artifacts-source.v1.json", source_content)
    for filename, content in rendered.by_filename():
        _write(target / ".ci-coordinator" / filename, content)
    lock = {
        "schemaVersion": "ci-coordinator-target-artifact-lock/v1",
        "coordinatorCommit": coordinator_commit,
        "source": {
            "path": "target-artifacts-source.v1.json",
            "sha256": hashlib.sha256(source_content).hexdigest(),
        },
        "artifacts": [
            {"path": filename, "sha256": hashlib.sha256(content).hexdigest()}
            for filename, content in rendered.by_filename()
        ],
    }
    _write(
        target / ".ci-coordinator/target-artifacts.lock.v1.json",
        canonical_json(lock) + b"\n",
    )
    policy_content = canonical_json(dynamic_policy(coordinator_commit)) + b"\n"
    _write(target / _POLICY_PATH, policy_content)
    corpus = ScenarioCorpus(
        "native-consumer",
        (
            ConsumerLabScenario(
                "fallback-workflow",
                "pull_request",
                (ScenarioChange(WORKFLOW_PATH, "modified", None),),
                ExpectedOutcome("fallback", ()),
            ),
            ConsumerLabScenario(
                "selected-source",
                "pull_request",
                (ScenarioChange("src/service.py", "modified", None),),
                ExpectedOutcome("selected", ("lint", "test")),
            ),
        ),
    )
    corpus_content = canonical_json(corpus.to_mapping()) + b"\n"
    _write(target / _SCENARIOS_PATH, corpus_content)
    profile = ConsumerLabProfile(
        profile_id="native-consumer",
        expected_coordinator_commit=coordinator_commit,
        repository=ConsumerRepository(100, 200, "example-org", "consumer", "master"),
        workflow_path=WORKFLOW_PATH,
        event_surface=("pull_request",),
        target_artifacts_directory=".ci-coordinator",
        dynamic_policy=FileBinding(_POLICY_PATH, hashlib.sha256(policy_content).hexdigest()),
        scenario_corpus=FileBinding(
            _SCENARIOS_PATH,
            hashlib.sha256(corpus_content).hexdigest(),
        ),
        target_bindings=(FileBinding(WORKFLOW_PATH, hashlib.sha256(workflow).hexdigest()),),
    )
    _write(target / _PROFILE_PATH, canonical_json(profile.to_mapping()) + b"\n")
    _initialize_repository(target, "consumer lab target")
    return _LabEpoch(coordinator, target, coordinator_commit)


def _run_cli(epoch: _LabEpoch, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            sys.executable,
            "-m",
            "ci_coordinator.consumer_contract_lab.bootstrap",
            "--coordinator-root",
            str(epoch.coordinator_root),
            "--target-root",
            str(epoch.target_root),
            "--profile",
            _PROFILE_PATH,
            "--output",
            str(output),
        ),
        cwd=epoch.coordinator_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env=_python_environment(epoch),
    )


def _reseal_workflow_authority_mutation(epoch: _LabEpoch, mutation: str) -> None:
    workflow_path = epoch.target_root / WORKFLOW_PATH
    workflow = workflow_path.read_bytes()
    source_path = epoch.target_root / ".ci-coordinator/target-artifacts-source.v1.json"
    source_mapping = json.loads(source_path.read_bytes())
    if mutation == "workflow-closure":
        _write(epoch.target_root / _UNREACHABLE_WORKFLOW_PATH, _UNREACHABLE_WORKFLOW)
        source_mapping["adapterWorkflowFiles"].append(
            {
                "path": _UNREACHABLE_WORKFLOW_PATH,
                "sha256": hashlib.sha256(_UNREACHABLE_WORKFLOW).hexdigest(),
            }
        )
        source_mapping["adapterWorkflowFiles"].sort(
            key=lambda item: item["path"].encode("utf-16-be")
        )
    else:
        replacements = {
            "execution-topology": (
                b"    needs: [lint, plan]\n",
                b"    needs: plan\n",
            ),
            "gate-signal": (
                b"  full-check-gate:\n    name: Full Check\n",
                b"  full-check-gate:\n    name: Full Check Drift\n",
            ),
            "plan-control": (
                (
                    b"  plan:\n"
                    b"    name: Resolve dynamic CI plan\n"
                    b"    needs: plan-request\n"
                    b"    if: ${{ !cancelled() }}\n"
                    b"    runs-on: ubuntu-24.04\n"
                ),
                (
                    b"  plan:\n"
                    b"    name: Resolve dynamic CI plan\n"
                    b"    needs: plan-request\n"
                    b"    if: ${{ !cancelled() }}\n"
                    b"    runs-on: ubuntu-22.04\n"
                ),
            ),
            "plan-request-ref": (
                b"trusted-plan-request.yml@" + b"1" * 40,
                b"trusted-plan-request.yml@" + b"2" * 40,
            ),
        }
        original, replacement = replacements[mutation]
        assert workflow.count(original) == 1
        workflow = workflow.replace(original, replacement)
        workflow_path.write_bytes(workflow)
        adapter_binding = next(
            binding
            for binding in source_mapping["adapterWorkflowFiles"]
            if binding["path"] == WORKFLOW_PATH
        )
        adapter_binding["sha256"] = hashlib.sha256(workflow).hexdigest()
    source_content = canonical_json(source_mapping) + b"\n"
    source = parse_target_artifacts_source(source_content)
    rendered = render_target_artifacts(source)
    source_path.write_bytes(source_content)
    for filename, content in rendered.by_filename():
        (epoch.target_root / ".ci-coordinator" / filename).write_bytes(content)

    lock_path = epoch.target_root / ".ci-coordinator/target-artifacts.lock.v1.json"
    lock = json.loads(lock_path.read_bytes())
    lock["source"]["sha256"] = hashlib.sha256(source_content).hexdigest()
    lock["artifacts"] = [
        {"path": filename, "sha256": hashlib.sha256(content).hexdigest()}
        for filename, content in rendered.by_filename()
    ]
    lock_path.write_bytes(canonical_json(lock) + b"\n")

    profile_path = epoch.target_root / _PROFILE_PATH
    profile = json.loads(profile_path.read_bytes())
    profile_binding = next(
        binding for binding in profile["targetBindings"] if binding["path"] == WORKFLOW_PATH
    )
    profile_binding["sha256"] = hashlib.sha256(workflow).hexdigest()
    profile_path.write_bytes(canonical_json(profile) + b"\n")


def _python_environment(epoch: _LabEpoch) -> dict[str, str]:
    return {
        **{
            name: os.environ[name]
            for name in ("COVERAGE_PROCESS_CONFIG", "COVERAGE_PROCESS_START")
            if name in os.environ
        },
        "HOME": str(epoch.coordinator_root),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.environ["PATH"],
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONPATH": str(epoch.coordinator_root / "backend/src"),
        "TZ": "UTC",
    }


def _initialize_repository(root: Path, message: str) -> str:
    _git(root, "init", "--quiet", "--initial-branch=master")
    # The frozen fixture is copied; background maintenance must not remove its objects.
    _git(root, "config", "gc.auto", "0")
    _git(root, "config", "maintenance.auto", "false")
    return _commit(root, message)


def _commit(root: Path, message: str, *, allow_empty: bool = False) -> str:
    _git(root, "add", "--all")
    arguments = [
        "-c",
        "commit.gpgsign=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "user.email=consumer-lab@example.invalid",
        "-c",
        "user.name=Consumer Lab",
        "commit",
        "--quiet",
        "-m",
        message,
    ]
    if allow_empty:
        arguments.append("--allow-empty")
    _git(root, *arguments)
    return _git(root, "rev-parse", "--verify", "HEAD^{commit}").strip()


def _git(root: Path, *arguments: str) -> str:
    git = shutil.which("git")
    assert git is not None
    environment = {
        "GIT_AUTHOR_DATE": "2026-07-30T12:00:00Z",
        "GIT_COMMITTER_DATE": "2026-07-30T12:00:00Z",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(root),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.environ["PATH"],
    }
    completed = subprocess.run(
        (git, "-C", str(root), *arguments),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
