from __future__ import annotations

import hashlib
import json
import os
import selectors
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import IO, cast

import pytest
from consumer_contract_lab_support import WORKFLOW_PATH, dynamic_policy
from jsonschema import Draft202012Validator
from package_b_support import PLAN_REQUEST_WORKFLOW_REF
from process_timeout_support import descendant_timeout_probe

from ci_coordinator.consumer_contract_lab import bootstrap as consumer_bootstrap
from ci_coordinator.consumer_contract_lab import node_runtime
from ci_coordinator.consumer_contract_lab import process as consumer_process
from ci_coordinator.consumer_contract_lab.bootstrap import (
    ConsumerLabBootstrapError,
    _capture_bounded,
)
from ci_coordinator.consumer_contract_lab.codec import (
    ConsumerLabAdmissionError,
    parse_consumer_lab_profile,
)
from ci_coordinator.consumer_contract_lab.composition import issue_scenario
from ci_coordinator.consumer_contract_lab.git_source import (
    GitSourceError,
    read_regular,
    relative_path,
)
from ci_coordinator.consumer_contract_lab.model import (
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
)
from ci_coordinator.consumer_contract_lab.node_executable import (
    NodeExecutableError,
    admit_node_executable,
)
from ci_coordinator.consumer_contract_lab.node_runtime import (
    ConsumerControlError,
    execute_consumer_controls,
)
from ci_coordinator.consumer_contract_lab.process import LifetimeScope, run_bounded
from ci_coordinator.consumer_contract_lab.runner import ConsumerLabReceipt
from ci_coordinator.consumer_contract_lab.source_epoch import PreparedConsumerContract
from ci_coordinator.execution_orchestration import (
    LOCAL_PLAN_REQUEST_WORKFLOW_PATH,
    LOCAL_PLAN_REQUEST_WORKFLOW_REF,
    parse_target_execution_registry,
)
from ci_coordinator.kernel import canonical_json, hash_object, utf16_sort_key
from ci_coordinator.target_artifacts import (
    parse_target_artifacts_source,
    render_target_artifacts,
)
from ci_coordinator.target_artifacts.requester import render_plan_requester

_ROOT = Path(__file__).resolve().parents[4]
_FIXTURE = _ROOT / "fixtures/native-target-repository"


def test_native_managed_leaf_retains_exact_lease_identity(
    managed_consumer_lifetime: tuple[LifetimeScope, int, int],
    tmp_path: Path,
) -> None:
    scope, _, _ = managed_consumer_lifetime
    descriptors = scope.inherited_fds
    expected = [[fd, os.fstat(fd).st_dev, os.fstat(fd).st_ino] for fd in descriptors]
    result = run_bounded(
        sys.executable,
        (
            "-I",
            "-S",
            "-c",
            "import json,os,sys\nrows=[]\n"
            "try:\n    for value in sys.argv[1:]:\n"
            "        fd=int(value); info=os.fstat(fd); rows.append([fd,info.st_dev,info.st_ino])\n"
            "except OSError: pass\nprint(json.dumps(rows))\n",
            *map(str, descriptors),
        ),
        cwd=tmp_path,
        max_output_bytes=65_536,
        timeout_seconds=10,
        env={},
    )
    assert result.status == 0 and result.error is None, result
    assert json.loads(result.stdout) == expected and expected
    assert result.process_group_quiescent is True
    assert consumer_process.current_lifetime() is scope
    for descriptor in descriptors:
        os.fstat(descriptor)


def test_native_unobserved_group_is_never_a_success_receipt(
    managed_consumer_lifetime: tuple[LifetimeScope, int, int],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = ("-I", "-S", "-c", "raise SystemExit(0)")
    deadline = time.monotonic() + 2
    positive = run_bounded(
        sys.executable,
        command,
        cwd=tmp_path,
        max_output_bytes=4096,
        timeout_seconds=5,
        execution_deadline=deadline,
        env={},
    )
    assert positive.status == 0 and positive.process_group_quiescent is True, positive
    monkeypatch.setattr(consumer_process, "_group_alive", lambda _group, **_kwargs: True)
    unavailable = run_bounded(
        sys.executable,
        command,
        cwd=tmp_path,
        max_output_bytes=4096,
        timeout_seconds=5,
        execution_deadline=deadline,
        env={},
    )
    assert unavailable.status is None and unavailable.error is not None
    assert unavailable.process_group_quiescent is False


def test_native_managed_leaf_cancellation_drains_before_return(
    managed_consumer_lifetime: tuple[LifetimeScope, int, int],
    tmp_path: Path,
) -> None:
    scope, writer, _ = managed_consumer_lifetime
    marker = tmp_path / "leaf.json"
    expected = [(fd, os.fstat(fd).st_dev, os.fstat(fd).st_ino) for fd in scope.inherited_fds]
    code = (
        "import json,os,signal,sys,time\nfrom pathlib import Path\n"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
        f"expected={expected!r}\nretained=True\n"
        "try:\n    for fd,device,inode in expected:\n"
        "        actual=os.fstat(fd)\n"
        "        retained = retained and (actual.st_dev,actual.st_ino)==(device,inode)\n"
        "except OSError: retained=False\n"
        f"marker=Path({str(marker)!r}); temporary=marker.with_suffix('.tmp')\n"
        "temporary.write_text(json.dumps({'pid':os.getpid(),'retained':retained}))\n"
        "temporary.replace(marker)\n"
        "while True: time.sleep(1)\n"
    )
    observations: list[dict[str, object]] = []
    failures: list[BaseException] = []

    def request_stop() -> None:
        try:
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            observation = json.loads(marker.read_bytes())
            observations.append(observation)
            os.write(writer, struct.pack("!4sd", b"CF1:", time.monotonic() + 1))
        except BaseException as error:
            failures.append(error)

    sender = threading.Thread(target=request_stop)
    sender.start()
    try:
        with pytest.raises(consumer_process.LifetimeCancelled):
            run_bounded(
                sys.executable,
                ("-I", "-S", "-c", code),
                cwd=tmp_path,
                max_output_bytes=4096,
                timeout_seconds=10,
                env={},
            )
        sender.join(timeout=6)
        assert not sender.is_alive() and not failures, failures
        assert len(observations) == 1 and observations[0]["retained"] is True
        pid = observations[0]["pid"]
        assert type(pid) is int
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        assert consumer_process.current_lifetime() is scope
        for descriptor in scope.inherited_fds:
            os.fstat(descriptor)
    finally:
        sender.join(timeout=6)
        if observations:
            pid = observations[0]["pid"]
            if type(pid) is int:
                with suppress(ProcessLookupError):
                    os.killpg(pid, signal.SIGKILL)


@pytest.mark.parametrize("remaining", [-1.0, 0.0, 1.0])
def test_bootstrap_cleanup_checkpoint_preserves_typed_expiry(
    managed_consumer_lifetime: tuple[LifetimeScope, int, int],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    remaining: float,
) -> None:
    parent, _, _ = managed_consumer_lifetime
    identity = tmp_path.stat()
    scope = consumer_bootstrap._SourceCleanupScope(
        parent, tmp_path, (identity.st_dev, identity.st_ino), _ceiling=10.0
    )
    with monkeypatch.context() as patch, consumer_bootstrap.borrowed_lifetime(scope):
        patch.setattr(time, "monotonic", lambda: 10.0 - remaining)
        if remaining <= 0:
            with pytest.raises(ConsumerLabBootstrapError, match="cleanup allowance exhausted"):
                consumer_bootstrap.assert_lifetime_running()
        else:
            consumer_bootstrap.assert_lifetime_running()


@pytest.mark.parametrize("owner", ["bootstrap", "process"])
@pytest.mark.parametrize(
    "cleanup,outcome",
    [
        (cleanup, outcome)
        for cleanup in ("complete", "expired")
        for outcome in ("normal", "ambient", "primary", "interrupt")
    ]
    + [("retained", "ambient"), ("interrupted", "ambient")],
)
def test_native_owned_lab_cleanup_has_no_later_recursive_finalizer(
    managed_consumer_lifetime: tuple[LifetimeScope, int, int],
    monkeypatch: pytest.MonkeyPatch,
    cleanup: str,
    owner: str,
    outcome: str,
) -> None:
    scope, writer, _ = managed_consumer_lifetime
    expired = cleanup == "expired"
    module = consumer_bootstrap if owner == "bootstrap" else consumer_process
    cleanup_interrupt = consumer_process.LifetimeCancelled(signal.SIGTERM)
    if cleanup == "retained":
        monkeypatch.setattr(module, "_DELETE_TREE", "import sys")
    elif cleanup == "interrupted":

        def interrupted(*_args: object, **_kwargs: object) -> None:
            raise cleanup_interrupt

        monkeypatch.setattr(
            module, "_capture_bounded" if owner == "bootstrap" else "run_bounded", interrupted
        )
    root: Path | None = None
    context = (
        consumer_bootstrap._owned_source_directory()
        if owner == "bootstrap"
        else consumer_process.owned_temporary_directory(prefix="lab-owned-cut-")
    )
    failure = (
        consumer_bootstrap.ConsumerLabBootstrapError
        if owner == "bootstrap"
        else consumer_process.ConsumerLabCleanupError
    )
    primary = (
        consumer_process.LifetimeCancelled(signal.SIGTERM)
        if outcome == "interrupt"
        else ValueError("owned directory body failure")
    )

    def invoke() -> None:
        nonlocal root
        with context as temporary:
            root = Path(temporary)
            (root / "owned.txt").write_text("owned data")
            if expired:
                os.write(writer, struct.pack("!4sd", b"CF1:", time.monotonic() - 1))
            if outcome in {"primary", "interrupt"}:
                raise primary

    def observe() -> None:
        if cleanup == "interrupted":
            with pytest.raises(type(cleanup_interrupt)) as interrupted_cleanup:
                invoke()
            assert interrupted_cleanup.value is cleanup_interrupt
        elif outcome in {"primary", "interrupt"}:
            with pytest.raises(type(primary)) as caught:
                invoke()
            assert caught.value is primary
            assert bool(getattr(primary, "__notes__", [])) is expired
        elif cleanup in {"expired", "retained"}:
            with pytest.raises(failure):
                invoke()
        else:
            invoke()

    try:
        if outcome == "ambient":
            ambient = ValueError("already-handled directory caller error")
            try:
                raise ambient
            except ValueError:
                observe()
                assert not getattr(ambient, "__notes__", [])
        else:
            observe()
        assert root is not None
        if cleanup != "complete":
            assert (root / "owned.txt").read_text() == "owned data"
        else:
            assert not root.exists()
        assert consumer_process.current_lifetime() is scope
        for descriptor in scope.inherited_fds:
            os.fstat(descriptor)
    finally:
        if root is not None:
            shutil.rmtree(root, ignore_errors=True)


def _profile(
    commit: str,
    event_surface: tuple[EventName, ...] = ("pull_request",),
) -> ConsumerLabProfile:
    return ConsumerLabProfile(
        profile_id="native-consumer",
        expected_coordinator_commit=commit,
        repository=ConsumerRepository(100, 200, "example-org", "consumer", "master"),
        workflow_path=WORKFLOW_PATH,
        event_surface=event_surface,
        target_artifacts_directory=".ci-coordinator",
        dynamic_policy=FileBinding("dynamic-ci-policy.v1.json", "1" * 64),
        scenario_corpus=FileBinding("local-lab-scenarios.v1.json", "2" * 64),
        target_bindings=(),
    )


@pytest.mark.parametrize("local_requester", [False, True])
@pytest.mark.parametrize(
    ("event_name", "path", "mode", "selected_jobs"),
    [
        (event_name, path, mode, selected_jobs)
        for event_name in ("merge_group", "pull_request", "push")
        for path, mode, selected_jobs in (
            ("src/service.py", "selected", ("lint", "test")),
            (WORKFLOW_PATH, "fallback", ()),
        )
    ],
)
def test_real_path_round_trips_exact_generated_consumer_controls(
    tmp_path: Path,
    event_name: str,
    path: str,
    mode: str,
    selected_jobs: tuple[str, ...],
    local_requester: bool,
) -> None:
    admitted_event = cast(EventName, event_name)
    contract = _contract(tmp_path, event_surface=(admitted_event,), local_requester=local_requester)
    scenario = ConsumerLabScenario(
        scenario_id=f"{event_name}-{mode}",
        event_name=admitted_event,
        changes=(ScenarioChange(path, "modified", None),),
        expected_outcome=ExpectedOutcome(cast(ExpectedMode, mode), selected_jobs),
    )

    issued = issue_scenario(contract, scenario, scenario_index=1)
    result = execute_consumer_controls(
        contract,
        scenario,
        issued,
    )

    assert (result.mode, result.selected_jobs) == (mode, selected_jobs)
    assert issued.request.event_name == event_name
    if event_name == "pull_request":
        assert issued.request.pull_request_number == 1
        assert issued.request.merge_group_head_ref is None
        assert issued.request.ref == "refs/pull/1/merge"
    elif event_name == "merge_group":
        assert issued.request.pull_request_number is None
        assert issued.request.merge_group_head_ref == issued.request.ref
        assert issued.request.ref.startswith("refs/heads/gh-readonly-queue/master/")
    else:
        assert issued.request.pull_request_number is None
        assert issued.request.merge_group_head_ref is None
        assert issued.request.ref == "refs/heads/master"
    expected_selected_only_calls = 1 if mode == "selected" else 0
    assert (
        issued.capacity_input_calls,
        issued.reconciliation_registration_count,
    ) == (expected_selected_only_calls, expected_selected_only_calls)


def test_runtime_admission_precedes_target_image_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _contract(tmp_path)
    scenario = contract.corpus.scenarios[0]
    issued = issue_scenario(contract, scenario, scenario_index=1)

    def reject_runtime(coordinator_root: Path, target_root: Path) -> str:
        assert coordinator_root == contract.coordinator_root
        assert target_root == contract.target_root
        assert coordinator_root != target_root
        raise NodeExecutableError("runtime rejected")

    def forbidden_image(*_args: object) -> None:
        pytest.fail("target image preceded runtime admission")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(node_runtime, "admit_node_executable", reject_runtime)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(node_runtime, "target_execution_image", forbidden_image)
    with pytest.raises(ConsumerControlError, match="evidence is unavailable"):
        execute_consumer_controls(contract, scenario, issued)


def test_consumer_controls_reject_target_expectation_divergence(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    scenario = ConsumerLabScenario(
        "incorrect-expectation",
        "pull_request",
        (ScenarioChange("src/service.py", "modified", None),),
        ExpectedOutcome("fallback", ()),
    )

    with pytest.raises(ConsumerControlError, match="target-owned expectation"):
        execute_consumer_controls(
            contract,
            scenario,
            issue_scenario(contract, scenario, scenario_index=1),
        )


def test_receipt_is_content_addressed_and_denies_provider_claims(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    scenario = ConsumerLabScenario(
        "source-change",
        "pull_request",
        (ScenarioChange("src/service.py", "modified", None),),
        ExpectedOutcome("selected", ("lint", "test")),
    )
    result = execute_consumer_controls(
        contract,
        scenario,
        issue_scenario(contract, scenario, scenario_index=1),
    )
    body = {
        "profileId": contract.profile.profile_id,
        "sourceEpochId": contract.source_epoch_id,
        "coordinatorSource": contract.coordinator_source.to_mapping(),
        "targetSource": contract.target_source.to_mapping(),
        "contractManifestSha256": contract.manifest_sha256,
        "contractManifest": [item.to_mapping() for item in contract.manifest_entries],
        "executionAuthority": contract.execution_authority.to_mapping(),
        "authority": "lab-fixture",
        "providerEnforcement": False,
        "targetJobsExecuted": False,
        "scenarioResults": [result.to_mapping()],
    }
    receipt = ConsumerLabReceipt(
        "consumer_lab_" + hash_object(body)[:32],
        contract,
        (result,),
    )

    decoded = json.loads(receipt.canonical_bytes())
    assert decoded["providerEnforcement"] is False
    assert decoded["targetJobsExecuted"] is False
    assert decoded["executionAuthority"] == {
        "coordinate": contract.target_source.head,
        "kind": "target-git-commit",
        "providerEvidence": False,
    }
    assert decoded["scenarioResults"][0]["jobResultKind"] == "synthetic-result"


def test_profile_codec_rejects_noncanonical_and_unknown_event() -> None:
    profile = _profile("a" * 40)
    canonical = canonical_json(profile.to_mapping()) + b"\n"
    assert parse_consumer_lab_profile(canonical) == profile
    with pytest.raises(ConsumerLabAdmissionError):
        parse_consumer_lab_profile(b" " + canonical)
    invalid = profile.to_mapping()
    invalid["eventSurface"] = ["workflow_dispatch"]
    with pytest.raises(ConsumerLabAdmissionError):
        parse_consumer_lab_profile(canonical_json(invalid) + b"\n")
    invalid = profile.to_mapping()
    repository = cast(dict[str, object], invalid["repository"])
    repository["defaultBranch"] = "invalid..branch"
    with pytest.raises(ConsumerLabAdmissionError):
        parse_consumer_lab_profile(canonical_json(invalid) + b"\n")


def test_plan_key_identity_is_unique_per_scenario(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    source = contract.corpus.scenarios[0]
    workflow = contract.corpus.scenarios[1]

    source_issue = issue_scenario(contract, source, scenario_index=1)
    workflow_issue = issue_scenario(contract, workflow, scenario_index=2)

    assert source_issue.key_id != workflow_issue.key_id
    assert source_issue.public_key_pem != workflow_issue.public_key_pem


def test_regular_file_reader_rejects_symlinked_parent(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    external = tmp_path / "external"
    repository.mkdir()
    external.mkdir()
    (external / "contract.json").write_text("{}\n", encoding="utf-8")
    (repository / "linked").symlink_to(external, target_is_directory=True)

    with pytest.raises(GitSourceError, match="symbolic link"):
        read_regular(repository, "linked/contract.json")


def test_profile_path_preserves_symlink_for_file_kind_admission(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "profile.json").write_text("{}\n", encoding="utf-8")
    profile_link = repository / "profile-link.json"
    profile_link.symlink_to("profile.json")

    relative = relative_path(repository, profile_link)

    assert relative == "profile-link.json"
    with pytest.raises(GitSourceError, match="symbolic link"):
        read_regular(repository, relative)


@pytest.mark.parametrize(
    ("schema_filename", "value"),
    [
        (
            "consumer-contract-lab-profile.schema.v1.json",
            _profile("a" * 40).to_mapping(),
        ),
        (
            "consumer-contract-lab-scenarios.schema.v1.json",
            ScenarioCorpus(
                "native-consumer",
                (
                    ConsumerLabScenario(
                        "source-change",
                        "pull_request",
                        (ScenarioChange("src/service.py", "modified", None),),
                        ExpectedOutcome("selected", ("lint",)),
                    ),
                ),
            ).to_mapping(),
        ),
    ],
)
def test_external_schemas_admit_codec_owned_values(
    schema_filename: str,
    value: dict[str, object],
) -> None:
    schema = json.loads(
        (_ROOT / "docs/specs/ci-coordinator-runtime" / schema_filename).read_bytes()
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(value)


@pytest.mark.parametrize(
    ("status", "previous_path", "mode", "selected_jobs"),
    [
        ("renamed", None, "selected", ["lint"]),
        ("modified", "src/old.py", "selected", ["lint"]),
        ("modified", None, "selected", []),
        ("modified", None, "fallback", ["lint"]),
    ],
)
def test_scenario_schema_rejects_cross_field_contradictions(
    status: str,
    previous_path: str | None,
    mode: str,
    selected_jobs: list[str],
) -> None:
    schema = json.loads(
        (
            _ROOT
            / "docs/specs/ci-coordinator-runtime"
            / "consumer-contract-lab-scenarios.schema.v1.json"
        ).read_bytes()
    )
    value = {
        "schemaVersion": "ci-coordinator-consumer-lab-scenarios/v1",
        "profileId": "native-consumer",
        "scenarios": [
            {
                "scenarioId": "invalid",
                "eventName": "pull_request",
                "changes": [
                    {
                        "path": "src/service.py",
                        "status": status,
                        "previousPath": previous_path,
                    }
                ],
                "expectedOutcome": {
                    "mode": mode,
                    "selectedJobs": selected_jobs,
                },
            }
        ],
    }

    assert not Draft202012Validator(schema).is_valid(value)


def test_structural_schema_does_not_claim_exact_codec_admission() -> None:
    schema = json.loads(
        (
            _ROOT
            / "docs/specs/ci-coordinator-runtime"
            / "consumer-contract-lab-profile.schema.v1.json"
        ).read_bytes()
    )
    value = _profile("a" * 40).to_mapping()
    repository = cast(dict[str, object], value["repository"])
    repository["defaultBranch"] = "\u00e9" * 200

    assert Draft202012Validator(schema).is_valid(value)
    with pytest.raises(ConsumerLabAdmissionError):
        parse_consumer_lab_profile(canonical_json(value) + b"\n")


def test_control_io_failure_is_classified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _contract(tmp_path)
    scenario = contract.corpus.scenarios[0]
    issued = issue_scenario(contract, scenario, scenario_index=1)

    def skip_node(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(node_runtime, "_run", skip_node)

    with pytest.raises(ConsumerControlError, match="evidence is unavailable"):
        execute_consumer_controls(contract, scenario, issued)


def test_execution_uses_sealed_target_bytes_after_source_swap(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    scenario = contract.corpus.scenarios[0]
    controls = tmp_path / contract.profile.target_artifacts_directory
    (controls / "ci-coordinator.cjs").write_bytes(b"throw new Error('transient replacement');\n")
    (controls / "dependency-graph.v1.json").write_bytes(b"{}\n")

    result = execute_consumer_controls(
        contract,
        scenario,
        issue_scenario(contract, scenario, scenario_index=1),
    )

    assert (result.mode, result.selected_jobs) == ("selected", ("lint", "test"))


def test_regular_file_reader_keeps_open_descriptor_during_path_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "contract.json"
    path.write_bytes(b'{"epoch":"sealed"}\n')
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(b'{"epoch":"replacement"}\n')
    original_read = os.read
    swapped = False

    def swap_then_read(descriptor: int, size: int) -> bytes:
        nonlocal swapped
        if not swapped:
            path.replace(tmp_path / "original.json")
            replacement.replace(path)
            swapped = True
        return original_read(descriptor, size)

    monkeypatch.setattr(os, "read", swap_then_read)

    assert read_regular(tmp_path, "contract.json") == b'{"epoch":"sealed"}\n'


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO support is unavailable")
def test_regular_file_reader_rejects_fifo_without_waiting_for_a_writer(tmp_path: Path) -> None:
    fifo = tmp_path / "contract.json"
    os.mkfifo(fifo)
    started = time.monotonic()

    with pytest.raises(GitSourceError, match="not a regular file"):
        read_regular(tmp_path, "contract.json")

    assert time.monotonic() - started < 1


def test_node_permission_model_denies_child_process_authority(tmp_path: Path) -> None:
    node = admit_node_executable(_ROOT, tmp_path)
    harness = tmp_path / "harness.cjs"
    program = tmp_path / "program.cjs"
    harness.write_text("", encoding="utf-8")
    program.write_text(
        "require('node:child_process').spawn(process.execPath, ['--version']);\n",
        encoding="utf-8",
    )

    with pytest.raises(ConsumerControlError, match="rejected the scenario"):
        node_runtime._run(
            node,
            harness,
            program,
            "validate-plan",
            tmp_path,
            tmp_path,
            {"LANG": "C", "LC_ALL": "C"},
        )


def test_each_node_control_process_loads_exact_runtime_guard(tmp_path: Path) -> None:
    node = admit_node_executable(_ROOT, tmp_path)
    harness = tmp_path / "harness.cjs"
    program = tmp_path / "program.cjs"
    harness.write_text("", encoding="utf-8")
    program.write_text(
        "if (globalThis.__CI_CONSUMER_LAB_NODE_RUNTIME_ADMITTED__ !== 'v24.21.0') "
        "throw new Error('runtime guard was not loaded');\n",
        encoding="utf-8",
    )

    node_runtime._run(
        node,
        harness,
        program,
        "validate-plan",
        tmp_path,
        tmp_path,
        {"LANG": "C", "LC_ALL": "C"},
    )


def test_node_runtime_guard_rejects_executing_process_version_drift(tmp_path: Path) -> None:
    node = admit_node_executable(_ROOT, tmp_path)
    spoof = tmp_path / "spoof-version.cjs"
    guard = (
        Path(node_runtime.__file__).with_name("resources") / "node-runtime-guard.cjs"
    ).resolve()
    spoof.write_text(
        "Object.defineProperty(process, 'version', {value: 'v26.5.0'});\n",
        encoding="utf-8",
    )

    completed = run_bounded(
        node,
        (
            "--permission",
            f"--allow-fs-read={spoof}",
            f"--allow-fs-read={guard}",
            "--require",
            str(spoof),
            "--require",
            str(guard),
            "--eval",
            "",
        ),
        cwd=tmp_path,
        max_output_bytes=1_024,
        timeout_seconds=5,
        env={"LANG": "C", "LC_ALL": "C"},
    )

    assert completed.status != 0
    assert b"Node.js runtime version is not admitted" in completed.stderr


def test_bootstrap_capture_enforces_output_limit_before_process_completion(
    tmp_path: Path,
) -> None:
    with pytest.raises(ConsumerLabBootstrapError, match="output limits"):
        _capture_bounded(
            (sys.executable, "-c", "import os;os.write(1,b'x'*4096)"),
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"},
            stdout_limit=128,
        )


def test_bounded_process_enforces_aggregate_output_limit(tmp_path: Path) -> None:
    result = run_bounded(
        sys.executable,
        (
            "-c",
            ("import os,time;os.write(1,b'x'*768);os.write(2,b'y'*768);time.sleep(5)"),
        ),
        cwd=tmp_path,
        max_output_bytes=1_024,
        timeout_seconds=5,
        env={"PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"},
    )

    assert result.status is None
    assert result.error == "process output limit"
    assert len(result.stdout) + len(result.stderr) == 1_024


@pytest.mark.parametrize("parent_only", [False, True], ids=["group-kill", "parent-only-mutant"])
def test_bounded_process_terminates_descendants_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parent_only: bool
) -> None:
    with descendant_timeout_probe(monkeypatch, consumer_process, parent_only=parent_only) as (
        parent,
        survived,
    ):
        result = run_bounded(
            sys.executable,
            ("-c", parent),
            cwd=tmp_path,
            max_output_bytes=1_024,
            timeout_seconds=0.05,
            env={"PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"},
        )
        assert result.status is None
        assert result.error == "process timeout"
        assert survived() is parent_only


def test_bounded_process_deadline_closes_detached_inherited_pipes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detached = "import time;time.sleep(0.3)"
    parent = (
        "import subprocess,sys;"
        f"subprocess.Popen([sys.executable,'-c',{detached!r}],start_new_session=True)"
    )
    started = time.monotonic()

    environment = {"PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"}
    if consumer_process.current_lifetime() is None:
        result = run_bounded(
            sys.executable,
            ("-c", parent),
            cwd=tmp_path,
            max_output_bytes=1_024,
            timeout_seconds=0.05,
            env=environment,
        )
    else:
        from scripts.bounded_process import _is_process_group_alive

        pid_path = tmp_path / "detached-pid"
        register = consumer_process._register_read_pipe

        def register_after_ready(
            selector: selectors.BaseSelector,
            stream: IO[bytes] | None,
            sink: bytearray,
        ) -> None:
            until = time.monotonic() + 5
            while not pid_path.exists() and time.monotonic() < until:
                consumer_process.assert_lifetime_running()
                time.sleep(0.01)
            assert pid_path.exists(), "detached receiver did not establish its positive baseline"
            register(selector, stream, sink)

        monkeypatch.setattr(consumer_process, "_register_read_pipe", register_after_ready)
        controlled = (
            "import os,time\nfrom pathlib import Path\n"
            "from ci_coordinator.consumer_contract_lab.process import ("
            "managed_lifetime_entrypoint,assert_lifetime_running)\n"
            "def main():\n"
            f"    Path({str(pid_path)!r}).write_text(str(os.getpid()))\n"
            "    until=time.monotonic()+0.3\n"
            "    while time.monotonic()<until:\n"
            "        assert_lifetime_running(); time.sleep(0.01)\n"
            "    return 0\n"
            "raise SystemExit(managed_lifetime_entrypoint(main))\n"
        )
        with consumer_process.lifetime_invocation(("-c", controlled), timeout_seconds=3) as child:
            parent = (
                "import subprocess,sys;"
                f"subprocess.Popen([sys.executable,*{child.arguments!r}],"
                f"start_new_session=True,pass_fds={child.inherited_fds!r})"
            )
            try:
                result = run_bounded(
                    sys.executable,
                    ("-c", parent),
                    cwd=tmp_path,
                    max_output_bytes=1_024,
                    timeout_seconds=0.05,
                    env={**environment, "PYTHONPATH": str(_ROOT / "backend/src")},
                    inherited_fds=child.inherited_fds,
                    cancellation_fd=child.cancellation_fd,
                    execution_deadline=child.deadline,
                )
                assert pid_path.exists(), result
                pid = int(pid_path.read_text())
                while _is_process_group_alive(pid) and time.monotonic() < child.deadline:
                    time.sleep(0.01)
                assert not _is_process_group_alive(pid), "owned detached receiver was not drained"
            finally:
                if pid_path.exists():
                    pid = int(pid_path.read_text())
                    if _is_process_group_alive(pid):
                        with suppress(ProcessLookupError):
                            os.killpg(pid, signal.SIGKILL)

    assert result.status is None
    assert result.error == "process timeout"
    assert time.monotonic() - started < 3


def test_managed_detached_pipe_fixture_has_an_independent_receiver(
    tmp_path: Path,
    managed_consumer_lifetime: tuple[LifetimeScope, int, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_bounded_process_deadline_closes_detached_inherited_pipes(tmp_path, monkeypatch)


def _contract(
    target_root: Path,
    *,
    event_surface: tuple[EventName, ...] = ("pull_request",),
    local_requester: bool = False,
) -> PreparedConsumerContract:
    commit = subprocess.run(
        ("git", "-C", str(_ROOT), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    workflow = (_FIXTURE / WORKFLOW_PATH).read_bytes()
    source_mapping = json.loads(
        (_FIXTURE / ".ci-coordinator/target-artifacts-source.v1.json").read_bytes()
    )
    extra_workflows = {}
    if local_requester:
        workflow = workflow.replace(
            PLAN_REQUEST_WORKFLOW_REF.encode(), LOCAL_PLAN_REQUEST_WORKFLOW_REF.encode()
        )
        assert LOCAL_PLAN_REQUEST_WORKFLOW_REF.encode() in workflow
        extra_workflows[LOCAL_PLAN_REQUEST_WORKFLOW_PATH] = render_plan_requester()
        source_mapping["executionWorkflows"][0]["planRequestWorkflowRef"] = (
            LOCAL_PLAN_REQUEST_WORKFLOW_REF
        )
        source_mapping["adapterWorkflowFiles"] = [
            {"path": path, "sha256": hashlib.sha256(content).hexdigest()}
            for path, content in sorted({WORKFLOW_PATH: workflow, **extra_workflows}.items())
        ]
    source_mapping["generator"]["version"] = commit
    source_mapping["dependencyGraph"]["nodes"] = [
        {"path": "docs/guide.md", "dependents": [], "riskClasses": ["docs"]},
        {"path": "src/service.py", "dependents": [], "riskClasses": ["source"]},
    ]
    source = parse_target_artifacts_source(canonical_json(source_mapping) + b"\n")
    rendered = render_target_artifacts(source)
    (target_root / ".ci-coordinator").mkdir()
    (target_root / ".github/workflows").mkdir(parents=True)
    (target_root / WORKFLOW_PATH).write_bytes(workflow)
    for path, content in extra_workflows.items():
        (target_root / path).write_bytes(content)
    for filename, content in rendered.by_filename():
        (target_root / ".ci-coordinator" / filename).write_bytes(content)
    registry = parse_target_execution_registry(
        (target_root / ".ci-coordinator/execution-registry.v1.json").read_bytes()
    )
    assert registry is not None
    contract_files = tuple(
        sorted(
            (
                (WORKFLOW_PATH, workflow),
                *extra_workflows.items(),
                *(
                    (f".ci-coordinator/{filename}", content)
                    for filename, content in rendered.by_filename()
                ),
            ),
            key=lambda item: utf16_sort_key(item[0]),
        )
    )
    manifest = tuple(
        ManifestEntry(
            path=path,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )
        for path, content in contract_files
    )
    manifest_sha = hash_object({"files": [item.to_mapping() for item in manifest]})
    source_snapshot = GitSourceSnapshot(commit, (), "commit")
    source_epoch = hash_object(
        {
            "coordinator": source_snapshot.to_mapping(),
            "target": source_snapshot.to_mapping(),
            "manifestSha256": manifest_sha,
        }
    )
    profile = _profile(commit, event_surface)
    scenario_event = event_surface[0]
    return PreparedConsumerContract(
        coordinator_root=_ROOT,
        coordinator_package_root=_ROOT / "backend/src/ci_coordinator",
        target_root=target_root,
        profile_path=".ci-coordinator/local-lab-profile.v1.json",
        profile=profile,
        corpus=ScenarioCorpus(
            profile.profile_id,
            (
                ConsumerLabScenario(
                    "source-change",
                    scenario_event,
                    (ScenarioChange("src/service.py", "modified", None),),
                    ExpectedOutcome("selected", ("lint", "test")),
                ),
                ConsumerLabScenario(
                    "workflow-change",
                    scenario_event,
                    (ScenarioChange(WORKFLOW_PATH, "modified", None),),
                    ExpectedOutcome("fallback", ()),
                ),
            ),
        ),
        coordinator_source=source_snapshot,
        target_source=source_snapshot,
        manifest_entries=manifest,
        contract_files=contract_files,
        manifest_sha256=manifest_sha,
        source_epoch_id=source_epoch,
        dynamic_policy=dynamic_policy(commit),
        target_artifacts_source=source,
        target_registry=registry,
    )
