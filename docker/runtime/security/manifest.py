from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import NotRequired, TypedDict


class _UpstreamRepair(TypedDict):
    cve: str
    upstreamCommit: str


class _ModuleRepair(TypedDict):
    module: str
    installedSha256: str
    patchSha256: str
    sourceSha256: str
    repairs: list[_UpstreamRepair]


class _StdlibRepairs(TypedDict):
    pythonVersion: str
    modules: list[_ModuleRepair]


class _RepairRecord(TypedDict):
    component: str
    version: str
    cve: str
    path: str
    patchSha256: str
    installedSha256: NotRequired[str]
    upstreamCommit: NotRequired[str]
    upstreamCommits: NotRequired[list[str]]
    packageSha256: NotRequired[str]
    sourceArchiveSha256: NotRequired[str]
    sourceModuleSha256: NotRequired[str]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


root = Path("/runtime")
stdlib: _StdlibRepairs = json.loads(Path("/security/stdlib-backports.json").read_bytes())
records: list[_RepairRecord] = [
    {
        "component": "zlib",
        "version": "1:1.3.2-0+ci1",
        "cve": "CVE-2026-85091",
        "upstreamCommit": "df84af25dc1942490e1d1c899a07619152a46148",
        "path": "/usr/lib/x86_64-linux-gnu/libz.so.1.3.2",
        "patchSha256": sha256(Path("/security/zlib.patch")),
        "packageSha256": sha256(Path("/repairs/zlib1g.deb")),
        "sourceArchiveSha256": "bb329a0a2cd0274d05519d61c667c062e06990d72e125ee2dfa8de64f0119d16",
    },
]
for module in stdlib["modules"]:
    for cve in sorted({repair["cve"] for repair in module["repairs"]}):
        path = "/usr/local/lib/python3.13/" + module["module"]
        if sha256(root / path.lstrip("/")) != module["installedSha256"]:
            raise ValueError(f"Unexpected installed repair: {path}")
        records.append(
            {
                "component": "python",
                "version": stdlib["pythonVersion"],
                "cve": cve,
                "upstreamCommits": [
                    repair["upstreamCommit"] for repair in module["repairs"] if repair["cve"] == cve
                ],
                "path": path,
                "patchSha256": module["patchSha256"],
                "sourceModuleSha256": module["sourceSha256"],
            }
        )
for record in records:
    record["installedSha256"] = sha256(root / record["path"].lstrip("/"))
manifest = {
    "schemaVersion": 1,
    "origin": "ci-coordinator-upstream-backports",
    "repairs": records,
    "nonClaims": [
        "Not an unchanged vendor package, vendor signature, VEX or release admission.",
        "Original upstream versions are retained so advisory matches remain visible.",
    ],
}
destination = root / "usr/share/ci-coordinator/security-repairs.json"
destination.write_text(json.dumps(manifest, indent=2) + "\n")
