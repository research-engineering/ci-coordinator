from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import TypedDict


class _ModuleRepair(TypedDict):
    module: str
    patch: str
    sourceSha256: str
    patchSha256: str
    installedSha256: str


class _StdlibRepairs(TypedDict):
    pythonVersion: str
    modules: list[_ModuleRepair]


security = Path("/security")
stdlib = Path("/usr/local/lib/python3.13")
manifest: _StdlibRepairs = json.loads((security / "stdlib-backports.json").read_bytes())
if manifest["pythonVersion"] != "3.13.15":
    raise ValueError("Unexpected Python repair target")
for record in manifest["modules"]:
    module = stdlib / record["module"]
    patch = security / record["patch"]
    if hashlib.sha256(module.read_bytes()).hexdigest() != record["sourceSha256"]:
        raise ValueError(f"Unexpected module preimage: {module}")
    if hashlib.sha256(patch.read_bytes()).hexdigest() != record["patchSha256"]:
        raise ValueError(f"Unexpected patch bytes: {patch}")
    with patch.open("rb") as source:
        subprocess.run(
            ["/usr/bin/patch", "--batch", "--fuzz=0", "-p1"],
            cwd=stdlib,
            stdin=source,
            check=True,
            timeout=10,
        )
    if hashlib.sha256(module.read_bytes()).hexdigest() != record["installedSha256"]:
        raise ValueError(f"Unexpected repaired module: {module}")
    for bytecode in (module.parent / "__pycache__").glob(f"{module.stem}.*.pyc"):
        bytecode.unlink()
    target = Path("/out/stdlib") / record["module"]
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    for parent in (target.parent, *target.parent.parents):
        if parent == Path("/out"):
            break
        parent.chmod(0o755)
    shutil.copyfile(module, target)
    target.chmod(0o644)
