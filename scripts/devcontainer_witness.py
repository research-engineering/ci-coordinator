from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from time import monotonic
from typing import Final, Protocol

from scripts.bounded_process import CommandResult, ResidualProcessGroupPolicy, spawn
from scripts.dev_environment.identity import derive_instance_identity
from scripts.dev_environment.private_files import (
    ensure_private_directory,
    exclusive_private_lock,
)
from scripts.python_witness import PYTHON_TEST_PROCESS_TIMEOUT_SECONDS
from scripts.quality_plan import load_quality_plan

REPO_ROOT: Final = Path(__file__).resolve().parent.parent
_CONTAINER_ID: Final = re.compile(r"[0-9a-f]{64}")
_COMMIT_SHA: Final = re.compile(r"[0-9a-f]{40}")
_NETWORK_NAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_MAX_OUTPUT_BYTES: Final = 16 * 1024 * 1024
_MAX_MOUNTS: Final = 6
_MAX_OWNED_CONTAINERS: Final = 1
_MAX_NETWORKS: Final = 8
_BASE32_DIGITS: Final = "0123456789abcdefghijklmnopqrstuv"
_PROVISION_TIMEOUT_SECONDS: Final = 600
_PORTABLE_PROOF_TIMEOUT_SECONDS: Final = 1_200
_PYTHON_TEST_WRAPPER_RESERVE_SECONDS: Final = 60
_PORTABLE_PROOF_AGGREGATE_RESERVE_SECONDS: Final = 240
_PROVIDER_OPERATION_TIMEOUT_SECONDS: Final = 30
_INSTALLED_NODE_PROOF_TIMEOUT_SECONDS: Final = 60
_CLEANUP_TIMEOUT_SECONDS: Final = 120
_DEVCONTAINER_COMMAND: Final = (
    "node",
    "node_modules/@devcontainers/cli/devcontainer.js",
)
_WITNESS_LABEL: Final = "io.ci-coordinator.devcontainer-witness"


class Runner(Protocol):
    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> CommandResult: ...


def verify_devcontainer(
    *,
    runner: Runner | None = None,
    witness_id: str | None = None,
    workspace_root: Path = REPO_ROOT,
) -> None:
    workspace = workspace_root.resolve(strict=True)
    _admit_portable_proof_deadlines(workspace)
    container_workspace = Path("/workspaces") / workspace.name
    identity = (
        derive_instance_identity(REPO_ROOT).project_name if witness_id is None else witness_id
    )
    if re.fullmatch(r"[a-z0-9-]{1,80}", identity) is None:
        raise ValueError("Dev Container witness identity is invalid")
    label = f"{_WITNESS_LABEL}={identity}"
    execute = _run if runner is None else runner

    container_id: str | None = None
    primary_error: RuntimeError | None = None
    try:
        _checked(
            execute(
                (
                    *_DEVCONTAINER_COMMAND,
                    "up",
                    "--workspace-folder",
                    str(workspace),
                    "--id-label",
                    label,
                    "--remove-existing-container",
                    "--frozen-lockfile",
                ),
                timeout_seconds=_PROVISION_TIMEOUT_SECONDS,
            ),
            "Dev Container provisioning",
        )
        container_id = _single_owned_container(label, execute)
        _verify_mount_boundary(
            container_id,
            label,
            workspace,
            container_workspace,
            execute,
        )
        _verify_pnpm_store(container_id, container_workspace, execute)
        _disconnect_networks(container_id, execute)
        _checked(
            execute(
                (
                    "docker",
                    "exec",
                    "--user",
                    "vscode",
                    "--workdir",
                    str(container_workspace),
                    container_id,
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
                ),
                timeout_seconds=_INSTALLED_NODE_PROOF_TIMEOUT_SECONDS,
            ),
            "Dev Container installed Node resolution",
        )
        _checked(
            execute(
                (
                    *_DEVCONTAINER_COMMAND,
                    "exec",
                    "--container-id",
                    container_id,
                    "--workspace-folder",
                    str(workspace),
                    "--id-label",
                    label,
                    "mise",
                    "run",
                    "check:portable",
                ),
                timeout_seconds=_PORTABLE_PROOF_TIMEOUT_SECONDS,
            ),
            "Dev Container portable proof",
        )
    except RuntimeError as error:
        primary_error = error

    cleanup_error = _cleanup(label, container_id, execute)
    if primary_error is not None and cleanup_error is not None:
        raise RuntimeError(f"{primary_error}; {cleanup_error}") from primary_error
    if primary_error is not None:
        raise primary_error
    if cleanup_error is not None:
        raise cleanup_error


def _admit_portable_proof_deadlines(workspace: Path) -> None:
    plan = load_quality_plan(workspace)
    python_test = plan.commands.get("python.test")
    if python_test is None or "python.test" not in plan.portable_command_ids:
        raise RuntimeError("Dev Container portable proof requires python.test")
    expected_command_ms = int(
        (PYTHON_TEST_PROCESS_TIMEOUT_SECONDS + _PYTHON_TEST_WRAPPER_RESERVE_SECONDS) * 1_000
    )
    if python_test.timeout_ms != expected_command_ms:
        raise RuntimeError("python.test does not preserve its wrapper deadline reserve")
    maximum_portable_timeout_ms = max(command.timeout_ms for command in plan.portable_commands())
    expected_parent_ms = maximum_portable_timeout_ms + (
        _PORTABLE_PROOF_AGGREGATE_RESERVE_SECONDS * 1_000
    )
    if expected_parent_ms != _PORTABLE_PROOF_TIMEOUT_SECONDS * 1_000:
        raise RuntimeError("portable proof does not preserve its aggregate deadline reserve")
    container_command = plan.commands.get("devcontainer.verify")
    stage_budget_ms = (
        _PROVISION_TIMEOUT_SECONDS
        + _INSTALLED_NODE_PROOF_TIMEOUT_SECONDS
        + _PORTABLE_PROOF_TIMEOUT_SECONDS
        + _CLEANUP_TIMEOUT_SECONDS
    ) * 1_000
    if container_command is None or container_command.timeout_ms < stage_budget_ms:
        raise RuntimeError("Dev Container command does not preserve its stage deadline budget")


@contextmanager
def admitted_devcontainer_source(repo_root: Path) -> Iterator[Path]:
    source = repo_root.resolve(strict=True)
    git_marker = source / ".git"
    if git_marker.is_symlink() or not (git_marker.is_dir() or git_marker.is_file()):
        raise RuntimeError("Dev Container source Git metadata is invalid")

    head = _git_stdout(source, ("rev-parse", "HEAD"), "source revision")
    if _COMMIT_SHA.fullmatch(head) is None:
        raise RuntimeError("Dev Container source revision is invalid")
    _require_clean_source(source)
    if git_marker.is_dir():
        yield source
        _require_source_receipt(source, head)
        return

    with tempfile.TemporaryDirectory(prefix="ci-devcontainer-source-") as temporary:
        workspace = Path(temporary) / source.name
        _checked(
            _git(
                source.parent,
                (
                    "clone",
                    "--no-local",
                    "--no-checkout",
                    "--quiet",
                    "--",
                    str(source),
                    str(workspace),
                ),
            ),
            "Dev Container standalone source clone",
        )
        _checked(
            _git(
                workspace,
                ("checkout", "--detach", "--quiet", head),
            ),
            "Dev Container standalone source checkout",
        )
        cloned_git = workspace / ".git"
        if (
            not cloned_git.is_dir()
            or cloned_git.is_symlink()
            or (cloned_git / "objects/info/alternates").exists()
            or _git_stdout(workspace, ("rev-parse", "HEAD"), "cloned revision") != head
        ):
            raise RuntimeError("Dev Container standalone source identity is invalid")
        _require_clean_source(workspace)
        yield workspace
        _require_source_receipt(workspace, head)
        _require_source_receipt(source, head)


def _cleanup(
    label: str,
    expected_container_id: str | None,
    runner: Runner,
) -> RuntimeError | None:
    try:
        container_ids = _owned_containers(label, runner, operation="cleanup discovery")
        if expected_container_id is not None and container_ids != (expected_container_id,):
            raise RuntimeError("Dev Container identity changed before cleanup")
        if container_ids:
            _checked(
                runner(
                    ("docker", "rm", "--force", *container_ids),
                    timeout_seconds=_CLEANUP_TIMEOUT_SECONDS,
                ),
                "Dev Container cleanup",
            )
    except RuntimeError as error:
        return error
    return None


def _single_owned_container(label: str, runner: Runner) -> str:
    container_ids = _owned_containers(label, runner, operation="container discovery")
    if len(container_ids) != 1:
        raise RuntimeError("Dev Container witness requires exactly one owned container")
    return container_ids[0]


def _owned_containers(label: str, runner: Runner, *, operation: str) -> tuple[str, ...]:
    result = runner(
        (
            "docker",
            "ps",
            "--all",
            "--no-trunc",
            "--quiet",
            "--filter",
            f"label={label}",
        ),
        timeout_seconds=_PROVIDER_OPERATION_TIMEOUT_SECONDS,
    )
    _checked(result, f"Dev Container {operation}")
    container_ids = tuple(line.strip() for line in result.stdout.splitlines() if line.strip())
    if len(container_ids) > _MAX_OWNED_CONTAINERS or len(set(container_ids)) != len(container_ids):
        raise RuntimeError("Dev Container cleanup returned an invalid resource set")
    if any(_CONTAINER_ID.fullmatch(container_id) is None for container_id in container_ids):
        raise RuntimeError("Dev Container cleanup returned an invalid container identity")
    return container_ids


def _verify_mount_boundary(
    container_id: str,
    label: str,
    workspace: Path,
    container_workspace: Path,
    runner: Runner,
) -> None:
    result = runner(
        ("docker", "inspect", "--format", "{{json .Mounts}}", container_id),
        timeout_seconds=_PROVIDER_OPERATION_TIMEOUT_SECONDS,
    )
    _checked(result, "Dev Container mount inspection")
    mounts = _json_value(result.stdout, "mount inspection")
    if (
        not isinstance(mounts, list)
        or len(mounts) != _MAX_MOUNTS
        or any(not isinstance(mount, dict) for mount in mounts)
    ):
        raise RuntimeError("Dev Container mount inspection returned an invalid document")

    by_destination: dict[str, tuple[str, str, str, bool]] = {}
    for mount in mounts:
        mount_type = mount.get("Type")
        source = mount.get("Source")
        destination = mount.get("Destination")
        name = mount.get("Name", "")
        writable = mount.get("RW")
        if (
            not isinstance(mount_type, str)
            or not isinstance(source, str)
            or not isinstance(destination, str)
            or not isinstance(name, str)
            or not isinstance(writable, bool)
            or destination in by_destination
            or source in {"/run/docker.sock", "/var/run/docker.sock"}
        ):
            raise RuntimeError("Dev Container mount inspection returned an invalid document")
        by_destination[destination] = (mount_type, source, name, writable)

    workspace_mount = by_destination.pop(str(container_workspace), None)
    if (
        workspace_mount is None
        or workspace_mount[0] != "bind"
        or workspace_mount[1] != str(workspace)
        or workspace_mount[2]
        or not workspace_mount[3]
    ):
        raise RuntimeError("Dev Container workspace mount is invalid")

    devcontainer_id = _devcontainer_id(label)
    volume_sources: dict[str, str] = {}
    for destination, suffix in _expected_volume_suffixes(container_workspace).items():
        mount = by_destination.pop(destination, None)
        if mount is None:
            raise RuntimeError("Dev Container required volume is missing")
        mount_type, source, name, writable = mount
        expected_name = f"{devcontainer_id}-{suffix}"
        source_path = Path(source)
        if (
            mount_type != "volume"
            or not writable
            or name != expected_name
            or not source_path.is_absolute()
            or source_path.anchor != "/"
            or source != source_path.as_posix()
            or ".." in source_path.parts
            or source_path.parts[-2:] != (name, "_data")
        ):
            raise RuntimeError("Dev Container required volume is invalid")
        volume_sources[name] = source

    if by_destination:
        raise RuntimeError("Dev Container mount topology is invalid")
    _verify_volume_boundaries(volume_sources, runner)


def _verify_volume_boundaries(volume_sources: dict[str, str], runner: Runner) -> None:
    result = runner(
        ("docker", "volume", "inspect", *volume_sources),
        timeout_seconds=_PROVIDER_OPERATION_TIMEOUT_SECONDS,
    )
    _checked(result, "Dev Container volume inspection")
    volumes = _json_value(result.stdout, "volume inspection")
    if (
        not isinstance(volumes, list)
        or len(volumes) != len(volume_sources)
        or any(not isinstance(volume, dict) for volume in volumes)
    ):
        raise RuntimeError("Dev Container volume inspection returned an invalid document")

    remaining = dict(volume_sources)
    for volume in volumes:
        name = volume.get("Name")
        mountpoint = volume.get("Mountpoint")
        options = volume.get("Options")
        expected_mountpoint = remaining.pop(name, None) if isinstance(name, str) else None
        if (
            expected_mountpoint is None
            or volume.get("Driver") != "local"
            or "Options" not in volume
            or (options is not None and options != {})
            or volume.get("Scope") != "local"
            or mountpoint != expected_mountpoint
        ):
            raise RuntimeError("Dev Container volume inspection returned an invalid document")
    if remaining:
        raise RuntimeError("Dev Container volume inspection returned an invalid document")


def _expected_volume_suffixes(container_workspace: Path) -> dict[str, str]:
    return {
        str(container_workspace / "backend" / ".venv"): "backend-venv",
        "/home/vscode/.local/share/mise": "mise-data",
        str(container_workspace / ".pnpm-store"): "pnpm-store",
        str(container_workspace / "node_modules"): "root-node-modules",
        str(container_workspace / "frontend" / "node_modules"): "frontend-node-modules",
    }


def _devcontainer_id(label: str) -> str:
    key, separator, value = label.partition("=")
    if not separator or not key or not value:
        raise ValueError("Dev Container identity label is invalid")
    payload = json.dumps(
        {key: value},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    number = int.from_bytes(hashlib.sha256(payload).digest(), "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, len(_BASE32_DIGITS))
        encoded = _BASE32_DIGITS[remainder] + encoded
    return encoded.rjust(52, "0")


def _verify_pnpm_store(
    container_id: str,
    container_workspace: Path,
    runner: Runner,
) -> None:
    result = runner(
        (
            "docker",
            "exec",
            "--user",
            "vscode",
            "--workdir",
            str(container_workspace),
            container_id,
            "mise",
            "exec",
            "--",
            "pnpm",
            "store",
            "path",
        ),
        timeout_seconds=_PROVIDER_OPERATION_TIMEOUT_SECONDS,
    )
    _checked(result, "Dev Container pnpm store inspection")
    lines = tuple(line.strip() for line in result.stdout.splitlines() if line.strip())
    if len(lines) != 1:
        raise RuntimeError("Dev Container pnpm store inspection returned an invalid path")
    raw_store_path = lines[0]
    store_path = Path(raw_store_path)
    if (
        not store_path.is_absolute()
        or raw_store_path != store_path.as_posix()
        or ".." in store_path.parts
        or not store_path.is_relative_to(container_workspace / ".pnpm-store")
    ):
        raise RuntimeError("Dev Container pnpm store escaped its identity volume")

    canonical = runner(
        (
            "docker",
            "exec",
            "--user",
            "vscode",
            container_id,
            "readlink",
            "--canonicalize-existing",
            "--",
            raw_store_path,
        ),
        timeout_seconds=_PROVIDER_OPERATION_TIMEOUT_SECONDS,
    )
    _checked(canonical, "Dev Container pnpm store canonicalization")
    canonical_lines = tuple(line.strip() for line in canonical.stdout.splitlines() if line.strip())
    if len(canonical_lines) != 1 or canonical_lines[0] != raw_store_path:
        raise RuntimeError("Dev Container pnpm store escaped its identity volume")


def _disconnect_networks(container_id: str, runner: Runner) -> None:
    networks = _container_networks(container_id, runner)
    for network in networks:
        _checked(
            runner(
                ("docker", "network", "disconnect", network, container_id),
                timeout_seconds=_PROVIDER_OPERATION_TIMEOUT_SECONDS,
            ),
            "Dev Container network isolation",
        )
    if _container_networks(container_id, runner):
        raise RuntimeError("Dev Container retained network access before portable proof")


def _container_networks(container_id: str, runner: Runner) -> tuple[str, ...]:
    result = runner(
        (
            "docker",
            "inspect",
            "--format",
            "{{json .NetworkSettings.Networks}}",
            container_id,
        ),
        timeout_seconds=_PROVIDER_OPERATION_TIMEOUT_SECONDS,
    )
    _checked(result, "Dev Container network inspection")
    value = _json_value(result.stdout, "network inspection")
    if not isinstance(value, dict) or len(value) > _MAX_NETWORKS:
        raise RuntimeError("Dev Container network inspection returned an invalid document")
    names = tuple(value)
    if any(_NETWORK_NAME.fullmatch(name) is None for name in names):
        raise RuntimeError("Dev Container network inspection returned an invalid identity")
    return names


def _json_value(raw: str, operation: str) -> object:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Dev Container {operation} returned invalid JSON") from error


def _run(
    argv: Sequence[str],
    *,
    timeout_seconds: float,
) -> CommandResult:
    residual_policy: ResidualProcessGroupPolicy = "reject"
    if tuple(argv[:3]) == (
        *_DEVCONTAINER_COMMAND,
        "up",
    ):
        residual_policy = "terminate"
    started = monotonic()
    result = spawn(
        argv[0],
        argv[1:],
        cwd=REPO_ROOT,
        max_buffer=_MAX_OUTPUT_BYTES,
        timeout_seconds=timeout_seconds,
        residual_process_group_policy=residual_policy,
    )
    print(
        json.dumps(
            {
                "devcontainerOperation": list(argv[:3]),
                "elapsedSeconds": monotonic() - started,
                "status": result.status,
            }
        ),
        flush=True,
    )
    return result


def _git(cwd: Path, arguments: Sequence[str]) -> CommandResult:
    return spawn(
        "git",
        arguments,
        cwd=cwd,
        max_buffer=_MAX_OUTPUT_BYTES,
        timeout_seconds=_PROVIDER_OPERATION_TIMEOUT_SECONDS,
    )


def _git_stdout(cwd: Path, arguments: Sequence[str], operation: str) -> str:
    result = _git(cwd, arguments)
    _checked(result, f"Dev Container {operation}")
    return result.stdout.strip()


def _require_clean_source(source: Path) -> None:
    if _git_stdout(
        source,
        ("status", "--porcelain", "--untracked-files=all"),
        "source status",
    ):
        raise RuntimeError("Dev Container source must be clean")


def _require_source_receipt(source: Path, expected_head: str) -> None:
    if _git_stdout(source, ("rev-parse", "HEAD"), "source revision") != expected_head:
        raise RuntimeError("Dev Container source revision changed during proof")
    _require_clean_source(source)


def _checked(result: CommandResult, operation: str) -> None:
    if result.error is not None:
        raise RuntimeError(f"{operation} failed: {result.error}")
    if result.status != 0:
        detail = (
            "\n".join(output for output in (result.stderr.strip(), result.stdout.strip()) if output)
            or f"status {result.status}"
        )
        raise RuntimeError(f"{operation} failed: {detail}")


def main() -> int:
    try:
        identity = derive_instance_identity(REPO_ROOT)
        lock_directory = identity.state_home / "witness-locks"
        ensure_private_directory(identity.state_home)
        ensure_private_directory(lock_directory)
        with (
            exclusive_private_lock(lock_directory / f"{identity.project_name}.lock"),
            admitted_devcontainer_source(REPO_ROOT) as workspace,
        ):
            verify_devcontainer(
                witness_id=identity.project_name,
                workspace_root=workspace,
            )
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "reportId": "ci-coordinator.devcontainer-witness",
                "schemaVersion": 1,
                "state": "passed",
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
