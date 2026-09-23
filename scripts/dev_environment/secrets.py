"""Atomic creation and admission of local-only instance material."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from ci_coordinator.runtime_settings import (
    is_break_glass_bearer_token,
    is_metrics_bearer_token,
    normalize_outbound_proxy_url,
)
from scripts.dev_environment.identity import InstanceIdentity
from scripts.dev_environment.private_files import (
    PrivateFileError,
    atomic_write_private_text,
    ensure_private_directory,
    exclusive_private_lock,
    read_private_text,
    require_private_directory,
    require_private_file,
)

_V2_REQUIRED_ENVIRONMENT_KEYS: Final = frozenset(
    {
        "CI_COORDINATOR_BIND_HOST",
        "CI_COORDINATOR_BIND_PORT",
        "CI_COORDINATOR_DEV_API_URL",
        "CI_COORDINATOR_DEV_ENV_FILE",
        "CI_COORDINATOR_DEV_ROOT_DIGEST",
        "CI_COORDINATOR_DEV_SECRET_DIR",
        "CI_COORDINATOR_DATABASE_POOL_SIZE",
        "CI_COORDINATOR_DATABASE_POOL_TIMEOUT_SECONDS",
        "CI_COORDINATOR_GITHUB_APP_ID",
        "CI_COORDINATOR_MAXIMUM_RETAINED_BODY_BYTES",
        "CI_COORDINATOR_OIDC_ALLOWED_JOB_WORKFLOW_REFS",
        "CI_COORDINATOR_OIDC_ALLOWED_WORKFLOW_REFS",
        "CI_COORDINATOR_OIDC_AUDIENCE",
        "CI_COORDINATOR_OPERATOR_ID",
        "CI_COORDINATOR_OPERATOR_SCOPE_ALLOWLIST",
        "CI_COORDINATOR_PLAN_SIGNING_KEY_ID",
        "CI_COORDINATOR_PLAN_TTL_SECONDS",
        "CI_COORDINATOR_RECONCILIATION_INTERVAL_SECONDS",
        "CI_COORDINATOR_RECONCILIATION_SCAN_LIMIT",
        "CI_COORDINATOR_RECONCILIATION_STARTUP_TIMEOUT_SECONDS",
        "CI_COORDINATOR_REQUEST_TIMEOUT_SECONDS",
        "CI_COORDINATOR_RUNTIME_MODE",
        "CI_COORDINATOR_SHADOW_ROLLOUT_PROFILE_ID",
        "CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS",
        "COMPOSE_PROJECT_NAME",
    }
)
_OPTIONAL_ENVIRONMENT_KEYS: Final = frozenset(
    {
        "CI_COORDINATOR_OUTBOUND_PROXY_URL",
        "CI_COORDINATOR_CONTROL_PLANE_INVENTORY_MODE",
    }
)
_V2_ENVIRONMENT_RENAMES: Final = {
    "CI_COORDINATOR_OPERATOR_ID": "CI_COORDINATOR_BREAK_GLASS_ACTOR_ID",
    "CI_COORDINATOR_OPERATOR_SCOPE_ALLOWLIST": "CI_COORDINATOR_CONTROL_PLANE_SCOPE_ALLOWLIST",
}
_REQUIRED_ENVIRONMENT_KEYS: Final = frozenset(
    (_V2_REQUIRED_ENVIRONMENT_KEYS - _V2_ENVIRONMENT_RENAMES.keys())
    | frozenset(_V2_ENVIRONMENT_RENAMES.values())
)
_V2_ENVIRONMENT_ADDITIONS: Final = (
    ("CI_COORDINATOR_DATABASE_POOL_SIZE", "16"),
    ("CI_COORDINATOR_DATABASE_POOL_TIMEOUT_SECONDS", "5"),
    ("CI_COORDINATOR_MAXIMUM_RETAINED_BODY_BYTES", "33554432"),
)
_V1_REQUIRED_ENVIRONMENT_KEYS: Final = _V2_REQUIRED_ENVIRONMENT_KEYS - frozenset(
    key for key, _value in _V2_ENVIRONMENT_ADDITIONS
)
_V2_SECRET_FILENAMES: Final = frozenset(
    {
        "github-private-key.pem",
        "migration-dsn",
        "metrics-bearer-token",
        "operator-bearer-token",
        "plan-signing-private-key.pem",
        "postgres-migration-password",
        "postgres-runtime-password",
        "postgres-superuser-password",
        "runtime-dsn",
        "webhook-secret",
    }
)
_SECRET_FILENAMES: Final = frozenset(
    (_V2_SECRET_FILENAMES - {"operator-bearer-token"}) | {"break-glass-bearer-token"}
)
_V1_SECRET_FILENAMES: Final = _V2_SECRET_FILENAMES - {"metrics-bearer-token"}
_METADATA_SCHEMA_VERSION: Final = 3
_V2_METADATA_SCHEMA_VERSION: Final = 2
_V1_METADATA_SCHEMA_VERSION: Final = 1


class InstanceStateError(ValueError):
    """Local state is incomplete, unsafe, or belongs to another root."""


class ForeignInstanceStateError(InstanceStateError):
    pass


def ensure_instance_state(
    identity: InstanceIdentity,
    *,
    outbound_proxy_url: str | None = None,
) -> Mapping[str, str]:
    normalized_proxy_url = _normalize_proxy_input(outbound_proxy_url)
    try:
        ensure_private_directory(identity.state_home)
        ensure_private_directory(identity.state_home / "instances")
        ensure_private_directory(identity.state_directory)
        with exclusive_private_lock(identity.state_directory / "lifecycle.lock"):
            if identity.metadata_path.exists():
                metadata_version = _admit_metadata(identity)
                if metadata_version == _V1_METADATA_SCHEMA_VERSION:
                    _migrate_or_resume_v1_state(
                        identity,
                        outbound_proxy_url=normalized_proxy_url,
                    )
                if metadata_version in {
                    _V1_METADATA_SCHEMA_VERSION,
                    _V2_METADATA_SCHEMA_VERSION,
                }:
                    _migrate_or_resume_v2_state(
                        identity,
                        outbound_proxy_url=normalized_proxy_url,
                    )
            else:
                _write_metadata(identity, _METADATA_SCHEMA_VERSION)
            _admit_or_create_current_material(
                identity,
                outbound_proxy_url=normalized_proxy_url,
            )
            return admit_instance_state(
                identity,
                outbound_proxy_url=normalized_proxy_url,
            )
    except PrivateFileError as error:
        raise InstanceStateError(str(error)) from error


def admit_instance_state(
    identity: InstanceIdentity,
    *,
    outbound_proxy_url: str | None = None,
) -> Mapping[str, str]:
    normalized_proxy_url = _normalize_proxy_input(outbound_proxy_url)
    try:
        require_private_directory(identity.state_home)
        require_private_directory(identity.state_home / "instances")
        require_private_directory(identity.state_directory)
        metadata = _read_json(identity.metadata_path)
        if metadata != _metadata(identity, _METADATA_SCHEMA_VERSION):
            if _declares_foreign_identity(identity, metadata):
                raise ForeignInstanceStateError(
                    "instance metadata does not match the repository root"
                )
            raise InstanceStateError("instance metadata does not match the repository root")
        environment = _read_environment(identity.environment_path)
        _admit_secret_directory(identity)
    except PrivateFileError as error:
        raise InstanceStateError(str(error)) from error
    _admit_environment(
        identity,
        environment,
        required_keys=_REQUIRED_ENVIRONMENT_KEYS,
        outbound_proxy_url=normalized_proxy_url,
    )
    return environment


def _admit_or_create_current_material(
    identity: InstanceIdentity,
    *,
    outbound_proxy_url: str | None,
) -> None:
    _admit_or_create_secrets(identity)
    if not identity.environment_path.exists():
        atomic_write_private_text(
            identity.environment_path,
            _render_environment(_new_environment(identity, outbound_proxy_url=outbound_proxy_url)),
        )


def _admit_environment(
    identity: InstanceIdentity,
    environment: Mapping[str, str],
    *,
    required_keys: frozenset[str],
    outbound_proxy_url: str | None,
) -> None:
    environment_keys = frozenset(environment)
    if not required_keys.issubset(environment_keys) or not environment_keys.issubset(
        required_keys | _OPTIONAL_ENVIRONMENT_KEYS
    ):
        raise InstanceStateError("instance environment key set is invalid")
    expected_bindings = {
        "COMPOSE_PROJECT_NAME": identity.project_name,
        "CI_COORDINATOR_DEV_ROOT_DIGEST": identity.root_digest,
        "CI_COORDINATOR_DEV_ENV_FILE": str(identity.environment_path),
        "CI_COORDINATOR_DEV_SECRET_DIR": str(identity.secrets_directory),
    }
    if any(environment.get(key) != value for key, value in expected_bindings.items()):
        raise ForeignInstanceStateError("instance environment identity is invalid")
    stored_proxy_url = environment.get("CI_COORDINATOR_OUTBOUND_PROXY_URL")
    if (
        stored_proxy_url is not None
        and _normalize_proxy_input(stored_proxy_url) != stored_proxy_url
    ):
        raise InstanceStateError("instance outbound proxy is not canonical")
    if outbound_proxy_url is not None and outbound_proxy_url != stored_proxy_url:
        raise InstanceStateError("instance outbound proxy does not match requested configuration")


def _admit_metadata(identity: InstanceIdentity) -> int:
    metadata = _read_json(identity.metadata_path)
    for version in (
        _V1_METADATA_SCHEMA_VERSION,
        _V2_METADATA_SCHEMA_VERSION,
        _METADATA_SCHEMA_VERSION,
    ):
        if metadata == _metadata(identity, version):
            return version
    if _declares_foreign_identity(identity, metadata):
        raise ForeignInstanceStateError("instance identity collision detected")
    raise InstanceStateError("instance identity collision detected")


def _declares_foreign_identity(identity: InstanceIdentity, metadata: object) -> bool:
    if not isinstance(metadata, dict):
        return False
    expected = _metadata(identity, _METADATA_SCHEMA_VERSION)
    keys = ("projectName", "repositoryRoot", "rootDigest")
    return all(isinstance(metadata.get(key), str) and metadata[key] for key in keys) and any(
        metadata[key] != expected[key] for key in keys
    )


def _write_metadata(identity: InstanceIdentity, schema_version: int) -> None:
    atomic_write_private_text(
        identity.metadata_path,
        json.dumps(
            _metadata(identity, schema_version),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
    )


def _metadata(identity: InstanceIdentity, schema_version: int) -> dict[str, object]:
    return {
        "projectName": identity.project_name,
        "repositoryRoot": str(identity.repo_root),
        "rootDigest": identity.root_digest,
        "schemaVersion": schema_version,
    }


def _migrate_v1_state(
    identity: InstanceIdentity,
    *,
    outbound_proxy_url: str | None,
) -> None:
    environment = _read_environment(identity.environment_path)
    environment_keys = frozenset(environment)
    if environment_keys.issubset(_V1_REQUIRED_ENVIRONMENT_KEYS | _OPTIONAL_ENVIRONMENT_KEYS):
        required_keys = _V1_REQUIRED_ENVIRONMENT_KEYS
    else:
        required_keys = _V2_REQUIRED_ENVIRONMENT_KEYS
    _admit_environment(
        identity,
        environment,
        required_keys=required_keys,
        outbound_proxy_url=outbound_proxy_url,
    )
    if required_keys == _V2_REQUIRED_ENVIRONMENT_KEYS:
        _admit_v2_environment_additions(environment)
    secret_filenames = _admit_migratable_secret_directory(identity)
    if secret_filenames == _V2_SECRET_FILENAMES:
        _admit_metrics_bearer_token(identity)

    if secret_filenames == _V1_SECRET_FILENAMES:
        _publish_metrics_bearer_token(identity)
    if required_keys == _V1_REQUIRED_ENVIRONMENT_KEYS:
        environment.update(_V2_ENVIRONMENT_ADDITIONS)
        atomic_write_private_text(
            identity.environment_path,
            _render_environment(environment),
        )
    _write_metadata(identity, _V2_METADATA_SCHEMA_VERSION)


def _migrate_or_resume_v1_state(
    identity: InstanceIdentity,
    *,
    outbound_proxy_url: str | None,
) -> None:
    if identity.environment_path.exists() or identity.environment_path.is_symlink():
        _migrate_v1_state(identity, outbound_proxy_url=outbound_proxy_url)
        return
    _resume_v1_creation_without_environment(
        identity,
        outbound_proxy_url=outbound_proxy_url,
    )


def _resume_v1_creation_without_environment(
    identity: InstanceIdentity,
    *,
    outbound_proxy_url: str | None,
) -> None:
    if not identity.secrets_directory.exists() and not identity.secrets_directory.is_symlink():
        _create_secret_directory(identity, _new_v2_secret_material())
    secret_filenames = _admit_migratable_secret_directory(identity)
    if secret_filenames == _V2_SECRET_FILENAMES:
        _admit_metrics_bearer_token(identity)
    else:
        _publish_metrics_bearer_token(identity)
    atomic_write_private_text(
        identity.environment_path,
        _render_environment(_new_v2_environment(identity, outbound_proxy_url=outbound_proxy_url)),
    )
    _write_metadata(identity, _V2_METADATA_SCHEMA_VERSION)


def _migrate_or_resume_v2_state(
    identity: InstanceIdentity,
    *,
    outbound_proxy_url: str | None,
) -> None:
    environment = _read_environment(identity.environment_path)
    environment_keys = frozenset(environment)
    if environment_keys.issubset(_V2_REQUIRED_ENVIRONMENT_KEYS | _OPTIONAL_ENVIRONMENT_KEYS):
        required_keys = _V2_REQUIRED_ENVIRONMENT_KEYS
    else:
        required_keys = _REQUIRED_ENVIRONMENT_KEYS
    _admit_environment(
        identity,
        environment,
        required_keys=required_keys,
        outbound_proxy_url=outbound_proxy_url,
    )
    if required_keys == _V2_REQUIRED_ENVIRONMENT_KEYS:
        _admit_v2_control_plane_environment(environment)

    secret_filenames = _admit_v2_or_current_secret_directory(identity)
    credential_filename = (
        "operator-bearer-token"
        if secret_filenames == _V2_SECRET_FILENAMES
        else "break-glass-bearer-token"
    )
    _admit_break_glass_bearer_token(identity, credential_filename)
    if required_keys == _V2_REQUIRED_ENVIRONMENT_KEYS and secret_filenames == _SECRET_FILENAMES:
        raise InstanceStateError("instance migration prefix is invalid")

    if required_keys == _V2_REQUIRED_ENVIRONMENT_KEYS:
        atomic_write_private_text(
            identity.environment_path,
            _render_environment(_migrated_v3_environment(environment)),
        )
    if secret_filenames == _V2_SECRET_FILENAMES:
        os.replace(
            identity.secrets_directory / "operator-bearer-token",
            identity.secrets_directory / "break-glass-bearer-token",
        )
    _write_metadata(identity, _METADATA_SCHEMA_VERSION)


def _admit_v2_control_plane_environment(environment: Mapping[str, str]) -> None:
    if environment.get("CI_COORDINATOR_OPERATOR_ID") != "operator:local-development":
        raise InstanceStateError("instance migration operator identity is invalid")


def _migrated_v3_environment(environment: Mapping[str, str]) -> dict[str, str]:
    migrated = dict(environment)
    for previous, current in _V2_ENVIRONMENT_RENAMES.items():
        value = migrated.pop(previous)
        migrated[current] = value
    migrated["CI_COORDINATOR_BREAK_GLASS_ACTOR_ID"] = "break-glass:v1:local-development"
    return migrated


def _publish_metrics_bearer_token(identity: InstanceIdentity) -> None:
    atomic_write_private_text(
        identity.secrets_directory / "metrics-bearer-token",
        secrets.token_urlsafe(48) + "\n",
        temporary_directory=identity.state_directory,
    )


def _admit_v2_environment_additions(environment: Mapping[str, str]) -> None:
    if any(environment.get(key) != value for key, value in _V2_ENVIRONMENT_ADDITIONS):
        raise InstanceStateError("instance migration environment defaults are invalid")


def _admit_metrics_bearer_token(identity: InstanceIdentity) -> None:
    value = read_private_text(identity.secrets_directory / "metrics-bearer-token")
    if not value.endswith("\n") or not is_metrics_bearer_token(value[:-1]):
        raise InstanceStateError("instance migration metrics bearer token is invalid")


def _admit_break_glass_bearer_token(identity: InstanceIdentity, filename: str) -> None:
    value = read_private_text(identity.secrets_directory / filename)
    if not value.endswith("\n") or not is_break_glass_bearer_token(value[:-1]):
        raise InstanceStateError("instance migration break-glass bearer token is invalid")


def _admit_or_create_secrets(identity: InstanceIdentity) -> None:
    if identity.secrets_directory.exists():
        _admit_secret_directory(identity)
        return
    _create_secret_directory(identity, _new_secret_material())
    _admit_secret_directory(identity)


def _create_secret_directory(identity: InstanceIdentity, material: Mapping[str, str]) -> None:
    temporary = Path(tempfile.mkdtemp(prefix=".secrets.", dir=identity.state_directory))
    try:
        os.chmod(temporary, 0o700)
        for filename, value in material.items():
            atomic_write_private_text(temporary / filename, value)
        os.replace(temporary, identity.secrets_directory)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _admit_secret_directory(identity: InstanceIdentity) -> None:
    names = _admit_secret_files(identity)
    if names != _SECRET_FILENAMES:
        raise InstanceStateError("instance secret file set is invalid")


def _admit_migratable_secret_directory(identity: InstanceIdentity) -> frozenset[str]:
    names = _admit_secret_files(identity)
    if names not in {_V1_SECRET_FILENAMES, _V2_SECRET_FILENAMES}:
        raise InstanceStateError("instance secret file set is invalid")
    return names


def _admit_v2_or_current_secret_directory(identity: InstanceIdentity) -> frozenset[str]:
    names = _admit_secret_files(identity)
    if names not in {_V2_SECRET_FILENAMES, _SECRET_FILENAMES}:
        raise InstanceStateError("instance secret file set is invalid")
    return names


def _admit_secret_files(identity: InstanceIdentity) -> frozenset[str]:
    require_private_directory(identity.secrets_directory)
    try:
        names = frozenset(path.name for path in identity.secrets_directory.iterdir())
    except OSError as error:
        raise InstanceStateError("instance secret directory is unavailable") from error
    for filename in names:
        require_private_file(identity.secrets_directory / filename)
    return names


def _new_secret_material() -> dict[str, str]:
    superuser_password = secrets.token_urlsafe(32)
    migration_password = secrets.token_urlsafe(32)
    runtime_password = secrets.token_urlsafe(32)
    github_key = rsa.generate_private_key(public_exponent=65_537, key_size=2_048)
    signing_key = ed25519.Ed25519PrivateKey.generate()
    return {
        "github-private-key.pem": _private_key_pem(github_key),
        "migration-dsn": (
            "postgresql+psycopg://ci_coordinator_migration:"
            f"{migration_password}@postgres:5432/ci_coordinator\n"
        ),
        "metrics-bearer-token": secrets.token_urlsafe(48) + "\n",
        "break-glass-bearer-token": secrets.token_urlsafe(48) + "\n",
        "plan-signing-private-key.pem": _private_key_pem(signing_key),
        "postgres-migration-password": migration_password + "\n",
        "postgres-runtime-password": runtime_password + "\n",
        "postgres-superuser-password": superuser_password + "\n",
        "runtime-dsn": (
            "postgresql+psycopg://ci_coordinator_runtime:"
            f"{runtime_password}@postgres:5432/ci_coordinator\n"
        ),
        "webhook-secret": secrets.token_urlsafe(48) + "\n",
    }


def _new_environment(
    identity: InstanceIdentity,
    *,
    outbound_proxy_url: str | None,
) -> dict[str, str]:
    profile_id = hashlib.sha256(b"ci-coordinator-local-shadow-v1").hexdigest()
    environment = {
        # The listener serves the Compose network; host publication stays on loopback.
        "CI_COORDINATOR_BIND_HOST": "0.0.0.0",  # noqa: S104
        "CI_COORDINATOR_BIND_PORT": "3000",
        "CI_COORDINATOR_DEV_API_URL": "http://backend:3000",
        "CI_COORDINATOR_DEV_ENV_FILE": str(identity.environment_path),
        "CI_COORDINATOR_DEV_ROOT_DIGEST": identity.root_digest,
        "CI_COORDINATOR_DEV_SECRET_DIR": str(identity.secrets_directory),
        "CI_COORDINATOR_DATABASE_POOL_SIZE": "16",
        "CI_COORDINATOR_DATABASE_POOL_TIMEOUT_SECONDS": "5",
        "CI_COORDINATOR_GITHUB_APP_ID": "1",
        "CI_COORDINATOR_MAXIMUM_RETAINED_BODY_BYTES": "33554432",
        "CI_COORDINATOR_OIDC_ALLOWED_JOB_WORKFLOW_REFS": (
            "local/ci-coordinator/.github/workflows/reusable-ci.yml@refs/heads/local"
        ),
        "CI_COORDINATOR_OIDC_ALLOWED_WORKFLOW_REFS": (
            "local/ci-coordinator/.github/workflows/bootstrap.yml@refs/heads/local"
        ),
        "CI_COORDINATOR_OIDC_AUDIENCE": "ci-coordinator-local",
        "CI_COORDINATOR_BREAK_GLASS_ACTOR_ID": "break-glass:v1:local-development",
        "CI_COORDINATOR_CONTROL_PLANE_SCOPE_ALLOWLIST": "1:1",
        "CI_COORDINATOR_CONTROL_PLANE_INVENTORY_MODE": "app",
        "CI_COORDINATOR_PLAN_SIGNING_KEY_ID": "local-development-ed25519",
        "CI_COORDINATOR_PLAN_TTL_SECONDS": "120",
        "CI_COORDINATOR_RECONCILIATION_INTERVAL_SECONDS": "30",
        "CI_COORDINATOR_RECONCILIATION_SCAN_LIMIT": "100",
        "CI_COORDINATOR_RECONCILIATION_STARTUP_TIMEOUT_SECONDS": "30",
        "CI_COORDINATOR_REQUEST_TIMEOUT_SECONDS": "15",
        "CI_COORDINATOR_RUNTIME_MODE": "non_enforcing",
        "CI_COORDINATOR_SHADOW_ROLLOUT_PROFILE_ID": profile_id,
        "CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS": "30",
        "COMPOSE_PROJECT_NAME": identity.project_name,
    }
    if outbound_proxy_url is not None:
        environment["CI_COORDINATOR_OUTBOUND_PROXY_URL"] = outbound_proxy_url
    return environment


def _new_v2_secret_material() -> dict[str, str]:
    material = _new_secret_material()
    material["operator-bearer-token"] = material.pop("break-glass-bearer-token")
    return material


def _new_v2_environment(
    identity: InstanceIdentity,
    *,
    outbound_proxy_url: str | None,
) -> dict[str, str]:
    environment = _new_environment(identity, outbound_proxy_url=outbound_proxy_url)
    environment["CI_COORDINATOR_OPERATOR_ID"] = "operator:local-development"
    environment["CI_COORDINATOR_OPERATOR_SCOPE_ALLOWLIST"] = environment.pop(
        "CI_COORDINATOR_CONTROL_PLANE_SCOPE_ALLOWLIST"
    )
    environment.pop("CI_COORDINATOR_BREAK_GLASS_ACTOR_ID")
    environment.pop("CI_COORDINATOR_CONTROL_PLANE_INVENTORY_MODE")
    return environment


def _normalize_proxy_input(value: str | None) -> str | None:
    try:
        normalized = normalize_outbound_proxy_url(value)
    except (TypeError, ValueError) as error:
        raise InstanceStateError("instance outbound proxy is invalid") from error
    if value is not None and normalized is None:
        raise InstanceStateError("instance outbound proxy is invalid")
    return normalized


def _private_key_pem(key: object) -> str:
    if not isinstance(key, (rsa.RSAPrivateKey, ed25519.Ed25519PrivateKey)):
        raise TypeError("unsupported private key")
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")


def _render_environment(environment: Mapping[str, str]) -> str:
    return "".join(f"{key}={json.dumps(environment[key])}\n" for key in sorted(environment))


def _read_environment(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in read_private_text(path).splitlines():
        key, separator, encoded = line.partition("=")
        if not separator or key in result:
            raise InstanceStateError("instance environment syntax is invalid")
        try:
            value: object = json.loads(encoded)
        except json.JSONDecodeError as error:
            raise InstanceStateError("instance environment value is invalid") from error
        if not isinstance(value, str) or not value:
            raise InstanceStateError("instance environment value must be non-empty text")
        result[key] = value
    return result


def _read_json(path: Path) -> object:
    try:
        return json.loads(read_private_text(path))
    except json.JSONDecodeError as error:
        raise InstanceStateError("instance metadata is invalid") from error
