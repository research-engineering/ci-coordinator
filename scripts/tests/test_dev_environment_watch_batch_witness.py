"""Barrier/retention falsifiers; mocked paths do not qualify Docker or Compose."""

from __future__ import annotations

import hashlib
import http.client
import json
import shutil
import sys
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from scripts.bounded_git import BoundedGitResult
from scripts.bounded_process import InteractiveResult
from scripts.dev_environment import compose, secrets, watch_batch_witness, watch_session
from scripts.dev_environment.compose import LocalEndpoints, ProviderInvocation
from scripts.dev_environment.identity import InstanceIdentity, derive_instance_identity
from scripts.dev_environment.lifecycle import (
    OperationBlocked,
    instance_operation_lock,
    operation_paths,
)
from scripts.dev_environment.watch_batch_witness import (
    BuildStepBarrier,
    _assert_fenced,
    _inject_step,
    _owned_gateway,
    _provider_epoch,
    _require_clean_stop,
    _require_provider_lease,
    verify_isolated_abandonment,
)
from scripts.dev_environment.watch_client_witness import WatchClientWitnessError
from scripts.dev_environment.watch_session import CancelResult, inspect_watch, owned_watch_session
from scripts.mutation import detached_worktree_lifecycle
from scripts.mutation.detached_worktree_lifecycle import CleanupResult

_DOCKERFILE = (
    b"FROM python AS application\nFROM application AS debug\nFROM application AS development\n"
)
_NONCE = "1" * 64


@pytest.fixture
def identity(tmp_path: Path) -> InstanceIdentity:
    root = tmp_path / "normal-ci" / "worktree"
    source = root / "docker/development/backend.Dockerfile"
    source.parent.mkdir(parents=True)
    source.write_bytes(_DOCKERFILE)
    return derive_instance_identity(root, state_home=root.parent / "state")


def test_build_step_is_inserted_only_in_the_selected_target() -> None:
    instruction = b'RUN ["python", "-c", "pass"]\n'
    source = _inject_step(_DOCKERFILE, "debug", instruction)
    assert source == (
        b"FROM python AS application\nFROM application AS debug\n\n"
        + instruction
        + b"FROM application AS development\n"
    )
    assert (
        _inject_step(_DOCKERFILE, "development", instruction) == _DOCKERFILE + b"\n" + instruction
    )


@pytest.mark.parametrize(
    "source",
    [b"FROM python AS application\n", b"# AS development\n", _DOCKERFILE + _DOCKERFILE],
)
def test_unadmitted_build_target_cannot_become_a_barrier(source: bytes) -> None:
    with pytest.raises(WatchClientWitnessError, match="target_unavailable"):
        _inject_step(source, "development", b"RUN false\n")


def test_actual_http_request_is_held_until_explicit_release(identity: InstanceIdentity) -> None:
    barrier = BuildStepBarrier(identity, "127.0.0.1")

    def request(path: str) -> tuple[int, bytes]:
        client = http.client.HTTPConnection("127.0.0.1", barrier.server.server_port, timeout=3)
        try:
            client.request("GET", path)
            response = client.getresponse()
            return response.status, response.read()
        finally:
            client.close()

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        assert request("/foreign-nonce")[0] == 404
        assert not barrier.entered.is_set()
        barrier.arm()
        pending = executor.submit(request, f"/{barrier.token}")
        assert barrier.entered.wait(timeout=3)
        assert not pending.done()
        # Close releases HTTP, but cannot restore source without mutation admission.
        barrier.close()
        assert pending.result(timeout=3) == (200, b"x")
        assert barrier.path.read_bytes() == barrier.modified
        with instance_operation_lock(identity) as lease:
            barrier.restore(lease)
        assert barrier.path.read_bytes() == _DOCKERFILE
    finally:
        barrier.close()
        executor.shutdown(wait=False, cancel_futures=True)


def test_barrier_timeout_is_not_an_inflight_observation(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(watch_batch_witness, "_BUILD_SECONDS", 0.01)
    barrier = BuildStepBarrier(identity, "127.0.0.1")
    try:
        with pytest.raises(WatchClientWitnessError, match="barrier_unobserved"):
            barrier.wait()
        assert not barrier.entered.is_set()
    finally:
        barrier.close()


def test_source_restore_requires_the_same_live_lease(identity: InstanceIdentity) -> None:
    barrier = BuildStepBarrier(identity, "127.0.0.1")
    other_root = identity.repo_root.parent / "other"
    other_root.mkdir()
    other = derive_instance_identity(other_root, state_home=identity.state_home)
    try:
        barrier.arm()
        with (
            instance_operation_lock(other) as foreign,
            pytest.raises(WatchClientWitnessError, match="restore_lease_unavailable"),
        ):
            barrier.restore(foreign)
        with instance_operation_lock(identity) as expired:
            pass
        with pytest.raises(OperationBlocked):
            barrier.restore(expired)
        assert barrier.path.read_bytes() == barrier.modified
    finally:
        barrier.close()


def _clean_receipt(process: InteractiveResult) -> tuple[dict[str, object], CancelResult]:
    cancellation = CancelResult("quiescent", "watch_client_stopped", _NONCE)
    return {"process": asdict(process), "outcome": asdict(cancellation)}, cancellation


@pytest.mark.parametrize("status", [0, 130])
def test_clean_receipt_preserves_the_actual_controlled_status(status: int) -> None:
    process = InteractiveResult(status, True, "cancelled", cancellation_signal_sent=True)
    receipt, cancellation = _clean_receipt(process)
    _require_clean_stop(receipt, cancellation, 0)
    assert receipt["process"] == asdict(process)


@pytest.mark.parametrize(
    "changed",
    [
        {"returncode": True},
        {"returncode": 1},
        {"returncode": -15},
        {"started": False},
        {"process_group_quiescent": False},
        {"cancellation_signal_sent": False},
        {"escalated": True},
        {"failure_kind": None},
        {"failure_kind": "timeout"},
    ],
)
def test_each_missing_physical_fact_falsifies_clean_stop(changed: dict[str, object]) -> None:
    process = InteractiveResult(130, True, "cancelled", cancellation_signal_sent=True)
    receipt, cancellation = _clean_receipt(process)
    receipt["process"] = {**asdict(process), **changed}
    with pytest.raises(WatchClientWitnessError, match="process_unproved"):
        _require_clean_stop(receipt, cancellation, 0)


@pytest.mark.parametrize("failure", ["nonce", "blocked", "controller"])
def test_stale_or_unacknowledged_cancellation_cannot_permit_next_mutation(failure: str) -> None:
    process = InteractiveResult(130, True, "cancelled", cancellation_signal_sent=True)
    receipt, cancellation = _clean_receipt(process)
    if failure == "nonce":
        receipt["outcome"] = asdict(replace(cancellation, nonce="2" * 64))
    if failure == "blocked":
        cancellation = CancelResult("blocked", "watch_abandoned", _NONCE)
    with pytest.raises(WatchClientWitnessError, match="cancel_unproved"):
        _require_clean_stop(receipt, cancellation, -9 if failure == "controller" else 0)


def test_fence_probe_fails_when_mutation_admission_is_available(identity: InstanceIdentity) -> None:
    with pytest.raises(WatchClientWitnessError, match="admitted_mutation"):
        _assert_fenced(identity)


def test_debug_oracle_requires_the_physical_mutation_lock(identity: InstanceIdentity) -> None:
    with instance_operation_lock(identity):
        _require_provider_lease(identity)
    with pytest.raises(WatchClientWitnessError, match="released_mutation_lease"):
        _require_provider_lease(identity)


def test_watch_marker_cannot_mask_a_lost_debug_descriptor(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        watch_session, "run_interactive", lambda *_args, **_kwargs: InteractiveResult(1, True)
    )
    with owned_watch_session(identity) as session:
        session.run(("fixture-provider",), cwd=identity.repo_root, env={})
    _assert_fenced(identity)
    with pytest.raises(WatchClientWitnessError, match="released_mutation_lease"):
        _require_provider_lease(identity)


@pytest.mark.parametrize("violation", ["foreign_project", "foreign_root", "driver", "gateway"])
def test_only_the_exact_owned_bridge_can_host_a_native_barrier(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch, violation: str
) -> None:
    network: dict[str, object] = {
        "Labels": {
            "com.docker.compose.project": identity.project_name,
            "io.ci-coordinator.root-digest": identity.root_digest,
        },
        "Driver": "bridge",
        "IPAM": {"Config": [{"Gateway": "172.20.0.1"}]},
    }
    if violation == "foreign_project":
        network["Labels"] = {"com.docker.compose.project": "foreign"}
    elif violation == "foreign_root":
        network["Labels"] = {"com.docker.compose.project": identity.project_name}
    elif violation == "driver":
        network["Driver"] = "overlay"
    else:
        network["IPAM"] = {"Config": [{"Gateway": "8.8.8.8"}]}
    monkeypatch.setattr(watch_batch_witness, "_checked", lambda *_args: json.dumps([network]))
    invocation = ProviderInvocation(("docker",), identity.repo_root, {})
    with pytest.raises(WatchClientWitnessError, match="network_"):
        _owned_gateway(identity, invocation)


@pytest.mark.parametrize("mismatch", [False, True])
def test_epoch_binds_binaries_version_and_source_root(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch, mismatch: bool
) -> None:
    docker = identity.repo_root / "docker-binary"
    plugin = identity.repo_root / "compose-binary"
    docker.write_bytes(b"docker-test-fixture")
    plugin.write_bytes(b"compose-test-fixture")
    invocation = ProviderInvocation(("docker",), identity.repo_root, {"GIT_DIR": "/foreign"})

    def checked(_invocation: ProviderInvocation, args: Sequence[str]) -> str:
        if args[0] == "compose":
            return "2.39.4"
        return json.dumps(
            [
                {
                    "Name": "compose",
                    "Path": str(plugin),
                    "Version": "v2.38.0" if mismatch else "v2.39.4",
                }
            ]
        )

    def git(root: Path, args: Sequence[str], **kwargs: object) -> BoundedGitResult:
        assert root == identity.repo_root
        assert args == ("rev-parse", "HEAD")
        assert kwargs["source_environment"] is invocation.environment
        return BoundedGitResult(0, "a" * 40, "")

    monkeypatch.setattr(watch_batch_witness, "_checked", checked)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(watch_batch_witness, "run_git", git)
    monkeypatch.setattr(shutil, "which", lambda *_args, **_kwargs: str(docker))
    if mismatch:
        with pytest.raises(WatchClientWitnessError, match="identity_unavailable"):
            _provider_epoch(invocation)
    else:
        assert _provider_epoch(invocation) == {
            "composeVersion": "2.39.4",
            "composeBinarySha256": hashlib.sha256(plugin.read_bytes()).hexdigest(),
            "dockerBinarySha256": hashlib.sha256(docker.read_bytes()).hexdigest(),
            "sourceSha": "a" * 40,
        }


def _install_isolated_fixture(
    identity: InstanceIdentity,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail: str | None = None,
    nested: bool = False,
    debug_provider: bool = False,
    aliased: bool = False,
) -> tuple[Path, list[str]]:
    root = identity.repo_root.parent if nested else identity.repo_root.parent.parent
    allocated = root / "abandoned-owner"
    allocated.mkdir()
    exposed_root = allocated
    if aliased:
        alias = identity.repo_root.parent.parent / "retention-alias"
        alias.symlink_to(root, target_is_directory=True)
        exposed_root = alias / allocated.name
    events: list[str] = []

    class Lifecycle:
        def __init__(self, *, repo_root: Path, temp_prefix: str) -> None:
            assert repo_root == identity.repo_root
            kind = "debug" if debug_provider else "watch"
            assert temp_prefix == f"ci-coordinator-{kind}-abandoned-"
            self.temp_root = exposed_root
            self.worktree = exposed_root / "worktree"

        def add_detached_worktree(self, revision: str) -> None:
            assert revision == "HEAD"
            events.append("add")
            self.worktree.mkdir()
            (self.worktree / "retained-source").write_bytes(b"source")

        def cleanup(self) -> CleanupResult:
            events.append("cleanup")
            return CleanupResult("passed", "removed")

    class Project:
        def __init__(
            self,
            child: InstanceIdentity,
            environment: Mapping[str, str],
            *,
            provider_environment: Mapping[str, str],
        ) -> None:
            self.identity = child

        def assert_unallocated(self) -> None:
            events.append("unallocated")

        def up(self) -> LocalEndpoints:
            events.append("up")
            if fail == "startup":
                raise WatchClientWitnessError("startup_failed")
            return LocalEndpoints("api", "ui", "postgres")

        def reset(self) -> None:
            events.append("reset")

    def state(child: InstanceIdentity) -> dict[str, str]:
        (child.state_home / "credential-sentinel").write_bytes(b"credential")
        return {}

    def batch(
        child: InstanceIdentity, _project: object, *, hard_parent_death: bool
    ) -> dict[str, object]:
        assert hard_parent_death is True
        with owned_watch_session(child) as session:
            session.run(("fixture-provider",), cwd=child.repo_root, env={})
        _assert_fenced(child)
        if fail == "native":
            raise WatchClientWitnessError("native_failed")
        return {"nonce": session.nonce, "retained": True}

    def debug_batch(child: InstanceIdentity, _project: object) -> dict[str, object]:
        assert inspect_watch(child).phase == "absent"
        if fail == "native":
            raise WatchClientWitnessError("native_failed")
        return {"retained": True, "mutationDescriptorRetained": True}

    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(detached_worktree_lifecycle, "DetachedWorktreeLifecycle", Lifecycle)
    monkeypatch.setattr(compose, "ComposeProject", Project)
    monkeypatch.setattr(secrets, "ensure_instance_state", state)
    monkeypatch.setattr(watch_batch_witness, "verify_watch_batch", batch)
    monkeypatch.setattr(watch_batch_witness, "verify_debug_provider_batch", debug_batch)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        watch_session, "run_interactive", lambda *_args, **_kwargs: InteractiveResult(1, True)
    )
    return allocated, events


def test_expected_abandonment_retains_its_sibling_after_normal_outer_cleanup(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    allocated, events = _install_isolated_fixture(identity, monkeypatch)
    invocation = ProviderInvocation(("docker",), identity.repo_root, {})
    receipt = verify_isolated_abandonment(identity, invocation)
    child = derive_instance_identity(allocated / "worktree", state_home=allocated / "state")
    record = operation_paths(child).session.read_bytes()
    assert events == ["add", "unallocated", "up"]
    retained = receipt["retainedFixture"]
    assert isinstance(retained, dict) and retained["tempRoot"] == str(allocated)
    assert receipt["nonce"] == inspect_watch(child).nonce
    shutil.rmtree(identity.repo_root.parent)
    assert (child.repo_root / "retained-source").read_bytes() == b"source"
    assert (child.state_home / "credential-sentinel").read_bytes() == b"credential"
    assert operation_paths(child).session.read_bytes() == record
    _assert_fenced(child)


def test_failed_abnormal_assertion_preserves_fence_and_actionable_fixture_location(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    allocated, events = _install_isolated_fixture(identity, monkeypatch, fail="native")
    invocation = ProviderInvocation(("docker",), identity.repo_root, {})
    with pytest.raises(WatchClientWitnessError, match="fixture_retained") as caught:
        verify_isolated_abandonment(identity, invocation)
    assert events == ["add", "unallocated", "up"]
    retained = caught.value.retained_fixture
    assert retained is not None and retained["tempRoot"] == str(allocated)
    child = derive_instance_identity(allocated / "worktree", state_home=allocated / "state")
    assert inspect_watch(child).nonce is not None
    assert retained["probeReason"] == "native_failed"
    assert retained["watchNonce"] == inspect_watch(child).nonce
    _assert_fenced(child)


def test_startup_failure_without_a_watch_still_has_normal_cleanup_admission(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    _allocated, events = _install_isolated_fixture(identity, monkeypatch, fail="startup")
    invocation = ProviderInvocation(("docker",), identity.repo_root, {})
    with pytest.raises(WatchClientWitnessError, match="startup_failed"):
        verify_isolated_abandonment(identity, invocation)
    assert events == ["add", "unallocated", "up", "reset", "cleanup"]


@pytest.mark.parametrize("aliased", [False, True])
def test_nested_retention_root_is_rejected_before_worktree_or_provider_allocation(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch, aliased: bool
) -> None:
    allocated, events = _install_isolated_fixture(
        identity, monkeypatch, nested=True, aliased=aliased
    )
    invocation = ProviderInvocation(("docker",), identity.repo_root, {})
    with pytest.raises(WatchClientWitnessError, match="root_is_nested"):
        verify_isolated_abandonment(identity, invocation)
    assert events == []
    assert not allocated.exists()


@pytest.mark.parametrize("failed_probe", [False, True])
def test_debug_abandonment_never_resets_unobserved_provider_effects(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch, failed_probe: bool
) -> None:
    allocated, events = _install_isolated_fixture(
        identity, monkeypatch, debug_provider=True, fail="native" if failed_probe else None
    )
    invocation = ProviderInvocation(("docker",), identity.repo_root, {})
    retained: object
    if failed_probe:
        with pytest.raises(
            WatchClientWitnessError, match="debug_abnormal_fixture_retained"
        ) as caught:
            verify_isolated_abandonment(identity, invocation, debug_provider=True)
        retained = caught.value.retained_fixture
    else:
        receipt = verify_isolated_abandonment(identity, invocation, debug_provider=True)
        retained = receipt["retainedFixture"]
    assert isinstance(retained, dict) and retained["tempRoot"] == str(allocated)
    assert events == ["add", "unallocated", "up"]
    child = derive_instance_identity(allocated / "worktree", state_home=allocated / "state")
    # Fixture retention does not fabricate a product watch fence or terminal outcome.
    assert inspect_watch(child).phase == "absent"
    assert (child.repo_root / "retained-source").read_bytes() == b"source"
    assert (child.state_home / "credential-sentinel").read_bytes() == b"credential"
