from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path("/runtime")


def query(*arguments: str) -> str:
    return subprocess.check_output(  # noqa: S603 -- fixed CLI, absolute paths or package identities
        ["/usr/bin/dpkg-query", *arguments], text=True, timeout=30
    )


packages = {"base-files", "ca-certificates", "tzdata", "zlib1g", "libc-bin", "openssl"}
files: dict[str, dict[str, str]] = {}
inventory = Path("/tmp/runtime-library-paths.txt")  # noqa: S108 -- isolated root-owned image build
for name in inventory.read_text().splitlines():
    source = Path(name)
    installed = ROOT / name.lstrip("/")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != hashlib.sha256(installed.read_bytes()).hexdigest():
        raise ValueError(f"Copied library differs: {name}")
    if name.startswith(("/usr/local/", "/app/")):
        continue
    owners = query("--search", str(source.resolve(strict=True))).splitlines()
    if len(owners) != 1 or ": " not in owners[0]:
        raise ValueError(f"Ambiguous library owner: {name}")
    package, _path = owners[0].split(": ", 1)
    if "," in package:
        raise ValueError(f"Shared library ownership: {name}")
    packages.add(package)
    files[name] = {"package": package, "sha256": digest}

database = ROOT / "var/lib/dpkg"
database.mkdir(parents=True)
packages = {query("--show", "--showformat=${binary:Package}", package) for package in packages}
paragraphs: list[str] = []
for package in sorted(packages):
    paragraphs.append(query("--status", package).strip())
    name = package.split(":", 1)[0]
    copyright_file = Path("/usr/share/doc") / name / "copyright"
    if not copyright_file.is_file():
        raise ValueError(f"Package license missing: {package}")
    destination = ROOT / "usr/share/doc" / name
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(copyright_file, destination / "copyright")
(database / "status").write_text("\n\n".join(paragraphs) + "\n")
shutil.copytree("/usr/share/common-licenses", ROOT / "usr/share/common-licenses")
destination = ROOT / "usr/share/ci-coordinator"
destination.mkdir(parents=True, exist_ok=True)
(destination / "system-libraries.json").write_text(json.dumps(files, sort_keys=True) + "\n")
