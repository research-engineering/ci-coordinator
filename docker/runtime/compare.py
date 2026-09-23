from __future__ import annotations

import hashlib
import json
import os
import re
import statistics
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import TypedDict


class Measurement(TypedDict):
    payloadSha256: str
    importNs: int
    maxRssKiB: int
    elapsedNs: dict[str, int]
    cpuNs: dict[str, int]


def admitted_commit(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{40}", value) is None or value == "0" * 40:
        raise ValueError("comparison source must be an exact nonzero commit")
    resolved = subprocess.check_output(
        ["git", "rev-parse", "--verify", f"{value}^{{commit}}"], text=True, timeout=20
    ).strip()
    if resolved != value:
        raise ValueError("comparison source identity changed")
    return resolved


def build_baseline(commit: str, tag: str) -> None:
    # Export only the admitted commit; never mix its Dockerfile with candidate inputs.
    with tempfile.TemporaryDirectory(prefix="runtime-baseline-") as temporary:
        root = Path(temporary)
        archive = root / "source.tar"
        source = root / "source"
        source.mkdir()
        subprocess.run(
            ["git", "archive", "--format=tar", f"--output={archive}", commit],
            check=True,
            timeout=30,
        )
        with tarfile.open(archive) as bundle:
            bundle.extractall(source, filter="data")
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
                tag,
                "--build-arg",
                "CI_COORDINATOR_SOURCE_COMMIT=" + commit,
                str(source),
            ],
            check=True,
            timeout=900,
        )


def image_id(tag: str) -> str:
    result = subprocess.check_output(
        ["docker", "image", "inspect", tag, "--format", "{{.Id}}"], text=True, timeout=20
    ).strip()
    if re.fullmatch(r"sha256:[0-9a-f]{64}", result) is None:
        raise ValueError("comparison image identity is invalid")
    return result


def compare(candidate: str, baseline: str, script: bytes) -> dict[str, object]:
    subjects = {"candidate": image_id(candidate), "baseline": image_id(baseline)}
    results: dict[str, list[Measurement]] = {role: [] for role in subjects}
    try:
        for sample in range(5):
            for role in list(subjects)[:: 1 if sample % 2 == 0 else -1]:
                output = subprocess.check_output(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "-i",
                        "--name",
                        "runtime-benchmark",
                        "--network",
                        "none",
                        "--read-only",
                        "--cap-drop",
                        "ALL",
                        "--security-opt",
                        "no-new-privileges",
                        "--memory",
                        "256m",
                        "--pids-limit",
                        "32",
                        "--cpus",
                        "1",
                        "--user",
                        "10001:10001",
                        "--tmpfs",
                        "/tmp:rw,noexec,nosuid,size=16m",  # noqa: S108 -- container mount, not host storage
                        "--env",
                        "EXPECTED_MALLOC_PROVIDER=/usr/lib/x86_64-linux-gnu/libc.so.6",
                        "--entrypoint",
                        "/app/backend/.venv/bin/python",
                        subjects[role],
                        "-I",
                        "-B",
                        "-",
                    ],
                    input=script,
                    timeout=30,
                )
                results[role].append(json.loads(output))
    finally:
        subprocess.run(
            ["docker", "rm", "--force", "runtime-benchmark"],
            check=False,
            capture_output=True,
            timeout=20,
        )
    if len({row["payloadSha256"] for rows in results.values() for row in rows}) != 1:
        raise ValueError("comparison workloads disagree")
    keys = set(results["candidate"][0]["elapsedNs"])
    if not keys or any(
        set(row["elapsedNs"]) != keys or set(row["cpuNs"]) != keys
        for rows in results.values()
        for row in rows
    ):
        raise ValueError("comparison workload inventory disagrees")
    return {
        role: {
            "imageId": subjects[role],
            "samples": rows,
            "medianImportNs": statistics.median(row["importNs"] for row in rows),
            "medianMaxRssKiB": statistics.median(row["maxRssKiB"] for row in rows),
            "medianElapsedNs": {
                key: statistics.median(row["elapsedNs"][key] for row in rows)
                for key in sorted(keys)
            },
            "medianCpuNs": {
                key: statistics.median(row["cpuNs"][key] for row in rows) for key in sorted(keys)
            },
        }
        for role, rows in results.items()
    }


def main() -> None:
    root = Path(os.environ["RUNNER_TEMP"]) / "qualification"
    candidate_commit = admitted_commit(os.environ["SOURCE_COMMIT"])
    baseline = os.environ.get("BASELINE_COMMIT", "")
    report: dict[str, object] = {
        "candidateSource": candidate_commit,
        "nonClaim": "Five alternating isolated workload samples; "
        "not global runtime equivalence, causal attribution or connected capacity",
    }
    if baseline in {"", "0" * 40}:
        report.update(status="not-measured", reason="baseline-unavailable", results={})
    else:
        baseline = admitted_commit(baseline)
        tag = "ci-coordinator:runtime-baseline"
        build_baseline(baseline, tag)
        script = Path("docker/runtime/benchmark.py").read_bytes()
        report.update(
            status="measured",
            baselineSource=baseline,
            workloadSha256=hashlib.sha256(script).hexdigest(),
            results=compare(os.environ["IMAGE"], tag, script),
        )
    (root / "performance.json").write_text(json.dumps(report) + "\n")


if __name__ == "__main__":
    main()
