from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import tarfile
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from ci_coordinator.runtime_settings.build_identity import parse_build_identity
from scripts.bounded_process import spawn
from scripts.release_predicate_admission import _load_strict_json
from scripts.release_publisher_identity import IMAGE
from scripts.release_repair_admission import (
    INSTALLED_PATHS,
    MAX_REPAIR_BYTES,
    ZLIB,
    RepairEvidence,
    evidence_bytes,
    read_policy,
    validate_observations,
)

ROOT = Path(__file__).resolve().parents[1]
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
BUILD_IDENTITY = "/app/backend/src/ci_coordinator/runtime_settings/resources/build-identity.v1.json"
CONTAINER_TMPFS = "/tmp:rw,noexec,nosuid,size=32m"  # noqa: S108 -- isolated container tmpfs


def command(*arguments: str, binary: bool = False, timeout: int = 60) -> bytes:
    result = spawn(
        "docker",
        arguments,
        cwd=ROOT,
        max_buffer=MAX_ARCHIVE_BYTES if binary else 16 * 1024 * 1024,
        timeout_seconds=timeout,
        decode_errors="surrogateescape" if binary else "strict",
    )
    if result.status != 0 or result.failure_kind is not None or result.error is not None:
        raise ValueError("repair Docker operation failed: " + arguments[0])
    return result.stdout.encode("utf-8", errors="surrogateescape" if binary else "strict")


def archive_file(archive: bytes, basename: str) -> bytes:
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise ValueError("repair file archive exceeds bound")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stream:
        member = stream.next()
        if (
            member is None
            or member.name != basename
            or member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE)
            or not 0 < member.size < MAX_ARCHIVE_BYTES
        ):
            raise ValueError("repair archive file identity")
        source = stream.extractfile(member)
        if source is None:
            raise ValueError("repair archive has no content")
        with source:
            content = source.read()
        if stream.next() is not None:
            raise ValueError("repair archive requires one file")
        return content


def copied_file(container: str, path: str) -> bytes:
    return archive_file(command("cp", container + ":" + path, "-", binary=True), Path(path).name)


def cleanup(label: str) -> None:
    rows = (
        command("ps", "--all", "--no-trunc", "--filter", "label=" + label, "--format", "{{.ID}}")
        .decode()
        .splitlines()
    )
    if any(re.fullmatch(r"[0-9a-f]{64}", row) is None for row in rows):
        raise ValueError("repair cleanup identity")
    if rows:
        command("rm", "--force", *rows, timeout=30)


def collect(*, subject: str, source_commit: str, policy_path: Path) -> RepairEvidence:
    registry = re.fullmatch(re.escape(IMAGE) + r"@sha256:[0-9a-f]{64}", subject) is not None
    if not registry and re.fullmatch(r"sha256:[0-9a-f]{64}", subject) is None:
        raise ValueError("repair subject must be an exact registry or local config digest")
    if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise ValueError("repair source commit")
    policy_hash, policy = read_policy(policy_path, ROOT)
    identifier = "repair-" + uuid.uuid4().hex
    label = "ci-coordinator.repair-proof=" + identifier
    try:
        if registry:
            command("pull", "--platform", "linux/amd64", subject, timeout=180)
        inspected = json.loads(command("image", "inspect", subject))
        if not isinstance(inspected, list) or len(inspected) != 1:
            raise ValueError("repair image observation")
        image = inspected[0]
        if image["Architecture"] != "amd64" or image["Os"] != "linux":
            raise ValueError("repair platform observation")
        image_id = image["Id"]
        if not isinstance(image_id, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
            raise ValueError("repair config identity")
        if (not registry and image_id != subject) or (
            registry and image["RepoDigests"] != [subject]
        ):
            raise ValueError("repair image subject observation")
        # Use the observed config identity for all later Docker operations.
        command(
            "create",
            "--name",
            identifier,
            "--label",
            label,
            "--network",
            "none",
            "--read-only",
            "--user",
            "10001:10001",
            "--entrypoint",
            "/usr/local/bin/python3.13",
            image_id,
        )
        installed = {
            path: hashlib.sha256(copied_file(identifier, path)).hexdigest()
            for path in sorted(INSTALLED_PATHS)
        }
        identity = parse_build_identity(copied_file(identifier, BUILD_IDENTITY))
        if identity.source_commit != source_commit:
            raise ValueError("repair source observation")
        witnesses = {}
        with tempfile.TemporaryDirectory(prefix="release-repairs-") as temporary:
            directory = Path(temporary)
            command(
                "buildx",
                "build",
                "--platform",
                "linux/amd64",
                "--target",
                "repair-witnesses",
                "--output",
                f"type=local,dest={directory}",
                ".",
                timeout=300,
            )
            for component in ("python", "stdlib", "zlib"):
                for phase in ("before", "after"):
                    _raw, value = _load_strict_json(
                        directory / f"{component}-{phase}.json",
                        label="repair-witness",
                        maximum=MAX_REPAIR_BYTES,
                    )
                    witnesses[f"{component}-{phase}"] = value
            directory.chmod(0o755)
            (directory / "check_zlib").chmod(0o755)
            proofs = (
                ("python", "/usr/local/bin/python3.13", ("-I", "-B", "/proof/check_tarfile.py")),
                (
                    "stdlib",
                    "/usr/local/bin/python3.13",
                    ("-I", "-B", "/proof/check_stdlib.py", "after"),
                ),
                ("zlib", "/native/check_zlib", (ZLIB,)),
            )
            for component, entrypoint, arguments in proofs:
                name = identifier + "-" + component
                script_root = ROOT / "docker/runtime/security"
                try:
                    output = command(
                        "run",
                        "--name",
                        name,
                        "--label",
                        label,
                        "--network",
                        "none",
                        "--read-only",
                        "--user",
                        "10001:10001",
                        "--cap-drop",
                        "ALL",
                        "--security-opt",
                        "no-new-privileges",
                        "--memory",
                        "128m",
                        "--pids-limit",
                        "32",
                        "--cpus",
                        "1",
                        "--tmpfs",
                        CONTAINER_TMPFS,
                        "--mount",
                        f"type=bind,source={script_root},target=/proof,readonly",
                        "--mount",
                        f"type=bind,source={directory},target=/native,readonly",
                        "--entrypoint",
                        entrypoint,
                        image_id,
                        *arguments,
                        timeout=45,
                    )
                    path = directory / (component + "-final.json")
                    path.write_bytes(output)
                    _raw, value = _load_strict_json(
                        path, label="repair-final", maximum=MAX_REPAIR_BYTES
                    )
                    witnesses[component + "-final"] = value
                finally:
                    cleanup(label)
        return RepairEvidence.model_validate(
            {
                "schemaVersion": "ci-coordinator.runtime-repair-evidence/v1",
                "sourceKind": "registry" if registry else "local",
                "subject": subject,
                "imageId": image_id,
                "repoDigests": image.get("RepoDigests") or [],
                "runtimeLayer": image["RootFS"]["Layers"][0],
                "sourceCommit": source_commit,
                "productionEligible": identity.production_eligible,
                "observedAt": datetime.now(UTC).isoformat(),
                "policySha256": policy_hash,
                "sourceInputs": policy.sourceInputs,
                "installedFiles": installed,
                "witnesses": witnesses,
            }
        )
    finally:
        cleanup(label)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = collect(
        subject=args.subject, source_commit=args.source_commit, policy_path=args.policy
    )
    policy_hash, policy = read_policy(args.policy, ROOT)
    validate_observations(
        policy=policy, policy_sha256=policy_hash, evidence=evidence, observed=datetime.now(UTC)
    )
    raw = evidence_bytes(evidence)
    if len(raw) > MAX_REPAIR_BYTES:
        raise ValueError("repair evidence exceeds bound")
    with args.output.open("xb") as output:
        output.write(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
