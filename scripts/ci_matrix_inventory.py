from __future__ import annotations

import hashlib
import json
import stat
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from scripts.ci_matrix_contract import MatrixProfile
from scripts.ci_matrix_inputs import input_dispositions
from scripts.ci_utility_inventory import repository_paths
from scripts.repository_paths import read_repository_regular_file, repository_path_matches

MAX_PATHS = 16_384
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024


def classify_paths(profile: MatrixProfile, paths: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    if not paths or len(paths) > MAX_PATHS or len(set(paths)) != len(paths):
        raise ValueError("CI matrix input universe must be nonempty, unique and bounded")
    classified: dict[str, tuple[str, ...]] = {}
    for path in paths:
        if (
            not path
            or path.startswith("/")
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or "\\" in path
        ):
            raise ValueError("CI matrix contains a noncanonical repository path")
        surfaces = tuple(
            row.id
            for row in profile.surfaces
            if any(repository_path_matches(pattern, path) for pattern in row.paths)
        )
        if not surfaces:
            raise ValueError(f"CI matrix has no admitted check surface for {path}")
        classified[path] = surfaces
    return classified


def snapshot(root: Path, profile: MatrixProfile) -> dict[str, object]:
    paths = repository_paths(root)
    classified = classify_paths(profile, paths)
    dispositions = input_dispositions(root, profile, paths)
    records: list[dict[str, object]] = []
    total = 0
    counts = dict.fromkeys((row.id for row in profile.surfaces), 0)
    for path, surfaces in classified.items():
        payload = read_repository_regular_file(
            root, Path(path), "CI matrix input", maximum_bytes=MAX_FILE_BYTES
        )
        total += len(payload)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("CI matrix input universe exceeds the aggregate byte bound")
        for surface in surfaces:
            counts[surface] += 1
        records.append(
            {
                "path": path,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "mode": stat.S_IMODE((root / path).stat().st_mode),
                "surfaces": surfaces,
                "inputDisposition": asdict(dispositions[path]),
            }
        )
    encoded = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    return {
        "scope": "git-observed-paths-with-current-worktree-bytes",
        "inputEvidenceClass": "static-consumer-input-dispositions",
        "fileCount": len(records),
        "byteCount": total,
        "inventorySha256": hashlib.sha256(encoded).hexdigest(),
        "surfaceCounts": counts,
        "inputDispositionCounts": dict(Counter(row.role for row in dispositions.values())),
        "inputConsumerCounts": dict(Counter(row.command_id for row in dispositions.values())),
        "nonClaims": [
            "Input dispositions prove a declared consumer relation at the named predicate, "
            "not all surface commands or semantic test adequacy.",
            "Native discovery, execution and fresh provider receipts remain separate "
            "proof obligations.",
            "Nonignored untracked inputs are included; ignored caches are outside "
            "this input universe.",
        ],
    }
