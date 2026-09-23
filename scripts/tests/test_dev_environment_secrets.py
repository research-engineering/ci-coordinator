from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import cast

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from scripts.dev_environment.identity import InstanceIdentity, derive_instance_identity
from scripts.dev_environment.secrets import (
    InstanceStateError,
    admit_instance_state,
    ensure_instance_state,
)

_V1_FIXTURE_PATH = (
    Path(__file__).parents[2]
    / "fixtures"
    / "conformance"
    / "v1"
    / "developer-instance-state-v1.json"
)


def _identity(tmp_path: Path) -> InstanceIdentity:
    return derive_instance_identity(
        tmp_path,
        state_home=tmp_path.parent / f"{tmp_path.name}-user-state",
    )


def test_instance_state_is_private_complete_and_reused(tmp_path: Path) -> None:
    identity = _identity(tmp_path)

    initial = ensure_instance_state(identity)
    initial_secret_bytes = {
        path.name: path.read_bytes() for path in identity.secrets_directory.iterdir()
    }
    repeated = ensure_instance_state(identity)

    assert repeated == initial
    assert {
        path.name: path.read_bytes() for path in identity.secrets_directory.iterdir()
    } == initial_secret_bytes
    assert identity.environment_path.stat().st_mode & 0o777 == 0o600
    assert identity.metadata_path.stat().st_mode & 0o777 == 0o600
    assert identity.state_directory.stat().st_mode & 0o777 == 0o700
    assert identity.secrets_directory.stat().st_mode & 0o777 == 0o700
    assert all(
        path.stat().st_mode & 0o777 == 0o600 for path in identity.secrets_directory.iterdir()
    )
    assert initial["COMPOSE_PROJECT_NAME"] == identity.project_name
    assert initial["CI_COORDINATOR_DEV_ROOT_DIGEST"] == identity.root_digest
    assert initial["CI_COORDINATOR_DEV_SECRET_DIR"] == str(identity.secrets_directory)

    github_key = serialization.load_pem_private_key(
        (identity.secrets_directory / "github-private-key.pem").read_bytes(),
        password=None,
    )
    signing_key = serialization.load_pem_private_key(
        (identity.secrets_directory / "plan-signing-private-key.pem").read_bytes(),
        password=None,
    )
    assert isinstance(github_key, rsa.RSAPrivateKey)
    assert github_key.key_size == 2_048
    assert signing_key is not None


def test_v1_instance_state_migrates_without_replacing_existing_material(
    tmp_path: Path,
) -> None:
    identity = _identity(tmp_path)
    proxy_url = "http://proxy.example.test:3128"
    v1_environment, v1_secret_bytes = _materialize_v1_state(
        identity,
        outbound_proxy_url=proxy_url,
    )

    migrated = ensure_instance_state(identity, outbound_proxy_url=proxy_url)

    fixture = _v1_fixture()
    additions = cast(dict[str, str], fixture["v2EnvironmentAdditions"])
    assert {key: migrated[key] for key in additions} == additions
    assert migrated == _as_v3_environment({**v1_environment, **additions})
    current_secrets = _snapshot_secret_bytes(identity)
    current_secrets.pop("metrics-bearer-token")
    assert current_secrets == _as_v3_secret_bytes(v1_secret_bytes)
    assert (identity.secrets_directory / "metrics-bearer-token").read_text(encoding="utf-8").strip()
    assert json.loads(identity.metadata_path.read_text(encoding="utf-8"))["schemaVersion"] == 3

    migrated_secret_bytes = {
        path.name: path.read_bytes() for path in identity.secrets_directory.iterdir()
    }
    assert ensure_instance_state(identity, outbound_proxy_url=proxy_url) == migrated
    assert {
        path.name: path.read_bytes() for path in identity.secrets_directory.iterdir()
    } == migrated_secret_bytes


@pytest.mark.parametrize("environment_already_upgraded", [False, True])
def test_v1_instance_state_resumes_after_each_additive_crash_prefix(
    tmp_path: Path,
    environment_already_upgraded: bool,
) -> None:
    identity = _identity(tmp_path)
    _materialize_v1_state(identity, outbound_proxy_url=None)
    fixture = _v1_fixture()
    environment = _read_rendered_environment(identity.environment_path)
    expected_environment = {
        **environment,
        **cast(dict[str, str], fixture["v2EnvironmentAdditions"]),
    }
    if environment_already_upgraded:
        _write_rendered_environment(identity.environment_path, expected_environment)
    token_path = identity.secrets_directory / "metrics-bearer-token"
    token_path.write_text("m" * 64 + "\n", encoding="utf-8")
    os.chmod(token_path, 0o600)
    before = {path.name: path.read_bytes() for path in identity.secrets_directory.iterdir()}

    migrated = ensure_instance_state(identity)

    assert migrated == _as_v3_environment(expected_environment)
    assert _snapshot_secret_bytes(identity) == _as_v3_secret_bytes(before)
    assert json.loads(identity.metadata_path.read_text(encoding="utf-8"))["schemaVersion"] == 3


@pytest.mark.parametrize("material_shape", ["absent", "v1", "v2"])
def test_v1_creation_crash_prefix_without_environment_converges(
    tmp_path: Path,
    material_shape: str,
) -> None:
    identity = _identity(tmp_path)
    _materialize_v1_state(identity, outbound_proxy_url=None)
    identity.environment_path.unlink()
    if material_shape == "absent":
        shutil.rmtree(identity.secrets_directory)
    elif material_shape == "v2":
        token_path = identity.secrets_directory / "metrics-bearer-token"
        token_path.write_text("m" * 64 + "\n", encoding="utf-8")
        os.chmod(token_path, 0o600)
    preserved = (
        {}
        if material_shape == "absent"
        else {path.name: path.read_bytes() for path in identity.secrets_directory.iterdir()}
    )

    migrated = ensure_instance_state(identity)

    additions = cast(dict[str, str], _v1_fixture()["v2EnvironmentAdditions"])
    assert {key: migrated[key] for key in additions} == additions
    assert {
        name: content
        for name, content in _snapshot_secret_bytes(identity).items()
        if name in _as_v3_secret_bytes(preserved)
    } == _as_v3_secret_bytes(preserved)
    assert json.loads(identity.metadata_path.read_text(encoding="utf-8"))["schemaVersion"] == 3


def test_v1_migration_stages_new_secret_outside_closed_secret_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _identity(tmp_path)
    _materialize_v1_state(identity, outbound_proxy_url=None)
    original_replace = os.replace
    observed_directories: list[Path] = []

    def recording_replace(
        source: str | os.PathLike[str],
        destination: str | os.PathLike[str],
    ) -> None:
        if Path(destination) == identity.secrets_directory / "metrics-bearer-token":
            observed_directories.append(Path(source).parent)
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", recording_replace)

    ensure_instance_state(identity)

    assert observed_directories == [identity.state_directory]


@pytest.mark.parametrize(
    ("environment_migrated", "credential_renamed"),
    [(False, False), (True, False), (True, True)],
)
def test_v2_control_plane_cutover_preserves_credential_and_resumes_exact_prefixes(
    tmp_path: Path,
    environment_migrated: bool,
    credential_renamed: bool,
) -> None:
    identity = _identity(tmp_path)
    previous_environment, previous_secrets = _materialize_v2_state(identity)
    if environment_migrated:
        _write_rendered_environment(
            identity.environment_path,
            _as_v3_environment(previous_environment),
        )
    if credential_renamed:
        (identity.secrets_directory / "operator-bearer-token").replace(
            identity.secrets_directory / "break-glass-bearer-token"
        )

    migrated = ensure_instance_state(identity)

    assert migrated == _as_v3_environment(previous_environment)
    assert _snapshot_secret_bytes(identity) == _as_v3_secret_bytes(previous_secrets)
    assert json.loads(identity.metadata_path.read_text(encoding="utf-8"))["schemaVersion"] == 3


def test_v2_control_plane_cutover_rejects_an_unreachable_write_order(
    tmp_path: Path,
) -> None:
    identity = _identity(tmp_path)
    _materialize_v2_state(identity)
    (identity.secrets_directory / "operator-bearer-token").replace(
        identity.secrets_directory / "break-glass-bearer-token"
    )
    before = _snapshot_state_files(identity)

    with pytest.raises(InstanceStateError, match="prefix"):
        ensure_instance_state(identity)

    assert _snapshot_state_files(identity) == before


@pytest.mark.parametrize(
    "mutated_environment_key",
    [
        "CI_COORDINATOR_DATABASE_POOL_SIZE",
        "CI_COORDINATOR_DATABASE_POOL_TIMEOUT_SECONDS",
        "CI_COORDINATOR_MAXIMUM_RETAINED_BODY_BYTES",
    ],
)
def test_v1_migration_rejects_mutated_v2_defaults_without_writing(
    tmp_path: Path,
    mutated_environment_key: str,
) -> None:
    identity = _identity(tmp_path)
    _materialize_resumable_v2_components(identity)
    environment = _read_rendered_environment(identity.environment_path)
    environment[mutated_environment_key] = "invalid"
    _write_rendered_environment(identity.environment_path, environment)
    before = _snapshot_state_files(identity)

    with pytest.raises(InstanceStateError, match="defaults"):
        ensure_instance_state(identity)

    assert _snapshot_state_files(identity) == before


def test_v1_migration_rejects_invalid_existing_metrics_token_without_writing(
    tmp_path: Path,
) -> None:
    identity = _identity(tmp_path)
    _materialize_resumable_v2_components(identity)
    token_path = identity.secrets_directory / "metrics-bearer-token"
    token_path.write_text("\n", encoding="utf-8")
    os.chmod(token_path, 0o600)
    before = _snapshot_state_files(identity)

    with pytest.raises(InstanceStateError, match="metrics bearer token"):
        ensure_instance_state(identity)

    assert _snapshot_state_files(identity) == before


def test_invalid_v1_instance_state_is_rejected_without_mutation(tmp_path: Path) -> None:
    identity = _identity(tmp_path)
    _materialize_v1_state(identity, outbound_proxy_url=None)
    with identity.environment_path.open("a", encoding="utf-8") as environment_file:
        environment_file.write('UNKNOWN_SETTING="value"\n')
    before = _snapshot_state_files(identity)

    with pytest.raises(InstanceStateError, match="key set"):
        ensure_instance_state(identity)

    assert _snapshot_state_files(identity) == before


def test_future_instance_state_schema_is_rejected_without_mutation(
    tmp_path: Path,
) -> None:
    identity = _identity(tmp_path)
    ensure_instance_state(identity)
    metadata = json.loads(identity.metadata_path.read_text(encoding="utf-8"))
    metadata["schemaVersion"] = 4
    identity.metadata_path.write_text(
        json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.chmod(identity.metadata_path, 0o600)
    before = _snapshot_state_files(identity)

    with pytest.raises(InstanceStateError, match="collision"):
        ensure_instance_state(identity)

    assert _snapshot_state_files(identity) == before


def test_environment_contains_paths_but_no_secret_literals(tmp_path: Path) -> None:
    identity = _identity(tmp_path)
    environment = ensure_instance_state(identity)
    rendered = identity.environment_path.read_text(encoding="utf-8")

    for secret_path in identity.secrets_directory.iterdir():
        assert secret_path.read_text(encoding="utf-8").strip() not in rendered
    assert not any(
        key.endswith(("PASSWORD", "PRIVATE_KEY", "BEARER_TOKEN", "DATABASE_DSN"))
        for key in environment
    )


def test_instance_state_persists_one_canonical_optional_proxy(tmp_path: Path) -> None:
    identity = _identity(tmp_path)

    environment = ensure_instance_state(
        identity,
        outbound_proxy_url="http://proxy.example.test:3128/",
    )

    assert environment["CI_COORDINATOR_OUTBOUND_PROXY_URL"] == ("http://proxy.example.test:3128")
    assert (
        admit_instance_state(
            identity,
            outbound_proxy_url="http://proxy.example.test:3128/",
        )
        == environment
    )
    assert ensure_instance_state(identity) == environment


def test_instance_state_rejects_proxy_drift(tmp_path: Path) -> None:
    identity = _identity(tmp_path)
    ensure_instance_state(identity)

    with pytest.raises(InstanceStateError, match="does not match"):
        ensure_instance_state(
            identity,
            outbound_proxy_url="http://proxy.example.test:3128",
        )


@pytest.mark.parametrize(
    "proxy_url",
    ["", "https://proxy.example.test", "http://user@proxy.example.test"],
)
def test_instance_state_rejects_invalid_proxy_input(
    tmp_path: Path,
    proxy_url: str,
) -> None:
    with pytest.raises(InstanceStateError, match="proxy is invalid"):
        ensure_instance_state(_identity(tmp_path), outbound_proxy_url=proxy_url)


def test_instance_state_rejects_permissive_secret_mode(tmp_path: Path) -> None:
    identity = _identity(tmp_path)
    ensure_instance_state(identity)
    secret = identity.secrets_directory / "break-glass-bearer-token"
    os.chmod(secret, 0o640)

    with pytest.raises(InstanceStateError, match="unsafe"):
        admit_instance_state(identity)


def test_instance_state_rejects_secret_symlink(tmp_path: Path) -> None:
    identity = _identity(tmp_path)
    ensure_instance_state(identity)
    secret = identity.secrets_directory / "break-glass-bearer-token"
    secret.unlink()
    secret.symlink_to(identity.metadata_path)

    with pytest.raises(InstanceStateError, match="unsafe"):
        admit_instance_state(identity)


def test_instance_state_rejects_root_collision(tmp_path: Path) -> None:
    identity = _identity(tmp_path)
    ensure_instance_state(identity)
    metadata = json.loads(identity.metadata_path.read_text(encoding="utf-8"))
    metadata["repositoryRoot"] = "/foreign/worktree"
    identity.metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    os.chmod(identity.metadata_path, 0o600)

    with pytest.raises(InstanceStateError, match="does not match"):
        admit_instance_state(identity)


def test_instance_state_rejects_partial_secret_set(tmp_path: Path) -> None:
    identity = _identity(tmp_path)
    ensure_instance_state(identity)
    (identity.secrets_directory / "webhook-secret").unlink()

    with pytest.raises(InstanceStateError, match="file set"):
        ensure_instance_state(identity)


def _materialize_v1_state(
    identity: InstanceIdentity,
    *,
    outbound_proxy_url: str | None,
) -> tuple[dict[str, str], dict[str, bytes]]:
    current = _as_v2_environment(
        dict(ensure_instance_state(identity, outbound_proxy_url=outbound_proxy_url))
    )
    fixture = _v1_fixture()
    required_keys = cast(list[str], fixture["requiredEnvironmentKeys"])
    optional_keys = cast(list[str], fixture["optionalEnvironmentKeys"])
    admitted_keys = set(required_keys) | set(optional_keys)
    v1_environment = {key: value for key, value in current.items() if key in admitted_keys}
    assert set(v1_environment) == set(required_keys) | (
        {"CI_COORDINATOR_OUTBOUND_PROXY_URL"} if outbound_proxy_url is not None else set()
    )
    _write_rendered_environment(identity.environment_path, v1_environment)

    (identity.secrets_directory / "break-glass-bearer-token").replace(
        identity.secrets_directory / "operator-bearer-token"
    )
    (identity.secrets_directory / "metrics-bearer-token").unlink()
    expected_secret_names = set(cast(list[str], fixture["secretFilenames"]))
    v1_secret_bytes = {
        path.name: path.read_bytes() for path in identity.secrets_directory.iterdir()
    }
    assert set(v1_secret_bytes) == expected_secret_names

    metadata = json.loads(identity.metadata_path.read_text(encoding="utf-8"))
    metadata["schemaVersion"] = fixture["schemaVersion"]
    identity.metadata_path.write_text(
        json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.chmod(identity.metadata_path, 0o600)
    return v1_environment, v1_secret_bytes


def _materialize_resumable_v2_components(identity: InstanceIdentity) -> None:
    _materialize_v1_state(identity, outbound_proxy_url=None)
    environment = _read_rendered_environment(identity.environment_path)
    environment.update(cast(dict[str, str], _v1_fixture()["v2EnvironmentAdditions"]))
    _write_rendered_environment(identity.environment_path, environment)
    token_path = identity.secrets_directory / "metrics-bearer-token"
    token_path.write_text("m" * 64 + "\n", encoding="utf-8")
    os.chmod(token_path, 0o600)


def _materialize_v2_state(
    identity: InstanceIdentity,
) -> tuple[dict[str, str], dict[str, bytes]]:
    current = dict(ensure_instance_state(identity))
    previous_environment = _as_v2_environment(current)
    _write_rendered_environment(identity.environment_path, previous_environment)
    (identity.secrets_directory / "break-glass-bearer-token").replace(
        identity.secrets_directory / "operator-bearer-token"
    )
    metadata = json.loads(identity.metadata_path.read_text(encoding="utf-8"))
    metadata["schemaVersion"] = 2
    identity.metadata_path.write_text(
        json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.chmod(identity.metadata_path, 0o600)
    return previous_environment, _snapshot_secret_bytes(identity)


def _v1_fixture() -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads(_V1_FIXTURE_PATH.read_text(encoding="utf-8")),
    )


def _read_rendered_environment(path: Path) -> dict[str, str]:
    environment: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        assert separator
        decoded = json.loads(value)
        assert isinstance(decoded, str)
        environment[key] = decoded
    return environment


def _write_rendered_environment(path: Path, environment: dict[str, str]) -> None:
    path.write_text(
        "".join(f"{key}={json.dumps(environment[key])}\n" for key in sorted(environment)),
        encoding="utf-8",
    )
    os.chmod(path, 0o600)


def _snapshot_state_files(identity: InstanceIdentity) -> dict[str, bytes]:
    return {
        str(path.relative_to(identity.state_directory)): path.read_bytes()
        for path in identity.state_directory.rglob("*")
        if path.is_file() and path.name != "lifecycle.lock"
    }


def _snapshot_secret_bytes(identity: InstanceIdentity) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in identity.secrets_directory.iterdir()}


def _as_v3_environment(environment: dict[str, str]) -> dict[str, str]:
    migrated = dict(environment)
    migrated["CI_COORDINATOR_BREAK_GLASS_ACTOR_ID"] = "break-glass:v1:local-development"
    migrated["CI_COORDINATOR_CONTROL_PLANE_SCOPE_ALLOWLIST"] = migrated.pop(
        "CI_COORDINATOR_OPERATOR_SCOPE_ALLOWLIST"
    )
    migrated.pop("CI_COORDINATOR_OPERATOR_ID")
    return migrated


def _as_v2_environment(environment: dict[str, str]) -> dict[str, str]:
    previous = dict(environment)
    previous["CI_COORDINATOR_OPERATOR_ID"] = "operator:local-development"
    previous["CI_COORDINATOR_OPERATOR_SCOPE_ALLOWLIST"] = previous.pop(
        "CI_COORDINATOR_CONTROL_PLANE_SCOPE_ALLOWLIST"
    )
    previous.pop("CI_COORDINATOR_BREAK_GLASS_ACTOR_ID")
    return previous


def _as_v3_secret_bytes(secret_bytes: dict[str, bytes]) -> dict[str, bytes]:
    migrated = dict(secret_bytes)
    if "operator-bearer-token" in migrated:
        migrated["break-glass-bearer-token"] = migrated.pop("operator-bearer-token")
    return migrated
