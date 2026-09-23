from __future__ import annotations

import json
import os
import re
import resource
import stat
import sys
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from scripts.bounded_process import spawn
from scripts.ci_business_witness.lifecycle import WitnessBudget
from scripts.ci_business_witness.oracle import WitnessFailure, require

MAXIMUM_WORKSPACE_BYTES = 2 * 1024 * 1024 * 1024
MAXIMUM_SECRET_BYTES = 16 * 1024 * 1024
_FLAGS = frozenset({"rw", "noswap", "nosuid", "nodev", "noexec"})
_RUNTIME_FLAGS = _FLAGS - {"noexec"}


def admit_host_policy(swaps: str, core_pattern: str, core_uses_pid: str) -> None:
    rows = swaps.splitlines()
    require(
        bool(rows)
        and rows[0].split() == ["Filename", "Type", "Size", "Used", "Priority"]
        and not any(row.strip() for row in rows[1:]),
        "volatile-host-swap",
    )
    require(
        core_pattern.strip() == "/dev/null" and core_uses_pid.strip() == "0",
        "volatile-host-core-policy",
    )


def admit_mount(
    document: str, directory: Path, project: str, *, flags: frozenset[str] = _FLAGS
) -> str:
    parsed = json.loads(document)
    require(isinstance(parsed, dict), "volatile-mount-shape")
    mounts = parsed.get("filesystems")
    require(isinstance(mounts, list) and len(mounts) == 1, "volatile-mount-population")
    item = mounts[0]
    require(isinstance(item, dict), "volatile-mount-shape")
    options = item.get("options")
    device = item.get("maj:min")
    require(
        item.get("target") == str(directory)
        and item.get("source") == project
        and item.get("fstype") == "tmpfs"
        and isinstance(options, str)
        and flags <= set(options.split(","))
        and (flags != _RUNTIME_FLAGS or "noexec" not in options.split(","))
        and isinstance(device, str)
        and re.fullmatch(r"[0-9]+:[0-9]+", device) is not None,
        "volatile-mount-identity",
    )
    return str(device)


class VolatileWorkspace:
    def __init__(self, project: str, budget: WitnessBudget) -> None:
        require(re.fullmatch(r"ci-business-[0-9a-f]{24}", project) is not None, "volatile-project")
        self.project = project
        self.budget = budget
        self.directory: Path | None = None
        self._original_inode: tuple[int, int] | None = None
        self._device: str | None = None
        self.runtime_directory: Path | None = None
        self._runtime_inode: tuple[int, int] | None = None
        self._runtime_device: str | None = None
        self.receipt: dict[str, object] = {"admitted": False, "cleanup": "not-started"}

    def _control(self, *arguments: str) -> None:
        result = spawn(
            "sudo",
            ("--non-interactive", *arguments),
            cwd=Path("/"),
            env={"PATH": os.environ.get("PATH", "")},
            max_buffer=8192,
            timeout_seconds=self.budget.timeout(60),
        )
        require(
            result.status == 0 and result.error is None and result.failure_kind is None,
            "volatile-host-control",
        )

    def _mount(self, directory: Path | None = None) -> str | None:
        require(self.directory is not None, "volatile-directory-absent")
        result = spawn(
            "findmnt",
            (
                "--json",
                "--mountpoint",
                str(directory or self.directory),
                "--output",
                "TARGET,SOURCE,FSTYPE,OPTIONS,MAJ:MIN",
            ),
            cwd=Path("/"),
            max_buffer=8192,
            timeout_seconds=self.budget.timeout(10),
        )
        require(result.error is None and result.failure_kind is None, "volatile-mount-observation")
        if result.status == 1 and not result.stdout.strip():
            return None
        require(result.status == 0, "volatile-mount-observation")
        return result.stdout

    def prepare(self) -> Path:
        require(
            os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
            and sys.platform == "linux",
            "volatile-dedicated-host-required",
        )
        require(self.directory is None, "volatile-duplicate-prepare")
        self._control("swapoff", "--all")
        self._control(
            "sysctl",
            "--quiet",
            "--write",
            "kernel.core_pattern=/dev/null",
            "kernel.core_uses_pid=0",
        )
        self._admit_host()
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        self.directory = Path(tempfile.mkdtemp(prefix=self.project + "-"))
        initial = self.directory.lstat()
        self._original_inode = (initial.st_dev, initial.st_ino)
        require(
            self.directory.resolve() == self.directory
            and stat.S_ISDIR(initial.st_mode)
            and stat.S_IMODE(initial.st_mode) == 0o700
            and initial.st_uid == os.getuid(),
            "volatile-directory-identity",
        )
        self._control(
            "mount",
            "--types",
            "tmpfs",
            "--options",
            f"noswap,nosuid,nodev,noexec,size={MAXIMUM_SECRET_BYTES},mode=0700,"
            f"uid={os.getuid()},gid={os.getgid()}",
            self.project,
            str(self.directory),
        )
        initial_mount = self._mount()
        require(initial_mount is not None, "volatile-mount-absent")
        self._device = admit_mount(str(initial_mount), self.directory, self.project)
        self.runtime_directory = self.directory / "runtime"
        self.runtime_directory.mkdir(mode=0o700)
        initial_runtime = self.runtime_directory.lstat()
        self._runtime_inode = (initial_runtime.st_dev, initial_runtime.st_ino)
        self._control(
            "mount",
            "--types",
            "tmpfs",
            "--options",
            f"noswap,nosuid,nodev,size={MAXIMUM_WORKSPACE_BYTES},mode=0700,"
            f"uid={os.getuid()},gid={os.getgid()}",
            self.project + "-runtime",
            str(self.runtime_directory),
        )
        initial_runtime_mount = self._mount(self.runtime_directory)
        require(initial_runtime_mount is not None, "volatile-runtime-absent")
        self._runtime_device = admit_mount(
            str(initial_runtime_mount),
            self.runtime_directory,
            self.project + "-runtime",
            flags=_RUNTIME_FLAGS,
        )
        self.verify()
        self.receipt["admitted"] = True
        return self.directory

    def _admit_host(self) -> None:
        admit_host_policy(
            Path("/proc/swaps").read_text(),
            Path("/proc/sys/kernel/core_pattern").read_text(),
            Path("/proc/sys/kernel/core_uses_pid").read_text(),
        )
        null = Path("/dev/null").stat()
        require(
            stat.S_ISCHR(null.st_mode)
            and (os.major(null.st_rdev), os.minor(null.st_rdev)) == (1, 3),
            "volatile-core-discard-device",
        )

    def verify(self) -> None:
        if self.directory is None:
            raise WitnessFailure("volatile-directory-absent")
        self._admit_host()
        document = self._mount()
        require(document is not None, "volatile-mount-absent")
        device = admit_mount(str(document), self.directory, self.project)
        current = self.directory.lstat()
        require(
            not self.directory.is_symlink()
            and device == f"{os.major(current.st_dev)}:{os.minor(current.st_dev)}"
            and (self._device is None or device == self._device)
            and current.st_uid == os.getuid()
            and stat.S_IMODE(current.st_mode) == 0o700,
            "volatile-directory-drift",
        )
        capacity = os.statvfs(self.directory)
        require(
            0 < capacity.f_blocks * capacity.f_frsize <= MAXIMUM_SECRET_BYTES,
            "volatile-capacity",
        )
        self._device = device
        if self.runtime_directory is None:
            raise WitnessFailure("volatile-runtime-absent")
        runtime = self._mount(self.runtime_directory)
        require(runtime is not None, "volatile-runtime-absent")
        runtime_device = admit_mount(
            str(runtime),
            self.runtime_directory,
            self.project + "-runtime",
            flags=_RUNTIME_FLAGS,
        )
        current_runtime = self.runtime_directory.lstat()
        runtime_capacity = os.statvfs(self.runtime_directory)
        require(
            runtime_device
            == f"{os.major(current_runtime.st_dev)}:{os.minor(current_runtime.st_dev)}"
            and (self._runtime_device is None or runtime_device == self._runtime_device)
            and current_runtime.st_uid == os.getuid()
            and stat.S_IMODE(current_runtime.st_mode) == 0o700
            and not self.runtime_directory.is_symlink()
            and 0
            < runtime_capacity.f_blocks * runtime_capacity.f_frsize
            <= MAXIMUM_WORKSPACE_BYTES,
            "volatile-runtime-drift",
        )
        self._runtime_device = runtime_device
        self.receipt.update(
            filesystem="tmpfs",
            swap="disabled",
            coreDumpTarget="nonregular-discard-device",
            maximumSecretBytes=MAXIMUM_SECRET_BYTES,
            maximumRuntimeBytes=MAXIMUM_WORKSPACE_BYTES,
            device=device,
            runtimeDevice=runtime_device,
        )

    def own(self, path: Path, *, uid: int, gid: int, mode: int) -> None:
        if self.directory is None or self._device is None:
            raise WitnessFailure("volatile-not-admitted")
        require(
            path.resolve() == path
            and path.is_relative_to(self.directory)
            and not path.is_symlink()
            and uid >= 0
            and gid >= 0,
            "volatile-owned-path",
        )
        current = path.stat()
        require(
            stat.S_ISREG(current.st_mode) or stat.S_ISDIR(current.st_mode),
            "volatile-owned-kind",
        )
        expected_device = (
            self._runtime_device
            if self.runtime_directory is not None and path.is_relative_to(self.runtime_directory)
            else self._device
        )
        require(
            f"{os.major(current.st_dev)}:{os.minor(current.st_dev)}" == expected_device,
            "volatile-owned-filesystem",
        )
        self._control("chown", "--no-dereference", f"{uid}:{gid}", "--", str(path))
        self._control("chmod", f"{mode:o}", "--", str(path))
        after = path.stat()
        require(
            (after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)) == (uid, gid, mode),
            "volatile-owned-permissions",
        )

    def close(self) -> None:
        if self.directory is None:
            self.receipt["cleanup"] = "not-allocated"
            return
        if self.runtime_directory is not None:
            runtime = self._mount(self.runtime_directory)
            if runtime is not None:
                device = admit_mount(
                    runtime, self.runtime_directory, self.project + "-runtime", flags=frozenset()
                )
                require(
                    self._runtime_device is not None and device == self._runtime_device,
                    "volatile-runtime-cleanup-device",
                )
                self._control("umount", "--", str(self.runtime_directory))
                require(self._mount(self.runtime_directory) is None, "volatile-runtime-unmount")
            initial_runtime = self.runtime_directory.lstat()
            require(
                (initial_runtime.st_dev, initial_runtime.st_ino) == self._runtime_inode
                and stat.S_ISDIR(initial_runtime.st_mode)
                and not self.runtime_directory.is_symlink(),
                "volatile-runtime-cleanup-identity",
            )
            self.runtime_directory.rmdir()
        document = self._mount()
        if document is not None:
            device = admit_mount(document, self.directory, self.project, flags=frozenset())
            require(
                self._device is not None and device == self._device,
                "volatile-cleanup-device",
            )
            self._control("umount", "--", str(self.directory))
            require(self._mount() is None, "volatile-unmount")
        initial = self.directory.lstat()
        require(
            (initial.st_dev, initial.st_ino) == self._original_inode
            and stat.S_ISDIR(initial.st_mode)
            and not self.directory.is_symlink(),
            "volatile-cleanup-identity",
        )
        self.directory.rmdir()
        self.receipt["cleanup"] = "complete"


class _ProviderObject(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class _Limit(_ProviderObject):
    Name: str
    Hard: int
    Soft: int


class _Logging(_ProviderObject):
    Type: str
    Config: dict[str, str] | None = None


class _Host(_ProviderObject):
    ReadonlyRootfs: bool
    Privileged: bool
    LogConfig: _Logging
    Ulimits: list[_Limit]


class _Health(_ProviderObject):
    Test: list[str]


class _Configuration(_ProviderObject):
    User: str
    Labels: dict[str, str]
    Healthcheck: _Health | None = None


class _Mount(_ProviderObject):
    Type: str
    Source: str
    Destination: str
    RW: bool


class _Container(_ProviderObject):
    Id: str
    Image: str
    HostConfig: _Host
    Config: _Configuration
    Mounts: list[_Mount]


def admit_container_storage(
    value: object,
    *,
    directory: Path,
    project: str,
    expected_id: str,
    expected_image: str,
    expected_user: str,
    expected_mounts: dict[str, tuple[Path, bool]],
) -> None:
    inspected = _Container.model_validate(value)
    host, config = inspected.HostConfig, inspected.Config
    require(
        inspected.Id == expected_id
        and inspected.Image == expected_image
        and config.Labels.get("com.docker.compose.project") == project
        and config.User == expected_user,
        "volatile-container-owner",
    )
    require(
        host.ReadonlyRootfs
        and not host.Privileged
        and host.LogConfig.Type == "none"
        and not host.LogConfig.Config
        and (config.Healthcheck is None or config.Healthcheck.Test == ["NONE"]),
        "volatile-container-policy",
    )
    core = [item for item in host.Ulimits if item.Name == "core"]
    require(
        len(core) == 1 and core[0].Hard == 0 and core[0].Soft == 0,
        "volatile-container-core-limit",
    )
    require(len(inspected.Mounts) == len(expected_mounts), "volatile-container-mounts")
    destinations: set[str] = set()
    for mount in inspected.Mounts:
        require(
            mount.Type == "bind"
            and mount.Destination in expected_mounts
            and mount.Destination not in destinations,
            "volatile-container-mount-kind",
        )
        destinations.add(mount.Destination)
        path = Path(mount.Source)
        require(path.resolve() == path and not path.is_symlink(), "volatile-container-mount-path")
        require(
            (path, mount.RW) == expected_mounts[mount.Destination]
            and (not mount.RW or path.is_relative_to(directory)),
            "volatile-container-persistent-mount",
        )
    require(destinations == expected_mounts.keys(), "volatile-container-mount-population")
