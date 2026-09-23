from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path, PurePosixPath

from scripts.ci_utility_checks import project_environment
from scripts.release_predicate_admission import _load_strict_json
from scripts.release_repair_admission import REPAIR_PREDICATE
from scripts.release_vulnerability_admission import (
    IMAGE,
    MAX_EVIDENCE_BYTES,
    RELEASE_SHA256,
    RELEASE_URL,
    Policy,
    admit_release_vulnerability,
)
from scripts.scheduled_advisories import (
    REPOSITORY,
    command_fact,
    digest,
    execute,
    now,
    require_success,
    write_receipt,
)
from scripts.scheduled_release_subject import (
    WORKFLOW_PATH,
    admit_attestation,
    admit_build_identity,
    admit_repair_attestation,
    latest_release,
    registry_subject,
    strict_json,
)

POLICY_PATH = "docs/specs/ci-coordinator-release/vulnerability-gate-policy.v1.json"


def admit_scan_receipt(root: Path, receipt: dict[str, object], directory: Path) -> None:
    raw, evidence = _load_strict_json(
        directory / "released-image-vulnerabilities.json",
        label="release vulnerability receipt",
        maximum=MAX_EVIDENCE_BYTES,
    )
    if receipt.get("vulnerabilityEvidenceSha256") != digest(raw):
        raise ValueError("release-evidence-checksum-mismatch")
    subject = receipt.get("releasedSubject")
    if not isinstance(subject, dict) or subject.get("platform") != "linux/amd64":
        raise ValueError("release-evidence-subject-invalid")
    image = subject.get("image")
    if (
        not isinstance(image, str)
        or re.fullmatch(re.escape(IMAGE) + r"@sha256:[0-9a-f]{64}", image) is None
    ):
        raise ValueError("release-evidence-subject-invalid")
    if not isinstance(evidence, dict) or any(
        evidence.get(key) != expected
        for key, expected in (
            ("schemaVersion", "ci-coordinator.release-vulnerability-gate/v2"),
            ("admission", "accepted"),
            ("policySha256", digest((root / POLICY_PATH).read_bytes())),
            ("subject", {"image": image, "platform": subject["platform"]}),
        )
    ):
        raise ValueError("release-evidence-owner-or-subject-mismatch")
    manifest = evidence.get("manifest")
    if not isinstance(manifest, dict) or manifest.get("indexDigest") != image.rsplit("@", 1)[1]:
        raise ValueError("release-evidence-index-mismatch")
    child = manifest.get("platformManifestDigest")
    if not isinstance(child, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", child) is None:
        raise ValueError("release-evidence-platform-missing")
    _admit_scan_command(receipt, evidence, image)


def _admit_scan_command(
    receipt: dict[str, object], evidence: dict[str, object], subject: str
) -> None:
    commands = receipt.get("commands")
    scanner = evidence.get("scanner")
    inputs = evidence.get("inputs")
    if (
        not isinstance(commands, list)
        or not isinstance(scanner, dict)
        or not isinstance(inputs, dict)
    ):
        raise ValueError("release-evidence-scan-command-missing")
    count = evidence.get("rawThresholdFindingCount")
    rejected = evidence.get("rejectedFindingCount")
    scan_hash = inputs.get("scanReportSha256")
    if (
        type(count) is not int
        or count < 0
        or type(rejected) is not int
        or rejected != 0
        or not isinstance(scan_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", scan_hash) is None
    ):
        raise ValueError("release-evidence-scan-result")
    expected_exit = 2 if count else 0
    if type(scanner.get("exitCode")) is not int or scanner["exitCode"] != expected_exit:
        raise ValueError("release-evidence-scan-exit")
    expected_arguments = [
        subject,
        "--from",
        "registry",
        "--platform",
        "linux/amd64",
        "--scope",
        "squashed",
        "--output",
        "json",
        "--fail-on",
        "high",
    ]
    scans = 0
    for command in commands:
        if not isinstance(command, dict):
            raise ValueError("release-evidence-scan-command")
        argv = command.get("argv")
        if not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv):
            raise ValueError("release-evidence-scan-arguments")
        is_scan = len(argv) == 14 and argv[1] == "-c" and argv[3:] == expected_arguments
        if is_scan:
            binary = PurePosixPath(argv[0])
            if (
                not binary.is_absolute()
                or ".." in binary.parts
                or binary.name != "grype"
                or PurePosixPath(argv[2]) != binary.parent / "grype.json"
                or command.get("cwd") != "."
                or command.get("stdoutSha256") != scan_hash
            ):
                raise ValueError("release-evidence-scan-binding")
            scans += 1
        if (
            type(command.get("exitCode")) is not int
            or command["exitCode"] != (expected_exit if is_scan else 0)
            or command.get("processError") is not False
            or command.get("failureKind") is not None
            or command.get("admissionError") is not None
        ):
            raise ValueError("release-evidence-command-failed")
    if scans != 1:
        raise ValueError("release-evidence-scan-command-ambiguous")


def scan_release(root: Path, receipt: dict[str, object], output_directory: Path) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    environment = project_environment(os.environ)
    environment.update(
        {key: os.environ[key] for key in ("GH_TOKEN", "DOCKER_CONFIG") if key in os.environ}
    )
    facts: list[dict[str, object]] = []
    receipt["commands"] = facts

    def checked(*argv: str) -> str:
        result = execute(argv, root, environment)
        facts.append(command_fact(argv, result, cwd="."))
        return require_success(result)

    if checked("gh", "--version").splitlines()[:1] != ["gh version 2.101.0 (2026-09-15)"]:
        raise ValueError("attestation-cli-version")
    receipt["githubCliVersion"] = "2.101.0"
    page = checked(
        "gh",
        "api",
        f"repos/{REPOSITORY}/actions/workflows/release-artifact.yml/runs"
        "?branch=master&event=workflow_dispatch&status=success&per_page=100",
    )
    run = latest_release(strict_json(page), observed_at=now())
    discovery = checked(
        "docker", "buildx", "imagetools", "inspect", run.tag, "--format", "{{json .Manifest}}"
    )
    subject = registry_subject(strict_json(discovery))
    receipt["releasedSubject"] = {
        "image": subject,
        "platform": "linux/amd64",
        "sourceCommit": run.head_sha,
        "runId": run.id,
        "runAttempt": run.run_attempt,
        "runInvocation": run.invocation,
        "discoverySha256": digest(page),
    }
    common = (
        "gh",
        "attestation",
        "verify",
        f"oci://{subject}",
        "--deny-self-hosted-runners",
        "--repo",
        REPOSITORY,
        "--signer-digest",
        run.head_sha,
        "--signer-workflow",
        f"github.com/{REPOSITORY}/{WORKFLOW_PATH}",
        "--source-digest",
        run.head_sha,
        "--source-ref",
        "refs/heads/master",
        "--limit",
        "30",
    )
    repair_payloads = set()
    for transport in ((), ("--bundle-from-oci",)):
        verified = checked(
            *common,
            *transport,
            "--predicate-type",
            "https://slsa.dev/provenance/v1",
            "--format",
            "json",
        )
        admit_attestation(strict_json(verified), run, subject)
        checked(*common, *transport, "--predicate-type", "https://spdx.dev/Document/v2.3")
        repair_result = checked(
            *common, *transport, "--predicate-type", REPAIR_PREDICATE, "--format", "json"
        )
        repair_payloads.add(admit_repair_attestation(strict_json(repair_result), run, subject))
    if len(repair_payloads) != 1:
        raise ValueError("repair-attestation-stores-disagree")
    with tempfile.TemporaryDirectory(prefix="scheduled-advisories-") as directory:
        temporary = Path(directory)
        repair_path = temporary / "runtime-repairs.json"
        repair_path.write_bytes(repair_payloads.pop())
        manifest_path = temporary / "manifest.json"
        manifest_path.write_text(
            checked(
                "docker",
                "buildx",
                "imagetools",
                "inspect",
                subject,
                "--format",
                "{{json .Manifest}}",
            )
        )
        checked("docker", "pull", "--platform", "linux/amd64", subject)
        container = checked("docker", "create", "--platform", "linux/amd64", subject).strip()
        if re.fullmatch(r"[0-9a-f]{64}", container) is None:
            raise ValueError("image-inspection-container-identity")
        identity_path = temporary / "identity.json"
        try:
            checked(
                "docker",
                "cp",
                f"{container}:/app/backend/src/ci_coordinator/"
                "runtime_settings/resources/build-identity.v1.json",
                str(identity_path),
            )
            with identity_path.open("rb") as identity_file:
                receipt["releaseIdentity"] = admit_build_identity(identity_file.read(4097), run)
        finally:
            checked("docker", "rm", "--force", container)
        policy = Policy.model_validate(strict_json((root / POLICY_PATH).read_text()))
        if (policy.scannerReleaseUrl, policy.scannerReleaseSha256) != (RELEASE_URL, RELEASE_SHA256):
            raise ValueError("scanner-release-pin")
        archive = temporary / "grype.tar.gz"
        checked(
            "curl",
            "--silent",
            "--show-error",
            "--fail",
            "--location",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--tlsv1.2",
            "--max-time",
            "120",
            "--max-filesize",
            "67108864",
            "--output",
            str(archive),
            policy.scannerReleaseUrl,
        )
        if (
            archive.stat().st_size > 64 * 1024 * 1024
            or digest(archive.read_bytes()) != RELEASE_SHA256
        ):
            raise ValueError("scanner-release-checksum")
        checked("tar", "--extract", "--file", str(archive), "--directory", str(temporary), "grype")
        binary = temporary / "grype"
        cache = temporary / "database"
        config = temporary / "grype.json"
        write_receipt(
            config,
            {
                "db": {
                    "cache-dir": str(cache),
                    "update-url": "https://grype.anchore.io/databases/v6/latest.json",
                    "auto-update": False,
                    "validate-age": True,
                    "validate-by-hash-on-start": True,
                    "require-update-check": True,
                    "max-allowed-built-age": "24h",
                },
                "check-for-app-update": False,
                "only-fixed": False,
                "only-notfixed": False,
                "ignore-wontfix": "",
                "match-upstream-kernel-headers": True,
                "ignore": [],
                "exclude": [],
                "vex-documents": [],
                "vex-add": [],
                "distro": "",
            },
        )
        grype_environment: dict[str, str] = {
            key: environment[key]
            for key in ("PATH", "HOME", "TMPDIR", "DOCKER_CONFIG")
            if key in environment
        }

        def grype(arguments: tuple[str, ...], output: Path | None = None) -> int:
            argv = (str(binary), "-c", str(config), *arguments)
            result = execute(argv, root, grype_environment)
            facts.append(command_fact(argv, result, cwd="."))
            if result.error is not None or result.failure_kind is not None or result.status is None:
                raise ValueError("scanner-process-failed")
            if output is not None:
                output.write_text(result.stdout)
            return result.status

        update_exit = grype(("db", "update"))
        status_path = temporary / "database-status.json"
        status_exit = grype(("db", "status", "--output", "json"), status_path)
        scan_path = temporary / "scan.json"
        scan_exit = grype(
            (
                subject,
                "--from",
                "registry",
                "--platform",
                policy.platform,
                "--scope",
                policy.scope,
                "--output",
                "json",
                "--fail-on",
                "high",
            ),
            scan_path,
        )
        evidence = output_directory / "released-image-vulnerabilities.json"
        try:
            admit_release_vulnerability(
                policy_path=root / POLICY_PATH,
                scan_report_path=scan_path,
                database_status_path=status_path,
                manifest_inspection_path=manifest_path,
                evidence_output_path=evidence,
                subject=subject,
                platform=policy.platform,
                scanner_name="grype",
                scanner_version=policy.scannerVersion,
                scanner_release_url=policy.scannerReleaseUrl,
                scanner_release_sha256=policy.scannerReleaseSha256,
                scanner_binary_sha256=digest(binary.read_bytes()),
                scanner_exit_code=scan_exit,
                database_update_exit_code=update_exit,
                database_status_exit_code=status_exit,
                observed_at=now(),
                expected_db_cache_dir=str(cache),
                repair_policy_path=root / "docker/runtime/security/repaired-matches.v1.json",
                repair_evidence_path=repair_path,
                source_commit=run.head_sha,
                source_root=root,
            )
        finally:
            if evidence.is_file():
                receipt["vulnerabilityEvidenceSha256"] = digest(evidence.read_bytes())
        receipt["state"] = "passed"
