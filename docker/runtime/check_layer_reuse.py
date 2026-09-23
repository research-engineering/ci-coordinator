from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import TypedDict


class _RootFS(TypedDict):
    Layers: list[str]


class _ImageInspection(TypedDict):
    Id: str
    RootFS: _RootFS


def inspect(tag: str) -> _ImageInspection:
    images: list[_ImageInspection] = json.loads(
        subprocess.check_output(
            ["docker", "image", "inspect", tag],
            timeout=20,
        )
    )
    return images[0]


root = Path(os.environ["RUNNER_TEMP"]) / "qualification"
original = inspect(os.environ["IMAGE"])
layers = original["RootFS"]["Layers"]
assert len(layers) >= 3, layers
source = Path("backend/src/ci_coordinator/runtime/__init__.py")
contents = source.read_bytes()
try:
    source.write_bytes(contents + b"\n# Application-only layer reuse qualification.\n")
    started = time.perf_counter_ns()
    subprocess.run(
        [
            "docker",
            "buildx",
            "build",
            "--platform",
            "linux/amd64",
            "--load",
            "--provenance=false",
            "--tag",
            "ci-coordinator:application-layer-probe",
            "--build-arg",
            "CI_COORDINATOR_SOURCE_COMMIT=" + os.environ["SOURCE_COMMIT"],
            ".",
        ],
        check=True,
        timeout=180,
    )
    elapsed = time.perf_counter_ns() - started
    probe = inspect("ci-coordinator:application-layer-probe")
    probe_layers = probe["RootFS"]["Layers"]
    assert len(probe_layers) == len(layers), (layers, probe_layers)
    changed = [
        index
        for index, pair in enumerate(zip(layers, probe_layers, strict=True))
        if pair[0] != pair[1]
    ]
    assert changed == [2], (layers, probe_layers)
finally:
    source.write_bytes(contents)
assert source.read_bytes() == contents
(root / "layer-reuse.json").write_text(
    json.dumps(
        {
            "imageId": original["Id"],
            "probeImageId": probe["Id"],
            "unchangedLayers": original["RootFS"]["Layers"][:2],
            "applicationChangeBuildElapsedNs": elapsed,
            "nonClaim": "Same-run cache with one source edit; "
            "not registry transfer or cold-build performance",
        }
    )
)
