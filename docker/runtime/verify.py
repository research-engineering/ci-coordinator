from __future__ import annotations

import asyncio
import ctypes
import errno
import hashlib
import http.client
import importlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import socket
import ssl
import struct
import sys
import tarfile
from email import policy
from email.parser import Parser
from pathlib import Path
from typing import TypedDict

import psycopg
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from ci_coordinator.api.http.operator_ui import bundled_operator_ui_directory


class _RepairRecord(TypedDict):
    cve: str
    path: str
    installedSha256: str
    component: str


class _RepairManifest(TypedDict):
    repairs: list[_RepairRecord]


assert platform.python_version() == "3.13.15"
assert (os.getuid(), os.getgid()) == (10001, 10001)
assert not any(
    Path(p).exists()
    for p in ("/bin/sh", "/bin/bash", "/usr/bin/apt", "/usr/bin/dpkg", "/usr/bin/gcc")
)
assert platform.libc_ver()[0] == "glibc"
assert importlib.util.find_spec("tkinter") is None
assert importlib.util.find_spec("ensurepip") is None
repair_manifest: _RepairManifest = json.loads(
    Path("/usr/share/ci-coordinator/security-repairs.json").read_text()
)
bytecode_files: dict[str, str] = {}
assert {record["cve"] for record in repair_manifest["repairs"]} == {
    "CVE-2026-82049",
    "CVE-2026-85091",
    "CVE-2026-17084",
    "CVE-2026-15806",
    "CVE-2026-19672",
    "CVE-2026-87910",
    "CVE-2025-15367",
    "CVE-2026-15310",
}
assert len(repair_manifest["repairs"]) == 8
for record in repair_manifest["repairs"]:
    assert (
        hashlib.sha256(Path(record["path"]).read_bytes()).hexdigest() == record["installedSha256"]
    )
    if record["component"] == "python":
        bytecode = Path(importlib.util.cache_from_source(record["path"])).read_bytes()
        assert bytecode[:4] == importlib.util.MAGIC_NUMBER
        assert struct.unpack("<I", bytecode[4:8]) == (3,)
        bytecode_files[importlib.util.cache_from_source(record["path"])] = hashlib.sha256(
            bytecode
        ).hexdigest()
assert Path(tarfile.__file__).resolve() == Path("/usr/local/lib/python3.13/tarfile.py")
tarfile_bytecode = Path(importlib.util.cache_from_source(tarfile.__file__)).read_bytes()
assert tarfile_bytecode[:4] == importlib.util.MAGIC_NUMBER
assert struct.unpack("<I", tarfile_bytecode[4:8]) == (3,)
zlib_copies: list[dict[str, str]] = []
for directory in (
    Path("/lib"),
    Path("/usr/lib"),
    Path("/usr/local"),
    Path("/app/backend/.venv"),
):
    paths = {path.resolve() for path in directory.rglob("libz*.so*") if path.is_file()}
    for path in sorted(paths):
        if not (path.name.startswith("libz.so.") or path.name.startswith("libz-")):
            continue
        library = ctypes.CDLL(str(path))
        library.zlibVersion.restype = ctypes.c_char_p
        version: str = library.zlibVersion().decode("ascii")
        zlib_copies.append(
            {
                "path": str(path),
                "version": version,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
        assert version != "1.3.2" or path == Path("/usr/lib/x86_64-linux-gnu/libz.so.1.3.2"), (
            "additional zlib 1.3.2 copy needs independent repair"
        )
for name in (
    "_ssl",
    "_ctypes",
    "_decimal",
    "_sqlite3",
    "zlib",
    "bz2",
    "lzma",
    "cffi",
    "greenlet",
    "markupsafe",
    "pydantic_core",
    "rpds",
    "sqlalchemy",
):
    importlib.import_module(name)
assert psycopg.pq.__impl__ == "binary"
key = Ed25519PrivateKey.generate()
key.public_key().verify(key.sign(b"runtime-check"), b"runtime-check")
assert ssl.create_default_context().get_ca_certs()
assert ctypes.CDLL(None) is not None
try:
    Path("/home/ci-coordinator/write-probe").write_bytes(b"must-fail")
except OSError as error:
    assert error.errno == errno.EROFS
else:
    raise AssertionError("application root is writable")

connection = http.client.HTTPConnection("127.0.0.1", 3080, timeout=5)
connection.request("GET", "/healthz")
response = connection.getresponse()
assert response.status == 200
assert json.loads(response.read(4096)) == {"ok": True, "status": "alive"}
connection.close()

assert bundled_operator_ui_directory().is_dir()
dsn = os.environ["QUALIFICATION_DATABASE_DSN"]
database_address = socket.gethostbyname("database")
with psycopg.connect(dsn, hostaddr=database_address, connect_timeout=5) as conn:
    assert conn.execute("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()").fetchone() == (
        True,
    )
    assert conn.execute("SHOW server_version_num").fetchone() == ("180006",)
try:
    psycopg.connect(
        dsn,
        host="wrong-hostname.invalid",
        hostaddr=database_address,
        connect_timeout=5,
    )
except psycopg.OperationalError as error:
    assert 'does not match host name "wrong-hostname.invalid"' in str(error)
else:
    raise AssertionError("PostgreSQL hostname verification did not reject")
with psycopg.connect(dsn, hostaddr=database_address, connect_timeout=5) as conn:
    assert conn.execute("SELECT 1").fetchone() == (1,)


async def verify_transaction() -> None:
    engine = create_async_engine("postgresql+psycopg" + dsn.removeprefix("postgresql"))
    try:
        async with engine.connect() as conn:
            await conn.execute(text("CREATE TEMP TABLE image_probe(value integer)"))
            await conn.commit()
            await conn.execute(text("INSERT INTO image_probe VALUES (7)"))
            await conn.rollback()
            assert await conn.scalar(text("SELECT count(*) FROM image_probe")) == 0
    finally:
        await engine.dispose()


asyncio.run(verify_transaction())
installed = Path("/var/lib/dpkg/status").read_bytes()
packages = []
for paragraph in installed.decode().split("\n\n"):
    fields = Parser(policy=policy.default).parsestr(paragraph)
    if fields["Package"]:
        packages.append({"name": fields["Package"], "version": fields["Version"]})
distributions = sorted(
    (
        {"name": d.metadata["Name"], "version": d.version}
        for d in importlib.metadata.distributions()
    ),
    key=lambda d: d["name"],
)
interpreter_files: dict[str, str] = {}
for binary_path in ("/usr/local/bin/python3.13", "/usr/local/lib/libpython3.13.so.1.0"):
    with Path(binary_path).open("rb") as stream:
        interpreter_files[binary_path] = hashlib.file_digest(stream, "sha256").hexdigest()
assert not {d["name"].lower() for d in distributions}.intersection(
    {"pytest", "ruff", "mypy", "agentic-proofkit"}
)
print(
    json.dumps(
        {
            "schemaVersion": "runtime-image-qualification/v1",
            "python": platform.python_version(),
            "executable": sys.executable,
            "uid": os.getuid(),
            "gid": os.getgid(),
            "packageDatabaseSha256": hashlib.sha256(installed).hexdigest(),
            "systemPackages": sorted(packages, key=lambda p: p["name"]),
            "pythonPackages": distributions,
            "interpreterFiles": interpreter_files,
            "repairBytecodeFiles": bytecode_files,
            "securityRepairs": repair_manifest,
            "zlibCopies": zlib_copies,
            "zlibInventoryScope": {
                "roots": ["/lib", "/usr/lib", "/usr/local", "/app/backend/.venv"],
                "namedSharedLibraries": "enumerated",
                "staticOrRenamedEmbeddedCopies": "unverified",
            },
            "verified": [
                "nonroot",
                "read-only",
                "native-imports",
                "ed25519",
                "ca-store",
                "health",
                "postgres-tls",
                "postgres-hostname-rejection",
                "async-rollback",
                "packaged-ui",
            ],
            "nonClaims": [
                "No release admission, complete test-suite proof, performance "
                "or vulnerability-free claim."
            ],
        }
    )
)
