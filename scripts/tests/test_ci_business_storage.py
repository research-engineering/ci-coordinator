from __future__ import annotations

import json
import os
import resource
import secrets
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, cast

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from pydantic import ValidationError
from scripts.bounded_process import CommandResult
from scripts.ci_business_witness import fixture as fixture_owner
from scripts.ci_business_witness import storage
from scripts.ci_business_witness.lifecycle import WitnessBudget
from scripts.ci_business_witness.oracle import WitnessFailure

_PROJECT = "ci-business-" + "a" * 24
_CONTAINER = "b" * 64
_IMAGE = "sha256:" + "c" * 64
_SWAP_HEADER = "Filename\tType\tSize\tUsed\tPriority\n"
_NOEXEC_MOUNT_OPTIONS = "rw,noswap,nosuid,nodev,noexec"
_RUNTIME_FLAGS = frozenset({"rw", "noswap", "nosuid", "nodev"})


def _object(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _array(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def _denied(*_args: object, **_kwargs: object) -> NoReturn:
    pytest.fail("storage unit fixture reached a real host-control boundary")


@pytest.fixture(autouse=True)
def _no_real_host_control(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "spawn", _denied)
    monkeypatch.setattr(resource, "setrlimit", _denied)


@pytest.mark.parametrize("padding", ["", "\n  \n"])
def test_empty_swap_inventory_and_discard_core_policy_are_admitted(padding: str) -> None:
    storage.admit_host_policy(_SWAP_HEADER + padding, "/dev/null\n", "0\n")


@pytest.mark.parametrize(
    "swaps",
    [
        "",
        "Filename Type Size Used\n",
        "Filename Type Size Used Priority Extra\n",
        "\n" + _SWAP_HEADER,
        _SWAP_HEADER + "/swapfile file 8192 0 -2\n",
        _SWAP_HEADER + "/dev/zram0 partition 8192 1 100\n",
        _SWAP_HEADER + "unparseable trailing row\n",
    ],
)
def test_swap_admission_rejects_missing_malformed_and_nonempty_inventory(swaps: str) -> None:
    with pytest.raises(WitnessFailure, match=r"^volatile-host-swap$"):
        storage.admit_host_policy(swaps, "/dev/null", "0")


@pytest.mark.parametrize(
    ("pattern", "uses_pid"),
    [
        ("|/usr/lib/systemd/systemd-coredump", "0"),
        ("core", "0"),
        ("/dev/null.%p", "0"),
        ("/dev/null", "1"),
        ("/dev/null", "true"),
        ("/dev/null", ""),
    ],
)
def test_core_admission_rejects_collectors_regular_targets_and_pid_suffixes(
    pattern: str, uses_pid: str
) -> None:
    with pytest.raises(WitnessFailure, match=r"^volatile-host-core-policy$"):
        storage.admit_host_policy(_SWAP_HEADER, pattern, uses_pid)


def _mount_document(
    path: Path, source: str = _PROJECT, options: str = _NOEXEC_MOUNT_OPTIONS, device: str = "0:41"
) -> dict[str, object]:
    return {
        "filesystems": [
            {
                "target": str(path),
                "source": source,
                "fstype": "tmpfs",
                "options": options,
                "maj:min": device,
            }
        ]
    }


def _mount_entry(document: dict[str, object]) -> dict[str, object]:
    return _object(_array(document["filesystems"])[0])


def test_secret_and_runtime_mounts_have_distinct_execution_contracts(tmp_path: Path) -> None:
    secret = _mount_document(tmp_path)
    runtime = _mount_document(tmp_path / "runtime", _PROJECT + "-runtime", "rw,noswap,nosuid,nodev")
    assert storage.admit_mount(json.dumps(secret), tmp_path, _PROJECT) == "0:41"
    assert (
        storage.admit_mount(
            json.dumps(runtime), tmp_path / "runtime", _PROJECT + "-runtime", flags=_RUNTIME_FLAGS
        )
        == "0:41"
    )
    with pytest.raises(WitnessFailure, match=r"^volatile-mount-identity$"):
        storage.admit_mount(json.dumps(runtime), tmp_path / "runtime", _PROJECT + "-runtime")


@pytest.mark.parametrize("missing", ["rw", "noswap", "nosuid", "nodev", "noexec"])
def test_each_required_secret_mount_flag_is_an_independent_operand(
    tmp_path: Path, missing: str
) -> None:
    document = _mount_document(
        tmp_path, options=",".join(x for x in _NOEXEC_MOUNT_OPTIONS.split(",") if x != missing)
    )
    with pytest.raises(WitnessFailure, match=r"^volatile-mount-identity$"):
        storage.admit_mount(json.dumps(document), tmp_path, _PROJECT)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target", "/foreign"),
        ("source", "foreign"),
        ("fstype", "ext4"),
        ("options", None),
        ("maj:min", "0:41:2"),
        ("maj:min", 41),
    ],
)
def test_mount_target_source_kind_and_device_cannot_be_substituted(
    tmp_path: Path, field: str, value: object
) -> None:
    document = _mount_document(tmp_path)
    _mount_entry(document)[field] = value
    with pytest.raises(WitnessFailure, match=r"^volatile-mount-identity$"):
        storage.admit_mount(json.dumps(document), tmp_path, _PROJECT)


@pytest.mark.parametrize("population", [None, [], [None], [{}, {}]])
def test_mount_observation_requires_one_complete_filesystem(
    tmp_path: Path, population: object
) -> None:
    with pytest.raises(WitnessFailure, match=r"^volatile-mount-(population|shape)$"):
        storage.admit_mount(json.dumps({"filesystems": population}), tmp_path, _PROJECT)


@pytest.mark.parametrize("document", ["not-json", "[]", "null"])
def test_malformed_mount_document_is_not_evidence(tmp_path: Path, document: str) -> None:
    with pytest.raises((ValueError, WitnessFailure)):
        storage.admit_mount(document, tmp_path, _PROJECT)


@dataclass
class _ContainerCase:
    directory: Path
    value: dict[str, object]
    expected: dict[str, tuple[Path, bool]]

    def admit(self) -> None:
        storage.admit_container_storage(
            self.value,
            directory=self.directory,
            project=_PROJECT,
            expected_id=_CONTAINER,
            expected_image=_IMAGE,
            expected_user="10001:10001",
            expected_mounts=self.expected,
        )


def _container_case(tmp_path: Path) -> _ContainerCase:
    root = tmp_path.resolve() / "ram"
    root.mkdir(mode=0o700)
    secret = root / "credential"
    secret.write_text("fixture-only")
    scratch = root / "runtime"
    scratch.mkdir(mode=0o700)
    public = tmp_path.resolve() / "bootstrap.sql"
    public.write_text("SELECT 1;")
    expected = {
        "/run/secrets/credential": (secret, False),
        "/scratch": (scratch, True),
        "/bootstrap.sql": (public, False),
    }
    value: dict[str, object] = {
        "Id": _CONTAINER,
        "Image": _IMAGE,
        "HostConfig": {
            "ReadonlyRootfs": True,
            "Privileged": False,
            "LogConfig": {"Type": "none", "Config": None},
            "Ulimits": [
                {"Name": "core", "Hard": 0, "Soft": 0},
                {"Name": "nofile", "Hard": 1024, "Soft": 1024},
            ],
        },
        "Config": {
            "User": "10001:10001",
            "Labels": {"com.docker.compose.project": _PROJECT},
            "Healthcheck": None,
        },
        "Mounts": [
            {"Type": "bind", "Source": str(path), "Destination": destination, "RW": writable}
            for destination, (path, writable) in expected.items()
        ],
    }
    return _ContainerCase(root, value, expected)


@pytest.mark.parametrize("disabled_healthcheck", [False, True])
def test_container_accepts_exact_private_writes_and_explicit_public_readonly_input(
    tmp_path: Path, disabled_healthcheck: bool
) -> None:
    case = _container_case(tmp_path)
    if disabled_healthcheck:
        _object(case.value["Config"])["Healthcheck"] = {"Test": ["NONE"]}
        _object(_object(case.value["HostConfig"])["LogConfig"])["Config"] = {}
    case.admit()


@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        (("Id",), "d" * 64, "owner"),
        (("Image",), "sha256:" + "e" * 64, "owner"),
        (("Config", "User"), "0", "owner"),
        (("Config", "Labels", "com.docker.compose.project"), "foreign", "owner"),
        (("HostConfig", "ReadonlyRootfs"), False, "policy"),
        (("HostConfig", "Privileged"), True, "policy"),
        (("HostConfig", "LogConfig", "Type"), "json-file", "policy"),
        (("HostConfig", "LogConfig", "Config"), {"path": "/persistent"}, "policy"),
        (("Config", "Healthcheck"), {"Test": ["CMD", "probe"]}, "policy"),
        (("Config", "Healthcheck"), {"Test": []}, "policy"),
    ],
)
def test_container_owner_and_persistence_controls_are_independent(
    tmp_path: Path, path: tuple[str, ...], value: object, code: str
) -> None:
    case = _container_case(tmp_path)
    target = case.value
    for part in path[:-1]:
        target = _object(target[part])
    target[path[-1]] = value
    with pytest.raises(WitnessFailure, match=rf"^volatile-container-{code}$"):
        case.admit()


@pytest.mark.parametrize("change", ["missing", "duplicate", "soft", "hard", "boolean"])
def test_core_limits_require_one_typed_zero_pair(tmp_path: Path, change: str) -> None:
    case = _container_case(tmp_path)
    limits = _array(_object(case.value["HostConfig"])["Ulimits"])
    core = _object(limits[0])
    if change == "missing":
        limits.pop(0)
    elif change == "duplicate":
        limits.append(dict(core))
    else:
        core["Soft" if change == "soft" else "Hard"] = False if change == "boolean" else 1
    with pytest.raises((WitnessFailure, ValidationError)):
        case.admit()


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "extra",
        "duplicate",
        "volume",
        "tmpfs",
        "destination",
        "writable-secret",
        "typed-rw",
    ],
)
def test_container_mount_inventory_cannot_hide_a_persistent_or_unbound_consumer(
    tmp_path: Path, change: str
) -> None:
    case = _container_case(tmp_path)
    mounts = _array(case.value["Mounts"])
    first = _object(mounts[0])
    if change == "missing":
        mounts.pop()
    elif change == "extra":
        mounts.append(dict(first))
    elif change == "duplicate":
        mounts[1] = dict(first)
    elif change in {"volume", "tmpfs"}:
        first["Type"] = change
    elif change == "destination":
        first["Destination"] = "/unbound"
    else:
        first["RW"] = True if change == "writable-secret" else "false"
    with pytest.raises((WitnessFailure, ValidationError)):
        case.admit()


@pytest.mark.parametrize("change", ["sibling-prefix", "symlink", "parent-symlink", "dotdot"])
def test_private_mount_path_must_be_canonical_and_inside_the_owned_workspace(
    tmp_path: Path, change: str
) -> None:
    case = _container_case(tmp_path)
    if change == "sibling-prefix":
        source = case.directory.with_name("ram-foreign")
        source.mkdir()
    elif change == "symlink":
        source = case.directory / "alias"
        source.symlink_to(case.directory / "runtime", target_is_directory=True)
    elif change == "parent-symlink":
        alias = case.directory / "alias"
        alias.symlink_to(case.directory, target_is_directory=True)
        source = alias / "runtime"
    else:
        source = case.directory / "runtime" / ".." / "runtime"
    case.expected["/scratch"] = (source, True)
    _object(_array(case.value["Mounts"])[1])["Source"] = str(source)
    with pytest.raises(WitnessFailure, match=r"^volatile-container-(mount-path|persistent-mount)$"):
        case.admit()


class _Controller:
    def __init__(self, base: Path) -> None:
        self.base = base
        self.commands: list[tuple[str, ...]] = []
        self.mounts: dict[Path, dict[str, object]] = {}
        self.failed_mount: Path | None = None
        self.unadmitted_mount: Path | None = None
        self.refuse_unmount: Path | None = None
        self.underlay_file: Path | None = None
        self.swaps = _SWAP_HEADER
        self.capacities = {base: 16 * 1024 * 1024, base / "runtime": 2 * 1024 * 1024 * 1024}
        self.core_limits: list[tuple[int, tuple[int, int]]] = []

    def allocate(self, *, prefix: str) -> str:
        assert prefix == _PROJECT + "-"
        self.base.mkdir(mode=0o700)
        return str(self.base)

    def control(self, *args: str) -> None:
        self.commands.append(args)
        if args[0] in {"swapoff", "sysctl"}:
            return
        path = Path(args[-1])
        if args[0] in {"chown", "chmod"}:
            assert path.resolve() == path and path.is_relative_to(self.base)
            # Model ownership changes clearing setgid without changing the host UID.
            mode = stat.S_IMODE(path.stat().st_mode)
            path.chmod(mode & ~stat.S_ISGID if args[0] == "chown" else int(args[1], 8))
            return
        assert path in {self.base, self.base / "runtime"}
        if args[0] == "mount":
            if path == self.failed_mount:
                raise WitnessFailure("injected-mount-failure")
            device = path.stat().st_dev
            self.mounts[path] = _mount_document(
                path,
                args[-2],
                "rw," + args[args.index("--options") + 1],
                f"{os.major(device)}:{os.minor(device)}",
            )
            if path == self.unadmitted_mount:
                _mount_entry(self.mounts[path])["options"] = "rw,nosuid,nodev,noexec"
        elif args[0] == "umount":
            if path != self.refuse_unmount:
                self.mounts.pop(path)
            if self.underlay_file is not None and self.underlay_file.parent == path:
                self.underlay_file.write_text("preserve underlay")
        else:
            pytest.fail("unexpected host-control command")

    def observe(self, directory: Path | None = None) -> str | None:
        document = self.mounts.get(directory or self.base)
        return None if document is None else json.dumps(document)

    def host_policy(self) -> None:
        storage.admit_host_policy(self.swaps, "/dev/null", "0")

    def capacity(self, directory: Path) -> os.statvfs_result:
        blocks = self.capacities[directory] // 4096
        return os.statvfs_result((4096, 4096, blocks, blocks, blocks, 1024, 1024, 1024, 0, 255))

    def core_limit(self, kind: int, limits: tuple[int, int]) -> None:
        self.core_limits.append((kind, limits))


def _workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[storage.VolatileWorkspace, _Controller]:
    controller = _Controller(tmp_path.resolve() / "allocated")
    workspace = storage.VolatileWorkspace(_PROJECT, WitnessBudget(clock=lambda: 0.0))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(tempfile, "mkdtemp", controller.allocate)
    monkeypatch.setattr(resource, "setrlimit", controller.core_limit)
    monkeypatch.setattr(os, "statvfs", controller.capacity)
    monkeypatch.setattr(workspace, "_control", controller.control)
    monkeypatch.setattr(workspace, "_mount", controller.observe)
    monkeypatch.setattr(workspace, "_admit_host", controller.host_policy)
    return workspace, controller


def test_workspace_has_two_admitted_zones_and_unmounts_in_dependency_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, controller = _workspace(tmp_path, monkeypatch)
    directory = workspace.prepare()
    assert directory == controller.base and workspace.receipt["admitted"] is True
    mounted = [args for args in controller.commands if args[0] == "mount"]
    assert len(mounted) == 2
    assert "noexec" in mounted[0][mounted[0].index("--options") + 1].split(",")
    assert "noexec" not in mounted[1][mounted[1].index("--options") + 1].split(",")
    assert controller.core_limits == [(resource.RLIMIT_CORE, (0, 0))]
    workspace.close()
    assert [args[-1] for args in controller.commands if args[0] == "umount"] == [
        str(directory / "runtime"),
        str(directory),
    ]
    assert not directory.exists() and not controller.mounts
    assert workspace.receipt["cleanup"] == "complete"


@pytest.mark.parametrize(
    "drift",
    [
        "swap",
        "secret-flags",
        "runtime-flags",
        "runtime-noexec",
        "secret-absent",
        "runtime-absent",
        "secret-mode",
        "runtime-mode",
        "secret-capacity",
        "runtime-capacity",
    ],
)
def test_verify_rechecks_current_policy_and_each_zone_after_initial_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    workspace, controller = _workspace(tmp_path, monkeypatch)
    workspace.prepare()
    target = controller.base / "runtime" if drift.startswith("runtime") else controller.base
    if drift == "swap":
        controller.swaps += "/swapfile file 8192 0 -2\n"
    elif drift == "runtime-noexec":
        entry = _mount_entry(controller.mounts[target])
        entry["options"] = str(entry["options"]) + ",noexec"
    elif drift.endswith("flags"):
        entry = _mount_entry(controller.mounts[target])
        entry["options"] = str(entry["options"]).replace("noswap,", "")
    elif drift.endswith("absent"):
        controller.mounts.pop(target)
    elif drift.endswith("mode"):
        target.chmod(0o755)
    else:
        controller.capacities[target] = 0
    with pytest.raises(WitnessFailure):
        workspace.verify()


@pytest.mark.parametrize(
    "failure",
    [
        "environment",
        "host-swap",
        "secret-mount",
        "runtime-mount",
        "secret-capacity",
        "runtime-capacity",
    ],
)
def test_failed_allocation_cleans_only_allocated_known_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    workspace, controller = _workspace(tmp_path, monkeypatch)
    if failure == "environment":
        monkeypatch.setenv("RUNNER_ENVIRONMENT", "self-hosted")
    elif failure == "host-swap":
        controller.swaps += "/swapfile file 8192 0 -2\n"
    elif failure.endswith("mount"):
        controller.failed_mount = (
            controller.base if failure == "secret-mount" else controller.base / "runtime"
        )
    else:
        path = controller.base if failure == "secret-capacity" else controller.base / "runtime"
        controller.capacities[path] += 4096
    try:
        with pytest.raises(WitnessFailure):
            workspace.prepare()
        assert workspace.receipt["admitted"] is False
    finally:
        workspace.close()
    assert not controller.base.exists() and not controller.mounts
    if failure == "environment":
        assert controller.commands == [] and controller.core_limits == []
    if failure in {"environment", "host-swap", "secret-mount"}:
        assert not any(args[0] == "umount" for args in controller.commands)


def test_unadmitted_mount_identity_is_never_treated_as_cleanup_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, controller = _workspace(tmp_path, monkeypatch)
    controller.unadmitted_mount = controller.base
    with pytest.raises(WitnessFailure, match=r"^volatile-mount-identity$"):
        workspace.prepare()
    with pytest.raises(WitnessFailure, match=r"^volatile-cleanup-device$"):
        workspace.close()
    assert controller.base.exists() and controller.base in controller.mounts
    assert not any(args[0] == "umount" for args in controller.commands)


@pytest.mark.parametrize("zone", ["secret", "runtime"])
@pytest.mark.parametrize("drift", ["source", "device"])
def test_foreign_or_same_label_replacement_mount_cannot_be_unmounted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, zone: str, drift: str
) -> None:
    workspace, controller = _workspace(tmp_path, monkeypatch)
    workspace.prepare()
    target = controller.base if zone == "secret" else controller.base / "runtime"
    entry = _mount_entry(controller.mounts[target])
    entry["source" if drift == "source" else "maj:min"] = (
        "foreign" if drift == "source" else "0:999999"
    )
    with pytest.raises(WitnessFailure):
        workspace.close()
    assert target in controller.mounts and target.exists()
    assert not any(args[0] == "umount" and args[-1] == str(target) for args in controller.commands)
    assert workspace.receipt["cleanup"] != "complete"


@pytest.mark.parametrize("zone", ["secret", "runtime"])
def test_cleanup_preserves_nonempty_underlying_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, zone: str
) -> None:
    workspace, controller = _workspace(tmp_path, monkeypatch)
    workspace.prepare()
    target = controller.base if zone == "secret" else controller.base / "runtime"
    controller.underlay_file = target / "underlay.txt"
    with pytest.raises(OSError):
        workspace.close()
    assert controller.underlay_file.read_text() == "preserve underlay"
    assert workspace.receipt["cleanup"] != "complete"


def test_failed_unmount_is_not_reported_as_destroyed_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, controller = _workspace(tmp_path, monkeypatch)
    workspace.prepare()
    controller.refuse_unmount = controller.base / "runtime"
    with pytest.raises(WitnessFailure, match=r"^volatile-runtime-unmount$"):
        workspace.close()
    assert controller.base / "runtime" in controller.mounts
    assert not any(
        args[0] == "umount" and args[-1] == str(controller.base) for args in controller.commands
    )


@pytest.mark.parametrize(("zone", "mode"), [("secret", 0o400), ("runtime", 0o3775)])
def test_own_changes_only_a_real_owned_file_or_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, zone: str, mode: int
) -> None:
    workspace, controller = _workspace(tmp_path, monkeypatch)
    workspace.prepare()
    parent = controller.base if zone == "secret" else controller.base / "runtime"
    path = parent / "credential"
    if zone == "runtime":
        path.mkdir(mode=mode)
    else:
        path.write_text("fixture-only")
    workspace.own(path, uid=os.getuid(), gid=os.getgid(), mode=mode)
    assert stat.S_IMODE(path.stat().st_mode) == mode
    assert [args[0] for args in controller.commands[-2:]] == ["chown", "chmod"]
    assert all(args[-1] == str(path) for args in controller.commands[-2:])


@pytest.mark.parametrize(
    "kind",
    [
        "outside",
        "symlink",
        "parent-symlink",
        "fifo",
        "negative-uid",
        "negative-gid",
        "foreign-device",
    ],
)
def test_own_rejects_escape_alias_special_kind_and_foreign_filesystem_before_chown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    workspace, controller = _workspace(tmp_path, monkeypatch)
    workspace.prepare()
    path = controller.base / "credential"
    path.write_text("fixture-only")
    uid, gid = os.getuid(), os.getgid()
    if kind == "outside":
        path = controller.base.with_name("allocated-foreign")
        path.write_text("preserve")
    elif kind == "symlink":
        alias = path.with_name("alias")
        alias.symlink_to(path)
        path = alias
    elif kind == "parent-symlink":
        alias = controller.base / "alias"
        alias.symlink_to(controller.base, target_is_directory=True)
        path = alias / "credential"
    elif kind == "fifo":
        path.unlink()
        os.mkfifo(path)
    elif kind == "negative-uid":
        uid = -1
    elif kind == "negative-gid":
        gid = -1
    else:
        original = Path.stat

        def foreign_device(subject: Path, *, follow_symlinks: bool = True) -> os.stat_result:
            result = original(subject, follow_symlinks=follow_symlinks)
            if subject == path:
                fields = list(result)
                fields[2] = os.makedev(0, 999999)
                return os.stat_result(fields)
            return result

        monkeypatch.setattr(Path, "stat", foreign_device)
    previous = len(controller.commands)
    with pytest.raises(WitnessFailure):
        workspace.own(path, uid=uid, gid=gid, mode=0o400)
    assert len(controller.commands) == previous


def test_successful_chown_command_does_not_replace_permission_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, controller = _workspace(tmp_path, monkeypatch)
    workspace.prepare()
    path = controller.base / "credential"
    path.write_text("fixture-only")
    with pytest.raises(WitnessFailure, match=r"^volatile-owned-permissions$"):
        workspace.own(path, uid=os.getuid() + 1, gid=os.getgid(), mode=0o400)
    assert path.stat().st_uid == os.getuid()


@pytest.mark.parametrize(
    "result",
    [
        CommandResult(1, "", ""),
        CommandResult(0, "", "", error="failure"),
        CommandResult(None, "", "", failure_kind="timeout"),
    ],
)
def test_failed_control_result_never_admits_a_host_mutation(
    monkeypatch: pytest.MonkeyPatch, result: CommandResult
) -> None:
    monkeypatch.setattr(storage, "spawn", lambda *_args, **_kwargs: result)
    workspace = storage.VolatileWorkspace(_PROJECT, WitnessBudget(clock=lambda: 0.0))
    with pytest.raises(WitnessFailure, match=r"^volatile-host-control$"):
        workspace._control("mount", "--types", "tmpfs")


class _GenerationReached(RuntimeError):
    pass


@pytest.mark.parametrize("state", ["rejected", "missing-directory", "admitted"])
def test_ram_admission_precedes_every_credential_generation_or_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    events: list[str] = []

    class Workspace:
        directory = None if state == "missing-directory" else tmp_path

        def verify(self) -> None:
            events.append("verify")
            if state == "rejected":
                raise WitnessFailure("RAM-admission-denied")

    def generation(*_args: object, **_kwargs: object) -> NoReturn:
        events.append("generate")
        raise _GenerationReached

    monkeypatch.setattr(secrets, "token_urlsafe", generation)
    monkeypatch.setattr(secrets, "token_hex", _denied)
    monkeypatch.setattr(rsa, "generate_private_key", _denied)
    monkeypatch.setattr(ed25519.Ed25519PrivateKey, "generate", _denied)
    monkeypatch.setattr(fixture_owner, "write_private", _denied)
    if state == "admitted":
        with pytest.raises(_GenerationReached):
            fixture_owner.prepare_fixture(Workspace(), tmp_path)
        assert events == ["verify", "generate"]
    else:
        with pytest.raises((WitnessFailure, ValueError)):
            fixture_owner.prepare_fixture(Workspace(), tmp_path)
        assert events == ["verify"]
    assert list(tmp_path.iterdir()) == []
