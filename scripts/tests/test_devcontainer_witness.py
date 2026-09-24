from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest
from scripts import devcontainer_witness
from scripts.bounded_process import CommandResult, ResidualProcessGroupPolicy
from scripts.devcontainer_witness import (
    admitted_devcontainer_source,
    verify_devcontainer,
)
from scripts.python_witness import PYTHON_TEST_PROCESS_TIMEOUT_SECONDS
from scripts.quality_plan import load_quality_plan

_REPOSITORY_NAME = Path(__file__).resolve().parents[2].name
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_CONTAINER_ID = "a" * 64
_REPLACEMENT_CONTAINER_ID = "b" * 64
_WITNESS_LABEL = "io.ci-coordinator.devcontainer-witness=test-witness"
_DEVCONTAINER_ID = "0qni6uj8j7cfum66dvl77j5dh6f558f9s0gone0kshp1674jp58c"


def _expected_mounts() -> list[dict[str, object]]:
    container_workspace = Path("/workspaces") / _REPOSITORY_NAME
    mounts: list[dict[str, object]] = [
        {
            "Type": "bind",
            "Source": str(_REPOSITORY_ROOT),
            "Destination": str(container_workspace),
            "RW": True,
        }
    ]
    for destination, suffix in {
        container_workspace / "backend" / ".venv": "backend-venv",
        Path("/home/vscode/.local/share/mise"): "mise-data",
        container_workspace / ".pnpm-store": "pnpm-store",
        container_workspace / "node_modules": "root-node-modules",
        container_workspace / "frontend" / "node_modules": "frontend-node-modules",
    }.items():
        name = f"{_DEVCONTAINER_ID}-{suffix}"
        mounts.append(
            {
                "Type": "volume",
                "Name": name,
                "Source": f"/var/lib/docker/volumes/{name}/_data",
                "Destination": str(destination),
                "RW": True,
            }
        )
    return mounts


def _expected_volume_documents(
    mounts: list[dict[str, object]],
) -> list[dict[str, object]]:
    volumes: list[dict[str, object]] = []
    for mount in mounts:
        if mount.get("Type") != "volume":
            continue
        name = mount.get("Name")
        source = mount.get("Source")
        assert isinstance(name, str)
        assert isinstance(source, str)
        volumes.append(
            {
                "Driver": "local",
                "Mountpoint": source,
                "Name": name,
                "Options": None,
                "Scope": "local",
            }
        )
    return volumes


def test_devcontainer_volume_identity_matches_the_pinned_cli_algorithm() -> None:
    assert devcontainer_witness._devcontainer_id(_WITNESS_LABEL) == _DEVCONTAINER_ID


class FakeRunner:
    def __init__(
        self,
        failure: str | None = None,
        *,
        invalid_identity: bool = False,
        duplicate_container: bool = False,
        invalid_store: bool = False,
        retained_network: bool = False,
        mounts: list[dict[str, object]] | None = None,
        store_path: str | None = None,
        canonical_store_path: str | None = None,
        replace_before_proof: bool = False,
        discovered_container_id: str | None = None,
    ) -> None:
        self.failure = failure
        self.invalid_identity = invalid_identity
        self.duplicate_container = duplicate_container
        self.invalid_store = invalid_store
        self.retained_network = retained_network
        self.replace_before_proof = replace_before_proof
        self.discovered_container_id = discovered_container_id
        self.mounts = _expected_mounts() if mounts is None else mounts
        self.volumes = _expected_volume_documents(self.mounts)
        self.network_connected = True
        default_store_path = (
            "/opt/foreign-store"
            if invalid_store
            else f"/workspaces/{_REPOSITORY_NAME}/.pnpm-store/v11"
        )
        self.store_path = default_store_path if store_path is None else store_path
        self.canonical_store_path = (
            self.store_path if canonical_store_path is None else canonical_store_path
        )
        self.calls: list[tuple[tuple[str, ...], float]] = []
        self.discovery_count = 0

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> CommandResult:
        args = tuple(argv)
        self.calls.append((args, timeout_seconds))
        container_workspace = f"/workspaces/{_REPOSITORY_NAME}"
        provision_command = (
            "node",
            "node_modules/@devcontainers/cli/devcontainer.js",
            "up",
            "--workspace-folder",
            str(_REPOSITORY_ROOT),
            "--id-label",
            _WITNESS_LABEL,
            "--remove-existing-container",
            "--frozen-lockfile",
        )
        if args == provision_command:
            if self.failure == "provision":
                return CommandResult(1, "", "provision failed")
            return CommandResult(0, "", "")
        if args == (
            "docker",
            "exec",
            "--user",
            "vscode",
            "--workdir",
            container_workspace,
            _CONTAINER_ID,
            "mise",
            "exec",
            "--",
            "backend/.venv/bin/python",
            "-m",
            "pytest",
            "-q",
            "-c",
            "backend/pyproject.toml",
            "scripts/conformance/installed_mise_node_test.py",
        ):
            if self.failure == "node-resolution":
                return CommandResult(1, "", "node resolution failed")
            return CommandResult(0, "", "")
        proof_command = (
            "node",
            "node_modules/@devcontainers/cli/devcontainer.js",
            "exec",
            "--container-id",
            _CONTAINER_ID,
            "--workspace-folder",
            str(_REPOSITORY_ROOT),
            "--id-label",
            _WITNESS_LABEL,
            "mise",
            "run",
            "check:portable",
        )
        if args == proof_command:
            if self.replace_before_proof:
                return CommandResult(1, "", "container missing")
            if self.failure == "proof":
                return CommandResult(1, "", "proof failed")
            return CommandResult(0, "", "")
        if args == (
            "docker",
            "ps",
            "--all",
            "--no-trunc",
            "--quiet",
            "--filter",
            f"label={_WITNESS_LABEL}",
        ):
            self.discovery_count += 1
            if self.failure == "discovery":
                return CommandResult(1, "", "discovery failed")
            if self.replace_before_proof and self.discovery_count > 1:
                return CommandResult(0, _REPLACEMENT_CONTAINER_ID + "\n", "")
            identity = (
                "not-a-container"
                if self.invalid_identity
                else self.discovered_container_id or _CONTAINER_ID
            )
            count = 2 if self.duplicate_container else 1
            return CommandResult(0, "".join(f"{identity}\n" for _ in range(count)), "")
        if args == (
            "docker",
            "inspect",
            "--format",
            "{{json .Mounts}}",
            _CONTAINER_ID,
        ):
            return CommandResult(0, json.dumps(self.mounts) + "\n", "")
        volume_names = tuple(volume["Name"] for volume in self.volumes)
        if args == ("docker", "volume", "inspect", *volume_names):
            return CommandResult(0, json.dumps(self.volumes) + "\n", "")
        if args == (
            "docker",
            "inspect",
            "--format",
            "{{json .NetworkSettings.Networks}}",
            _CONTAINER_ID,
        ):
            connected = self.network_connected or self.retained_network
            value = {"bridge": {"NetworkID": "b" * 64}} if connected else {}
            return CommandResult(0, json.dumps(value) + "\n", "")
        if args == ("docker", "network", "disconnect", "bridge", _CONTAINER_ID):
            self.network_connected = False
            return CommandResult(0, "", "")
        if args == (
            "docker",
            "exec",
            "--user",
            "vscode",
            "--workdir",
            container_workspace,
            _CONTAINER_ID,
            "mise",
            "exec",
            "--",
            "pnpm",
            "store",
            "path",
        ):
            return CommandResult(0, self.store_path + "\n", "")
        if args == (
            "docker",
            "exec",
            "--user",
            "vscode",
            _CONTAINER_ID,
            "readlink",
            "--canonicalize-existing",
            "--",
            self.store_path,
        ):
            return CommandResult(0, self.canonical_store_path + "\n", "")
        if args == ("docker", "rm", "--force", _CONTAINER_ID):
            if self.failure == "cleanup":
                return CommandResult(1, "", "cleanup failed")
            return CommandResult(0, "", "")
        raise AssertionError(f"unexpected command: {args!r}")


def test_witness_provisions_proves_and_cleans_one_owned_container() -> None:
    runner = FakeRunner()

    verify_devcontainer(runner=runner, witness_id="test-witness")

    commands = [call[0] for call in runner.calls]
    assert commands[0] == (
        "node",
        "node_modules/@devcontainers/cli/devcontainer.js",
        "up",
        "--workspace-folder",
        str(_REPOSITORY_ROOT),
        "--id-label",
        _WITNESS_LABEL,
        "--remove-existing-container",
        "--frozen-lockfile",
    )
    disconnect_index = next(
        index
        for index, command in enumerate(commands)
        if command[:3] == ("docker", "network", "disconnect")
    )
    proof_index = next(
        index for index, command in enumerate(commands) if "check:portable" in command
    )
    node_index = next(
        index
        for index, command in enumerate(commands)
        if command[-1] == "scripts/conformance/installed_mise_node_test.py"
    )
    assert commands[node_index][:10] == (
        "docker",
        "exec",
        "--user",
        "vscode",
        "--workdir",
        f"/workspaces/{_REPOSITORY_NAME}",
        _CONTAINER_ID,
        "mise",
        "exec",
        "--",
    )
    assert (
        sum(
            command[-1] == "scripts/conformance/installed_mise_node_test.py" for command in commands
        )
        == 1
    )
    store_command = next(
        command for command in commands if command[-3:] == ("pnpm", "store", "path")
    )
    assert store_command == (
        "docker",
        "exec",
        "--user",
        "vscode",
        "--workdir",
        f"/workspaces/{_REPOSITORY_NAME}",
        _CONTAINER_ID,
        "mise",
        "exec",
        "--",
        "pnpm",
        "store",
        "path",
    )
    owned_filter = (
        "docker",
        "ps",
        "--all",
        "--no-trunc",
        "--quiet",
        "--filter",
        f"label={_WITNESS_LABEL}",
    )
    assert commands.count(owned_filter) == 2
    assert (
        "node",
        "node_modules/@devcontainers/cli/devcontainer.js",
        "exec",
        "--container-id",
        _CONTAINER_ID,
        "--workspace-folder",
        str(_REPOSITORY_ROOT),
        "--id-label",
        _WITNESS_LABEL,
        "mise",
        "run",
        "check:portable",
    ) in commands
    timeout_by_command = dict(runner.calls)
    assert timeout_by_command[commands[0]] == 600
    assert timeout_by_command[commands[proof_index]] == 1_380
    assert timeout_by_command[commands[node_index]] == 60
    plan = load_quality_plan()
    python_test = plan.commands["python.test"]
    assert python_test.timeout_ms == int((PYTHON_TEST_PROCESS_TIMEOUT_SECONDS + 60) * 1_000)
    maximum_portable_timeout_ms = max(command.timeout_ms for command in plan.portable_commands())
    assert timeout_by_command[commands[proof_index]] == (maximum_portable_timeout_ms / 1_000 + 240)
    assert plan.commands["devcontainer.verify"].timeout_ms == (1_920 + 120 + 60 + 60) * 1_000
    assert disconnect_index < node_index < proof_index
    assert commands[-1] == ("docker", "rm", "--force", _CONTAINER_ID)


def test_witness_rejects_a_longer_portable_command_outside_the_parent_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = load_quality_plan()
    python_lint = plan.commands["python.lint"]
    commands = dict(plan.commands)
    commands["python.lint"] = replace(
        python_lint, timeout_ms=plan.commands["python.test"].timeout_ms + 1
    )
    longer_plan = replace(plan, commands=commands)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        devcontainer_witness,
        "load_quality_plan",
        lambda _workspace: longer_plan,
    )

    with pytest.raises(RuntimeError, match="aggregate deadline reserve"):
        devcontainer_witness._admit_portable_proof_deadlines(_REPOSITORY_ROOT)


@pytest.mark.parametrize("timeout_ms", [None, 2_159_999])
def test_witness_rejects_a_missing_or_short_container_stage_budget(
    monkeypatch: pytest.MonkeyPatch,
    timeout_ms: int | None,
) -> None:
    plan = load_quality_plan()
    commands = dict(plan.commands)
    container_command = commands.pop("devcontainer.verify")
    if timeout_ms is not None:
        commands["devcontainer.verify"] = replace(container_command, timeout_ms=timeout_ms)
    invalid_plan = replace(plan, commands=commands)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(devcontainer_witness, "load_quality_plan", lambda _workspace: invalid_plan)

    with pytest.raises(RuntimeError, match="stage deadline budget"):
        devcontainer_witness._admit_portable_proof_deadlines(_REPOSITORY_ROOT)


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("provision", "provision failed"),
        ("node-resolution", "node resolution failed"),
        ("proof", "proof failed"),
        ("discovery", "container discovery failed"),
        ("cleanup", "cleanup failed"),
    ],
)
def test_witness_fails_closed_and_attempts_cleanup(failure: str, message: str) -> None:
    runner = FakeRunner(failure)

    with pytest.raises(RuntimeError, match=message):
        verify_devcontainer(runner=runner, witness_id="test-witness")

    assert any(call[0][:2] == ("docker", "ps") for call in runner.calls)
    if failure == "node-resolution":
        assert not any("check:portable" in call[0] for call in runner.calls)
        assert runner.calls[-1][0] == ("docker", "rm", "--force", _CONTAINER_ID)


@pytest.mark.parametrize("primary_type", [RuntimeError, OSError, KeyboardInterrupt])
@pytest.mark.parametrize("cleanup_type", [None, RuntimeError, OSError, KeyboardInterrupt])
def test_interruption_preserves_primary_and_attempts_owned_cleanup(
    primary_type: type[BaseException], cleanup_type: type[BaseException] | None
) -> None:
    primary = primary_type("primary failure")
    cleanup = None if cleanup_type is None else cleanup_type("secondary cleanup failure")

    class InterruptedRunner(FakeRunner):
        def __call__(self, argv: Sequence[str], *, timeout_seconds: float) -> CommandResult:
            if "check:portable" in argv:
                raise primary
            result = super().__call__(argv, timeout_seconds=timeout_seconds)
            if tuple(argv[:3]) == ("docker", "rm", "--force") and cleanup is not None:
                raise cleanup
            return result

    runner = InterruptedRunner()
    with pytest.raises(primary_type) as caught:
        verify_devcontainer(runner=runner, witness_id="test-witness")

    assert caught.value is primary
    assert sum(call[0][:3] == ("docker", "rm", "--force") for call in runner.calls) == 1
    if cleanup is not None:
        assert any("secondary cleanup failure" in note for note in primary.__notes__)


def test_cleanup_interruption_without_primary_failure_is_not_success() -> None:
    interruption = KeyboardInterrupt("cleanup interrupted")

    class InterruptedRunner(FakeRunner):
        def __call__(self, argv: Sequence[str], *, timeout_seconds: float) -> CommandResult:
            result = super().__call__(argv, timeout_seconds=timeout_seconds)
            if tuple(argv[:3]) == ("docker", "rm", "--force"):
                raise interruption
            return result

    with pytest.raises(KeyboardInterrupt) as caught:
        verify_devcontainer(runner=InterruptedRunner(), witness_id="test-witness")
    assert caught.value is interruption


@pytest.mark.parametrize("network_count", [1, 8])
def test_cumulative_work_clips_proof_and_cleanup_discovery_consumes_its_reserve(
    monkeypatch: pytest.MonkeyPatch, network_count: int
) -> None:
    now = [0.0]
    monkeypatch.setattr(devcontainer_witness, "monotonic", lambda: now[0])
    networks = {f"network-{index}" for index in range(network_count)}
    observed: dict[str, float] = {}

    class SlowRunner(FakeRunner):
        def __call__(self, argv: Sequence[str], *, timeout_seconds: float) -> CommandResult:
            args = tuple(argv)
            if args[:3] == ("docker", "inspect", "--format") and "Networks" in args[3]:
                self.calls.append((args, timeout_seconds))
                result = CommandResult(0, json.dumps({name: {} for name in sorted(networks)}), "")
            elif args[:3] == ("docker", "network", "disconnect"):
                self.calls.append((args, timeout_seconds))
                networks.remove(args[3])
                result = CommandResult(0, "", "")
            else:
                result = super().__call__(argv, timeout_seconds=timeout_seconds)
            if "up" in args[:3]:
                now[0] += 590
            elif "check:portable" in args:
                observed["proof_started"] = now[0]
                observed["proof_timeout"] = timeout_seconds
                now[0] += timeout_seconds
                observed["cleanup_started"] = now[0]
                return CommandResult(None, "last completed proof", "", "test timeout", "timeout")
            elif "installed_mise_node_test.py" in " ".join(args):
                now[0] += 50
            elif args[:3] == ("docker", "rm", "--force"):
                observed["remove_timeout"] = timeout_seconds
                observed["remove_started"] = now[0]
                now[0] += timeout_seconds - 1
            else:
                now[0] += 29
            return result

    runner = SlowRunner()
    with pytest.raises(RuntimeError, match="test timeout"):
        verify_devcontainer(runner=runner, witness_id="test-witness")

    assert observed["proof_timeout"] < 1_380
    assert observed["proof_started"] + observed["proof_timeout"] < 1_920
    assert observed["remove_started"] - observed["cleanup_started"] == 29
    assert 0 < observed["remove_timeout"] < 120 - 29
    assert now[0] < observed["cleanup_started"] + 120
    assert runner.calls[-1][0] == ("docker", "rm", "--force", _CONTAINER_ID)


def test_exhausted_deadline_does_not_start_another_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [0.0]
    monkeypatch.setattr(devcontainer_witness, "monotonic", lambda: now[0])
    calls: list[float] = []

    def execute(argv: Sequence[str], *, timeout_seconds: float) -> CommandResult:
        assert tuple(argv) == ("operation",)
        calls.append(timeout_seconds)
        now[0] += 15
        return CommandResult(0, "", "")

    runner = devcontainer_witness._DeadlineRunner(execute, 20)
    assert runner(("operation",), timeout_seconds=30).status == 0
    with pytest.raises(RuntimeError, match="deadline exhausted"):
        runner(("operation",), timeout_seconds=30)
    assert calls == [15]


@pytest.mark.parametrize("status", [0, 1])
def test_late_success_rejects_but_does_not_replace_an_existing_failure(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    now = [0.0]
    monkeypatch.setattr(devcontainer_witness, "monotonic", lambda: now[0])
    original = CommandResult(status, "captured output", "captured error")

    def execute(argv: Sequence[str], *, timeout_seconds: float) -> CommandResult:
        assert tuple(argv) == ("operation",)
        assert timeout_seconds == 15
        now[0] = 20
        return original

    result = devcontainer_witness._DeadlineRunner(execute, 20)(("operation",), timeout_seconds=30)
    if status == 0:
        assert result.status is None
        assert result.failure_kind == "timeout"
        assert result.stdout == original.stdout
        assert result.stderr == original.stderr
    else:
        assert result is original


@pytest.mark.parametrize("executor_failure", [False, True])
def test_failure_preserves_bounded_channel_tails_and_primary_cause(
    executor_failure: bool,
) -> None:
    result = CommandResult(
        None if executor_failure else 1,
        "old-stdout" + "x" * 10_000 + "last-stdout",
        "old-stderr" + "y" * 10_000 + "last-stderr",
        "primary timeout" if executor_failure else None,
        "timeout" if executor_failure else None,
    )
    with pytest.raises(RuntimeError) as caught:
        devcontainer_witness._checked(result, "proof")
    message = str(caught.value)
    assert message.startswith(
        "proof failed: " + ("primary timeout" if executor_failure else "status 1")
    )
    assert "stdout: [tail truncated]" in message and "stderr: [tail truncated]" in message
    assert "last-stdout" in message and "last-stderr" in message
    assert "old-stdout" not in message and "old-stderr" not in message
    assert len(message) < 2 * 8_192 + 150


def test_source_finalization_is_clipped_by_the_nonrenewable_outer_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(devcontainer_witness, "monotonic", lambda: 2_050.0)
    assert devcontainer_witness._finalization_deadline(1_920) == 2_100


def test_witness_rejects_untrusted_cleanup_identity() -> None:
    runner = FakeRunner(invalid_identity=True)

    with pytest.raises(RuntimeError, match="invalid container identity"):
        verify_devcontainer(runner=runner, witness_id="test-witness")

    assert not any(call[0][:2] == ("docker", "rm") for call in runner.calls)


@pytest.mark.parametrize("length", [12, 63])
def test_witness_rejects_truncated_lowercase_hex_container_ids(length: int) -> None:
    runner = FakeRunner(discovered_container_id="a" * length)

    with pytest.raises(RuntimeError, match="invalid container identity"):
        verify_devcontainer(runner=runner, witness_id="test-witness")

    assert not any(
        call[0][:2] in {("docker", "inspect"), ("docker", "rm")} or "check:portable" in call[0]
        for call in runner.calls
    )


def test_witness_rejects_ambiguous_cleanup_set() -> None:
    runner = FakeRunner(duplicate_container=True)

    with pytest.raises(RuntimeError, match="invalid resource set"):
        verify_devcontainer(runner=runner, witness_id="test-witness")

    assert not any(call[0][:2] == ("docker", "rm") for call in runner.calls)


def test_witness_rejects_pnpm_store_outside_identity_volume() -> None:
    runner = FakeRunner(invalid_store=True)

    with pytest.raises(RuntimeError, match="pnpm store escaped"):
        verify_devcontainer(runner=runner, witness_id="test-witness")


def test_witness_rejects_a_lexical_pnpm_store_escape() -> None:
    runner = FakeRunner(store_path=f"/workspaces/{_REPOSITORY_NAME}/.pnpm-store/../../host-cache")

    with pytest.raises(RuntimeError, match="pnpm store escaped"):
        verify_devcontainer(runner=runner, witness_id="test-witness")


def test_witness_rejects_a_canonical_pnpm_store_escape() -> None:
    runner = FakeRunner(canonical_store_path="/host-cache")

    with pytest.raises(RuntimeError, match="pnpm store escaped"):
        verify_devcontainer(runner=runner, witness_id="test-witness")


def test_witness_rejects_a_remapped_host_docker_socket() -> None:
    mounts = _expected_mounts()
    mounts[0]["Source"] = "/var/run/docker.sock"
    runner = FakeRunner(mounts=mounts)

    with pytest.raises(RuntimeError, match="mount inspection returned an invalid document"):
        verify_devcontainer(runner=runner, witness_id="test-witness")


def test_witness_rejects_a_missing_identity_volume() -> None:
    mounts = _expected_mounts()
    mounts[-1]["Destination"] = "/unexpected"
    runner = FakeRunner(mounts=mounts)

    with pytest.raises(RuntimeError, match="required volume is missing"):
        verify_devcontainer(runner=runner, witness_id="test-witness")


def test_witness_rejects_a_workspace_bind_with_only_the_same_basename() -> None:
    mounts = _expected_mounts()
    mounts[0]["Source"] = f"/srv/foreign/{_REPOSITORY_NAME}"
    runner = FakeRunner(mounts=mounts)

    with pytest.raises(RuntimeError, match="workspace mount is invalid"):
        verify_devcontainer(runner=runner, witness_id="test-witness")


def test_witness_rejects_foreign_volume_names() -> None:
    mounts = _expected_mounts()
    for mount in mounts[1:]:
        original_name = mount["Name"]
        assert isinstance(original_name, str)
        foreign_name = f"foreign-{original_name}"
        mount["Name"] = foreign_name
        mount["Source"] = f"/var/lib/docker/volumes/{foreign_name}/_data"
    runner = FakeRunner(mounts=mounts)

    with pytest.raises(RuntimeError, match="required volume is invalid"):
        verify_devcontainer(runner=runner, witness_id="test-witness")


@pytest.mark.parametrize(
    "source_template",
    [
        "/var/lib/docker/volumes/{name}/../{name}/_data",
        "//var/lib/docker/volumes/{name}/_data",
    ],
)
def test_witness_rejects_noncanonical_volume_sources(source_template: str) -> None:
    mounts = _expected_mounts()
    for mount in mounts[1:]:
        name = mount["Name"]
        assert isinstance(name, str)
        mount["Source"] = source_template.format(name=name)
    runner = FakeRunner(mounts=mounts)

    with pytest.raises(RuntimeError, match="required volume is invalid"):
        verify_devcontainer(runner=runner, witness_id="test-witness")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("Driver", "foreign"),
        ("Scope", "global"),
        ("Mountpoint", "/srv/foreign/node-modules"),
        (
            "Options",
            {
                "device": "/srv/foreign/node-modules",
                "o": "bind",
                "type": "none",
            },
        ),
    ],
)
def test_witness_rejects_foreign_volume_metadata(field: str, value: object) -> None:
    runner = FakeRunner()
    for volume in runner.volumes:
        volume[field] = value

    with pytest.raises(RuntimeError, match="volume inspection returned an invalid document"):
        verify_devcontainer(runner=runner, witness_id="test-witness")


def test_witness_rejects_missing_volume_options_metadata() -> None:
    runner = FakeRunner()
    for volume in runner.volumes:
        del volume["Options"]

    with pytest.raises(RuntimeError, match="volume inspection returned an invalid document"):
        verify_devcontainer(runner=runner, witness_id="test-witness")


def test_witness_rejects_container_replacement_before_proof() -> None:
    runner = FakeRunner(replace_before_proof=True)

    with pytest.raises(RuntimeError, match="identity changed before cleanup"):
        verify_devcontainer(runner=runner, witness_id="test-witness")

    assert not any(call[0][:3] == ("docker", "rm", "--force") for call in runner.calls)


def test_witness_rejects_network_that_remains_connected() -> None:
    runner = FakeRunner(retained_network=True)

    with pytest.raises(RuntimeError, match="retained network access"):
        verify_devcontainer(runner=runner, witness_id="test-witness")

    assert not any("check:portable" in call[0] for call in runner.calls)


@pytest.mark.parametrize("witness_id", ["", "UPPERCASE", "path/value", "a" * 81])
def test_witness_rejects_invalid_identity_before_provider_use(witness_id: str) -> None:
    runner = FakeRunner()

    with pytest.raises(ValueError, match="identity is invalid"):
        verify_devcontainer(runner=runner, witness_id=witness_id)

    assert runner.calls == []


def test_command_double_rejects_unmodelled_commands() -> None:
    runner = FakeRunner()

    with pytest.raises(AssertionError, match="unexpected command"):
        runner(("docker", "unexpected"), timeout_seconds=1)


@pytest.mark.parametrize(
    ("argv", "expected_policy"),
    [
        ((*devcontainer_witness._DEVCONTAINER_COMMAND, "up"), "terminate"),
        ((*devcontainer_witness._DEVCONTAINER_COMMAND, "exec"), "reject"),
        (("node", "unadmitted-devcontainer.js", "up"), "reject"),
    ],
)
def test_run_scopes_residual_cleanup_to_the_devcontainer_up_operation(
    monkeypatch: pytest.MonkeyPatch,
    argv: tuple[str, ...],
    expected_policy: ResidualProcessGroupPolicy,
) -> None:
    observed_policies: list[ResidualProcessGroupPolicy] = []

    def fake_spawn(
        command: str,
        args: Sequence[str],
        *,
        cwd: Path,
        max_buffer: int,
        timeout_seconds: float,
        residual_process_group_policy: ResidualProcessGroupPolicy = "reject",
    ) -> CommandResult:
        assert command == argv[0]
        assert tuple(args) == argv[1:]
        assert cwd == _REPOSITORY_ROOT
        assert max_buffer == devcontainer_witness._MAX_OUTPUT_BYTES
        assert timeout_seconds == 1
        observed_policies.append(residual_process_group_policy)
        return CommandResult(0, "", "")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(devcontainer_witness, "spawn", fake_spawn)

    result = devcontainer_witness._run(argv, timeout_seconds=1)

    assert result.status == 0
    assert observed_policies == [expected_policy]


def test_linked_worktree_uses_a_clean_standalone_exact_source(tmp_path: Path) -> None:
    linked = _linked_worktree(tmp_path)
    expected_head = _git(linked, "rev-parse", "HEAD").stdout.strip()

    with admitted_devcontainer_source(linked) as source:
        materialized = source
        assert source != linked
        assert source.name == linked.name
        assert (source / ".git").is_dir()
        assert not (source / ".git/objects/info/alternates").exists()
        assert _git(source, "rev-parse", "HEAD").stdout.strip() == expected_head
        assert _git(source, "status", "--porcelain").stdout == ""

    assert not materialized.exists()


def test_linked_worktree_rejects_uncommitted_source(tmp_path: Path) -> None:
    linked = _linked_worktree(tmp_path)
    (linked / "untracked.txt").write_text("untracked\n")

    with (
        pytest.raises(RuntimeError, match="must be clean"),
        admitted_devcontainer_source(linked),
    ):
        raise AssertionError("dirty source was admitted")


def test_linked_worktree_rejects_mutated_materialized_source(tmp_path: Path) -> None:
    linked = _linked_worktree(tmp_path)

    with (
        pytest.raises(RuntimeError, match="must be clean"),
        admitted_devcontainer_source(linked) as source,
    ):
        (source / "tracked.txt").write_text("mutated\n")


def _linked_worktree(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    linked = tmp_path / "linked"
    _git(tmp_path, "init", str(repository))
    _git(repository, "config", "user.name", "Test")
    _git(repository, "config", "user.email", "test@example.invalid")
    (repository / "tracked.txt").write_text("tracked\n")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-m", "initial")
    _git(repository, "worktree", "add", "--detach", str(linked), "HEAD")
    return linked


def _git(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *arguments),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
