from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.x509.oid import NameOID

ISSUER = "https://auth.example.test/realms/coordinator"
SUBJECT = "3c42bfcb-a928-43f6-8c81-cc10c1b6eb24"
ACTOR = "keycloak-human:v1:" + hashlib.sha256(f"{ISSUER}\0{SUBJECT}".encode()).hexdigest()


@dataclass(frozen=True)
class Fixture:
    directory: Path
    canaries: tuple[bytes, ...]
    database_password: str
    policy_key: str


class FixtureWorkspace(Protocol):
    @property
    def directory(self) -> Path | None: ...

    def verify(self) -> None: ...


def write_private(path: Path, value: str | bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(value.encode() if isinstance(value, str) else value)
        os.fchmod(output.fileno(), 0o400)


def prepare_fixture(workspace: FixtureWorkspace, root: Path) -> Fixture:
    workspace.verify()
    directory = workspace.directory
    if directory is None:
        raise ValueError("volatile fixture directory is absent")
    directory.chmod(0o700)
    material = {
        name: secrets.token_urlsafe(48)
        for name in (
            "postgres-superuser-password",
            "postgres-migration-password",
            "postgres-runtime-password",
            "browser-secret",
            "reviewer-secret",
            "metrics-token",
            "break-glass-token",
            "webhook-secret",
            "password",
            "log-canary",
        )
    }
    material["session-key"] = secrets.token_urlsafe(32)
    material["runtime-dsn"] = (
        "postgresql+psycopg://ci_coordinator_runtime:"
        + material["postgres-runtime-password"]
        + "@postgres:5432/ci_coordinator"
    )
    material["migration-dsn"] = (
        "postgresql+psycopg://ci_coordinator_migration:"
        + material["postgres-migration-password"]
        + "@postgres:5432/ci_coordinator"
    )
    for name, key in (
        ("github-key.pem", rsa.generate_private_key(public_exponent=65537, key_size=2048)),
        ("signing-key.pem", ed25519.Ed25519PrivateKey.generate()),
    ):
        material[name] = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
    for name, value in material.items():
        if name not in {"password", "log-canary"}:
            write_private(directory / name, value)
    ca, certificate, tls_key = _certificates()
    write_private(directory / "ca.pem", ca)
    write_private(directory / "tls.pem", certificate)
    write_private(directory / "tls-key.pem", tls_key)
    realm = json.loads((root / "docker/ci/keycloak-realm.json").read_text())
    realm["clients"][1]["secret"] = material["browser-secret"]
    realm["users"][0]["credentials"] = [
        {"type": "password", "value": material["password"], "temporary": False}
    ]
    write_private(directory / "realm.json", json.dumps(realm))
    policy_key = "connected-" + secrets.token_hex(8)
    write_private(
        directory / "browser.json",
        json.dumps(
            {
                "username": "ci-administrator",
                "password": material["password"],
                "logCanary": material["log-canary"],
                "actorId": ACTOR,
                "policyKey": policy_key,
            }
        ),
    )
    (directory / "evidence").mkdir(mode=0o700)
    write_private(directory / "backend.env", _backend_environment())
    values = (*material.values(), tls_key.decode())
    encoded = {item.encode() for item in values}
    encoded.update(quote(item, safe="").encode() for item in values)
    encoded.add(
        base64.b64encode(("ci-coordinator-admin-ui:" + material["browser-secret"]).encode())
    )
    return Fixture(
        directory, tuple(sorted(encoded)), material["postgres-superuser-password"], policy_key
    )


def _certificates() -> tuple[bytes, bytes, bytes]:
    now = datetime.now(UTC)
    authority_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Connected witness disposable CA")])
    authority = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(authority_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(authority_key.public_key()), critical=False
        )
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True
        )
        .sign(authority_key, hashes.SHA256())
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "coordinator.test")]))
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(authority_key.public_key()),
            critical=False,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.KeyUsage(True, False, True, False, False, False, False, False, False),
            critical=True,
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("coordinator.test"),
                    x509.DNSName("auth.example.test"),
                ]
            ),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(authority_key, hashes.SHA256())
    )
    return (
        authority.public_bytes(serialization.Encoding.PEM),
        certificate.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )


def _backend_environment() -> str:
    settings = {
        "RUNTIME_MODE": "non_enforcing",
        "BIND_HOST": "0.0.0.0",  # noqa: S104 -- internal Compose network only
        "BIND_PORT": "3000",
        "DATABASE_POOL_SIZE": "16",
        "DATABASE_POOL_TIMEOUT_SECONDS": "5",
        "MAXIMUM_RETAINED_BODY_BYTES": "33554432",
        "GITHUB_APP_ID": "1",
        "BREAK_GLASS_ACTOR_ID": "break-glass:v1:connected-ci",
        "CONTROL_PLANE_SCOPE_ALLOWLIST": "1:1",
        "CONTROL_PLANE_SCOPE_MODE": "restricted",
        "CONTROL_PLANE_INVENTORY_MODE": "restricted",
        "PLAN_SIGNING_KEY_ID": "connected-ci-ed25519",
        "PLAN_TTL_SECONDS": "120",
        "OIDC_AUDIENCE": "connected-ci",
        "OIDC_ALLOWED_WORKFLOW_REFS": "fixture/ci/.github/workflows/check.yml@refs/heads/main",
        "RECONCILIATION_INTERVAL_SECONDS": "30",
        "RECONCILIATION_SCAN_LIMIT": "100",
        "RECONCILIATION_STARTUP_TIMEOUT_SECONDS": "30",
        "REQUEST_TIMEOUT_SECONDS": "15",
        "SHADOW_ROLLOUT_PROFILE_ID": hashlib.sha256(b"connected-ci").hexdigest(),
        "SHUTDOWN_TIMEOUT_SECONDS": "30",
        "CONTROL_PLANE_AUTH_MODE": "keycloak",
        "KEYCLOAK_ISSUER": ISSUER,
        "KEYCLOAK_BROWSER_CLIENT_ID": "ci-coordinator-admin-ui",
        "KEYCLOAK_API_CLIENT_ID": "ci-coordinator-admin-api",
        "PUBLIC_ORIGIN": "https://coordinator.test",
        "CONTROL_PLANE_SESSION_MAXIMUM_SECONDS": "900",
        "GITHUB_APP_CLIENT_ID": "Iv1SyntheticClient01",
    }
    files = {
        "DATABASE_DSN": "runtime-dsn",
        "GITHUB_PRIVATE_KEY": "github-key.pem",
        "PLAN_SIGNING_PRIVATE_KEY": "signing-key.pem",
        "WEBHOOK_SECRET": "webhook-secret",
        "METRICS_BEARER_TOKEN": "metrics-token",
        "BREAK_GLASS_BEARER_TOKEN": "break-glass-token",
        "KEYCLOAK_BROWSER_CLIENT_SECRET": "browser-secret",
        "GITHUB_APP_CLIENT_SECRET": "reviewer-secret",
        "CONTROL_PLANE_SESSION_KEY": "session-key",
    }
    settings.update({f"{key}_FILE": f"/run/secrets/{value}" for key, value in files.items()})
    return "".join(f"CI_COORDINATOR_{key}={value}\n" for key, value in sorted(settings.items()))
