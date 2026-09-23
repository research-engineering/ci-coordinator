from __future__ import annotations

import inspect
import io
import json
import os
import poplib
import sys
import tarfile
import tempfile
import textwrap
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol, cast
from unittest.mock import patch


class _POP3CommandProbe(Protocol):
    _debugging: int
    encoding: str
    sock: SimpleNamespace

    def _putcmd(self, line: str) -> None: ...


class _ZipDecompressor(Protocol):
    @property
    def eof(self) -> bool: ...

    @property
    def needs_input(self) -> bool: ...

    def decompress(self, data: bytes, *args: int) -> bytes: ...


def idna_mapping() -> bool:
    cases = (
        ("\u13a0\u13a0", b"xn--58da"),
        ("\u10a0.", b"xn--7md."),
        ("\u04c0.example", b"xn--d5a.example"),
        ("\u2183.example.", b"xn--q5g.example."),
    )
    assert "example.org".encode("idna") == b"example.org"
    results = [text.encode("idna") == encoded for text, encoded in cases]
    return all(results)


def credential_schemes() -> bool:
    safe = True
    for cls in (
        urllib.request.HTTPPasswordMgr,
        urllib.request.HTTPPasswordMgrWithPriorAuth,
    ):
        manager = cls()
        manager.add_password("realm", "https://example.invalid:443/", "user", "secret")
        assert manager.find_user_password("realm", "https://example.invalid:443/a") == (
            "user",
            "secret",
        )
        safe &= manager.find_user_password("realm", "http://example.invalid:443/a") == (
            None,
            None,
        )
        manager.add_password("proxy", "proxy.invalid:3128", "proxy-user", "proxy-secret")
        for scheme in ("http", "https"):
            assert manager.find_user_password("proxy", f"{scheme}://proxy.invalid:3128/") == (
                "proxy-user",
                "proxy-secret",
            )
    prior = urllib.request.HTTPPasswordMgrWithPriorAuth()
    prior.add_password(
        None, "https://example.invalid:443/", "user", "secret", is_authenticated=True
    )
    assert prior.is_authenticated("https://example.invalid:443/a") is True
    safe &= not prior.is_authenticated("http://example.invalid:443/a")
    return bool(safe)


def pop_commands() -> bool:
    # Only the private command path runs; the injected sendall sink never opens a socket.
    client = cast(_POP3CommandProbe, object.__new__(poplib.POP3))
    client._debugging = 0
    client.encoding = "UTF-8"
    writes: list[bytes] = []
    client.sock = SimpleNamespace(sendall=writes.append)
    client._putcmd("USER ordinary")
    assert writes == [b"USER ordinary\r\n"]
    rejected = 0
    for codepoint in (*range(32), 127):
        writes.clear()
        try:
            client._putcmd(f"USER a{chr(codepoint)}b")
        except ValueError:
            assert not writes
            rejected += 1
        else:
            assert writes == [f"USER a{chr(codepoint)}b\r\n".encode()]
    assert rejected in (0, 33)
    return rejected == 33


def local_file_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "local content.txt"
        payload = b"local file URI positive control\x00\xff"
        path.write_bytes(payload)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(path.as_uri(), timeout=1) as response:
            assert response.read() == payload


def local_file_sensitivity() -> list[str]:
    with patch.object(urllib.request.FileHandler, "file_open", return_value=io.BytesIO(b"wrong")):
        try:
            local_file_roundtrip()
        except AssertionError:
            return ["file-content"]
        raise AssertionError("File URI content mutant survived")


def tar_parent_excursion() -> bool:
    safe = True
    for filter_name in ("tar", "data"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "destination"
            destination.mkdir()
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w") as archive:
                info = tarfile.TarInfo("../outside/../destination/sub/file")
                info.size = 4
                archive.addfile(info, io.BytesIO(b"data"))
            buffer.seek(0)
            with tarfile.open(fileobj=buffer) as archive:
                archive.extractall(destination, filter=filter_name)  # noqa: S202 - owned adversarial fixture
            assert (destination / "sub/file").read_bytes() == b"data"
            safe &= not (root / "outside").exists()
    return bool(safe)


def tar_skipped_fallback() -> bool:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            link = tarfile.TarInfo("a/b/s")
            link.type = tarfile.SYMTYPE
            link.linkname = "../escape"
            archive.addfile(link)
            alias = tarfile.TarInfo("q")
            alias.type = tarfile.LNKTYPE
            alias.linkname = "a/b/s"
            archive.addfile(alias)
        rejected: list[str] = []

        def admission(member: tarfile.TarInfo, path: str) -> tarfile.TarInfo | None:
            try:
                return tarfile.data_filter(member, path)
            except tarfile.FilterError:
                rejected.append(member.name)
                return None

        buffer.seek(0)
        with tarfile.open(fileobj=buffer) as archive:
            archive.extractall(root, filter=admission)  # noqa: S202 - owned filter-rejection fixture
        assert (root / "a/b/s").is_symlink()
        assert "q" in rejected
        return not (root / "q").is_symlink()


def zip_bounded_expansion() -> bool:
    payload = b"a" * (2 * 1024 * 1024)
    safe = True
    # Typeshed omits this factory; only the BZIP2/LZMA probe paths call it.
    original: Callable[[int], _ZipDecompressor] = zipfile._get_decompressor  # type: ignore[attr-defined]

    class Observed:
        def __init__(self, kind: int, *, legacy: bool = False) -> None:
            self.inner = original(kind)
            self.outputs: list[int] = []
            self.legacy = legacy

        @property
        def eof(self) -> bool:
            return self.inner.eof

        @property
        def needs_input(self) -> bool:
            if self.legacy:
                raise AttributeError("legacy decompressor has no needs_input")
            return self.inner.needs_input

        def decompress(self, data: bytes, *args: int) -> bytes:
            if self.legacy and args:
                raise TypeError("legacy decompressor accepts one argument")
            output = self.inner.decompress(data, *args)
            self.outputs.append(len(output))
            return output

    for kind in (zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=kind) as archive:
            archive.writestr("payload", payload)
        for legacy in (False, True):
            observed = Observed(kind, legacy=legacy)
            with (
                patch.object(zipfile, "_get_decompressor", return_value=observed),
                zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive,
                archive.open("payload") as member,
            ):
                first = member.read(1)
                if not legacy:
                    safe &= max(observed.outputs) <= zipfile.ZipExtFile.MIN_READ_SIZE
                chunks = [first]
                while chunk := member.read(4096):
                    chunks.append(chunk)
                assert b"".join(chunks) == payload
            assert observed.outputs
    return bool(safe)


def legacy_zip_roundtrip() -> None:
    class Compressor:
        def compress(self, data: bytes) -> bytes:
            return data.swapcase()

        def flush(self) -> bytes:
            return b""

    class Decompressor:
        eof = False

        def decompress(self, data: bytes) -> bytes:
            return data.swapcase()

    payload = bytes(range(256)) * 8
    buffer = io.BytesIO()
    with (
        patch.object(zipfile, "_check_compression", return_value=None),
        patch.object(zipfile, "_get_compressor", side_effect=lambda *_: Compressor()),
        patch.object(zipfile, "_get_decompressor", side_effect=lambda *_: Decompressor()),
    ):
        with zipfile.ZipFile(buffer, "w", compression=99) as archive:
            archive.writestr("member", payload)
        assert payload.swapcase() in buffer.getvalue()
        with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
            assert archive.read("member") == payload
            # Read mode returns ZipExtFile; the stub also covers write-mode handles.
            with cast(zipfile.ZipExtFile, archive.open("member")) as member:
                assert member.read(100) == payload[:100]
                assert member.read1(100) == payload[100:200]
                member.seek(-100, os.SEEK_END)
                assert member.read() == payload[-100:]
                member.seek(0)
                assert member.read() == payload


def zip_compatibility_sensitivity() -> list[str]:
    # This exact private method is the owned compatibility-mutation target.
    source = textwrap.dedent(inspect.getsource(zipfile.ZipExtFile._read1))  # type: ignore[attr-defined]
    guarded = 'getattr(self._decompressor, "needs_input", True)'
    assert source.count(guarded) == 2
    prefix, middle, suffix = source.split(guarded)
    killed: list[str] = []
    for name, mutant in (
        ("input-drain", prefix + "self._decompressor.needs_input" + middle + guarded + suffix),
        ("terminal", prefix + guarded + middle + "self._decompressor.needs_input" + suffix),
    ):
        namespace = dict(vars(zipfile))
        exec(compile(mutant, "<owned-zip-compatibility-mutant>", "exec"), namespace)  # noqa: S102 - exact stdlib witness mutation
        with patch.object(zipfile.ZipExtFile, "_read1", namespace["_read1"]):
            try:
                legacy_zip_roundtrip()
            except AttributeError as error:
                assert error.name == "needs_input"
                killed.append(name)
            else:
                raise AssertionError(f"Compatibility mutant survived: {name}")
    return killed


checks: dict[str, Callable[[], bool]] = {
    "CVE-2026-17084": idna_mapping,
    "CVE-2026-15806": credential_schemes,
    "CVE-2025-15367": pop_commands,
    "CVE-2026-19672": tar_parent_excursion,
    "CVE-2026-87910": tar_skipped_fallback,
    "CVE-2026-15310": zip_bounded_expansion,
}
results = {cve: check() for cve, check in checks.items()}
local_file_roundtrip()
file_mutations = local_file_sensitivity()
legacy_zip_roundtrip()
assert sys.argv[1:] in (["before"], ["after"])
expected = sys.argv[1] == "after"
mutations = zip_compatibility_sensitivity() if expected else []
print(
    json.dumps(
        {
            "phase": sys.argv[1],
            "repairs": results,
            "positiveControls": "passed",
            "fileUriMutationsKilled": file_mutations,
            "legacyMutationsKilled": mutations,
        }
    )
)
assert all(result is expected for result in results.values()), results
