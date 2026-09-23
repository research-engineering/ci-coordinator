from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path, PurePosixPath

import pytest
from ruamel.yaml import YAML
from scripts.ci_business_witness import consumers
from scripts.ci_business_witness.lifecycle import WitnessBudget
from scripts.ci_business_witness.oracle import WitnessFailure
from scripts.ci_business_witness.storage import VolatileWorkspace, admit_container_storage

_ROOT = Path(__file__).resolve().parents[2]
_PROJECT = "ci-business-" + "a" * 24
_USERS = {
    "postgres": "",
    "database-provision": "",
    "migrate": "10001:10001",
    "keycloak": "1000",
    "proxy": "",
    "backend": "10001:10001",
    "browser": "",
}


def _compose_mounts(service: str, directory: Path) -> dict[str, tuple[Path, bool]]:
    compose = _ROOT / "docker/ci/connected-administrator.compose.yaml"
    specification = YAML(typ="safe").load(compose)["services"][service]
    mounts = {}
    for volume in specification["volumes"]:
        if isinstance(volume, str):
            source, target, *options = volume.split(":")
            assert options in ([], ["ro"])
            writable = not options
        else:
            assert volume["type"] == "bind"
            assert volume["bind"]["create_host_path"] is False
            source, target = volume["source"], volume["target"]
            writable = not volume.get("read_only", False)
        source_path = Path(source.replace("${BUSINESS_FIXTURE}", str(directory)))
        if not source_path.is_absolute():
            source_path = compose.parent / source_path
        assert target not in mounts
        mounts[target] = (source_path.resolve(), writable)
    return mounts


def _inspect(service: str, mounts: dict[str, tuple[Path, bool]]) -> dict[str, object]:
    return {
        "Id": "a" * 64,
        "Image": "sha256:" + "b" * 64,
        "HostConfig": {
            "ReadonlyRootfs": True,
            "Privileged": False,
            "LogConfig": {"Type": "none", "Config": {}},
            "Ulimits": [{"Name": "core", "Soft": 0, "Hard": 0}],
        },
        "Config": {
            "User": _USERS[service],
            "Labels": {"com.docker.compose.project": _PROJECT},
            "Healthcheck": {"Test": ["NONE"]},
        },
        "Mounts": [
            {"Type": "bind", "Source": str(source), "Destination": target, "RW": writable}
            for target, (source, writable) in mounts.items()
        ],
    }


def _admit(service: str, directory: Path, mounts: dict[str, tuple[Path, bool]]) -> None:
    admit_container_storage(
        _inspect(service, mounts),
        directory=directory,
        project=_PROJECT,
        expected_id="a" * 64,
        expected_image="sha256:" + "b" * 64,
        expected_user=consumers.expected_user(service),
        expected_mounts=consumers.expected_mounts(service, directory, _ROOT),
    )


def test_compose_consumer_population_and_volatile_root_policy_are_closed() -> None:
    services = YAML(typ="safe").load(_ROOT / "docker/ci/connected-administrator.compose.yaml")[
        "services"
    ]
    assert set(services) == _USERS.keys()
    for specification in services.values():
        assert specification["read_only"] is True
        assert specification["privileged"] is False
        assert specification["logging"] == {"driver": "none"}
        assert specification["healthcheck"] == {"disable": True}
        assert specification["ulimits"]["core"] == {"soft": 0, "hard": 0}


@pytest.mark.parametrize("service", _USERS)
def test_owned_compose_mounts_match_the_complete_consumer_contract(
    tmp_path: Path, service: str
) -> None:
    directory = tmp_path.resolve()
    native_mounts = _compose_mounts(service, directory)
    assert consumers.expected_mounts(service, directory, _ROOT) == native_mounts
    assert consumers.expected_user(service) == _USERS[service]
    _admit(service, directory, native_mounts)
    assert all(source != directory for source, _ in native_mounts.values())
    assert all(
        not writable
        for source, writable in native_mounts.values()
        if source.parent == directory and source.name != "evidence"
    )


@pytest.mark.parametrize("service", _USERS)
@pytest.mark.parametrize("change", ["extra", "missing", "source", "writable"])
def test_admission_rejects_consumer_mount_population_or_access_drift(
    tmp_path: Path, service: str, change: str
) -> None:
    directory = tmp_path.resolve()
    mounts = _compose_mounts(service, directory)
    target = next(iter(mounts))
    source, writable = mounts[target]
    if change == "extra":
        mounts["/whole-fixture"] = (directory, False)
    elif change == "missing":
        del mounts[target]
    elif change == "source":
        mounts[target] = (directory / "unrelated-secret", writable)
    else:
        mounts[target] = (source, not writable)
    with pytest.raises(WitnessFailure, match=r"volatile-container-(mounts|persistent-mount)"):
        _admit(service, directory, mounts)


def test_readiness_has_only_the_public_ca_and_its_own_volatile_tmp(tmp_path: Path) -> None:
    assert consumers.expected_mounts("readiness", tmp_path, _ROOT) == {
        "/fixture/ca.pem": (tmp_path / "ca.pem", False),
        str(PurePosixPath("/") / "tmp"): (tmp_path / "runtime/readiness-tmp", True),
    }
    assert consumers.expected_user("readiness") == ""
    for unknown in ("", "foreign", "../backend"):
        with pytest.raises(WitnessFailure, match="volatile-consumer-service"):
            consumers.expected_user(unknown)
        with pytest.raises(WitnessFailure, match="volatile-consumer-service"):
            consumers.expected_mounts(unknown, tmp_path, _ROOT)


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> VolatileWorkspace:
    workspace = VolatileWorkspace(_PROJECT, WitnessBudget())
    workspace.directory = tmp_path.resolve()
    workspace.runtime_directory = workspace.directory / "runtime"
    workspace.runtime_directory.mkdir()
    workspace.receipt["admitted"] = True
    monkeypatch.setattr(workspace, "verify", lambda: None)
    return workspace


def test_runtime_layout_is_created_before_deepest_first_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path, monkeypatch)
    directory = tmp_path.resolve()
    expected = {
        "postgres": (999, 999, 0o700),
        "postgres-run": (999, 999, 0o3775),
        "database-provision-postgres": (0, 0, 0o700),
        "keycloak": (1000, 0, 0o700),
        "keycloak/import": (1000, 0, 0o700),
        "keycloak-quarkus": (os.getuid(), os.getgid(), 0o700),
        "proxy-cache": (101, 101, 0o700),
        "proxy-run": (0, 0, 0o700),
        "postgres-tmp": (999, 999, 0o1777),
        "database-provision-tmp": (0, 0, 0o1777),
        "migrate-tmp": (10001, 10001, 0o1777),
        "keycloak-tmp": (1000, 0, 0o1777),
        "proxy-tmp": (0, 0, 0o1777),
        "backend-tmp": (10001, 10001, 0o1777),
        "browser-tmp": (0, 0, 0o1777),
        "readiness-tmp": (0, 0, 0o1777),
    }
    observed = {}
    verified = False

    def verify() -> None:
        nonlocal verified
        assert not list((directory / "runtime").iterdir())
        verified = True

    def own(path: Path, *, uid: int, gid: int, mode: int) -> None:
        assert verified
        assert all((directory / "runtime" / name).is_dir() for name in expected)
        observed[str(path.relative_to(directory / "runtime"))] = (uid, gid, mode)

    monkeypatch.setattr(workspace, "verify", verify)
    monkeypatch.setattr(workspace, "own", own)
    consumers.prepare_runtime_directories(workspace)
    assert observed == expected
    assert list(observed).index("keycloak/import") < list(observed).index("keycloak")
    assert list((directory / "runtime/keycloak-quarkus").iterdir()) == []


@pytest.mark.parametrize(
    "helper", [consumers.prepare_runtime_directories, consumers.apply_fixture_ownership]
)
@pytest.mark.parametrize(
    "defect", ["unverified", "unadmitted", "directory-absent", "runtime-absent", "runtime-foreign"]
)
def test_no_consumer_mutation_precedes_full_workspace_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    helper: Callable[[VolatileWorkspace], None],
    defect: str,
) -> None:
    workspace = _workspace(tmp_path, monkeypatch)
    owned = []
    monkeypatch.setattr(workspace, "own", lambda *args, **kwargs: owned.append(args))
    if defect == "unverified":

        def reject() -> None:
            raise WitnessFailure("volatile-test-unverified")

        monkeypatch.setattr(workspace, "verify", reject)
    elif defect == "unadmitted":
        workspace.receipt["admitted"] = False
    elif defect == "directory-absent":
        workspace.directory = None
    elif defect == "runtime-absent":
        workspace.runtime_directory = None
    else:
        workspace.runtime_directory = tmp_path / "foreign"
    with pytest.raises(WitnessFailure):
        helper(workspace)
    assert owned == []
    assert list((tmp_path / "runtime").iterdir()) == []


def test_fixture_ownership_preserves_entrypoint_reads_and_checkpoint_producer_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path, monkeypatch)
    directory = tmp_path.resolve()
    app_files = {
        "runtime-dsn",
        "migration-dsn",
        "github-key.pem",
        "signing-key.pem",
        "webhook-secret",
        "metrics-token",
        "break-glass-token",
        "browser-secret",
        "reviewer-secret",
        "session-key",
    }
    expected = {
        **dict.fromkeys(app_files, (10001, 10001, 0o400)),
        "realm.json": (1000, 0, 0o400),
        "tls-key.pem": (0, 0, 0o400),
        "browser.json": (0, 0, 0o400),
        "postgres-superuser-password": (999, 999, 0o400),
        "postgres-migration-password": (0, 0, 0o400),
        "postgres-runtime-password": (0, 0, 0o400),
        **dict.fromkeys(
            ("ca.pem", "tls.pem", "ca-bundle.pem", "backend.env"), (os.getuid(), os.getgid(), 0o444)
        ),
    }
    for name in expected:
        (directory / name).write_bytes(b"fixture")
    evidence = directory / "evidence"
    evidence.mkdir(mode=0o700)
    acknowledgement = evidence / "authenticated.ack"
    acknowledgement.write_text("accepted")
    acknowledgement.chmod(0o400)
    original = evidence.stat(), acknowledgement.stat()
    observed = {}
    verified = False

    def verify() -> None:
        nonlocal verified
        verified = True

    def own(path: Path, *, uid: int, gid: int, mode: int) -> None:
        assert verified and path.is_file()
        observed[path.name] = (uid, gid, mode)

    monkeypatch.setattr(workspace, "verify", verify)
    monkeypatch.setattr(workspace, "own", own)
    consumers.apply_fixture_ownership(workspace)
    assert observed == expected
    assert (evidence.stat(), acknowledgement.stat()) == original
    assert not {"password", "log-canary", "evidence"} & observed.keys()


@pytest.mark.parametrize("defect", ["group-readable", "public", "symlink", "absent"])
def test_shared_evidence_admission_requires_the_private_producer_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    workspace = _workspace(tmp_path, monkeypatch)
    evidence = tmp_path / "evidence"
    if defect == "symlink":
        target = tmp_path / "other"
        target.mkdir(mode=0o700)
        evidence.symlink_to(target, target_is_directory=True)
    elif defect != "absent":
        evidence.mkdir(mode=0o700)
        evidence.chmod(0o750 if defect == "group-readable" else 0o777)
    owned: list[object] = []
    monkeypatch.setattr(workspace, "own", lambda *args, **kwargs: owned.append(args))
    with pytest.raises(WitnessFailure, match="volatile-evidence-"):
        consumers.apply_fixture_ownership(workspace)
    assert owned == []
