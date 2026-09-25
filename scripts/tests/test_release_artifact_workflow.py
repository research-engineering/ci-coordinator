from __future__ import annotations

from pathlib import Path
from typing import cast

from ruamel.yaml import YAML

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github/workflows/release-artifact.yml"

CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON = "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97"
SETUP_UV = "astral-sh/setup-uv@bec219d24cd3e171d82865faccec33120bb574f4"
SETUP_BUILDX = "docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069"
LOGIN = "docker/login-action@dbcb813823bdd20940b903addbd779551569679f"
BUILD_PUSH = "docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc"
ATTEST = "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6"
UPLOAD_ARTIFACT = "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
DOWNLOAD_ARTIFACT = "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"


def _workflow() -> dict[str, object]:
    value = YAML(typ="safe").load(WORKFLOW_PATH)
    assert type(value) is dict
    return cast(dict[str, object], value)


def _job(workflow: dict[str, object], name: str) -> dict[str, object]:
    jobs = workflow["jobs"]
    assert type(jobs) is dict
    job = jobs[name]
    assert type(job) is dict
    return cast(dict[str, object], job)


def _steps(job: dict[str, object]) -> list[dict[str, object]]:
    steps = job["steps"]
    assert type(steps) is list
    assert all(type(step) is dict for step in steps)
    return cast(list[dict[str, object]], steps)


def test_release_workflow_has_one_manual_authority_and_no_ambient_permissions() -> None:
    workflow = _workflow()

    assert workflow["name"] == "Release Artifact"
    assert workflow["on"] == {"workflow_dispatch": {}}
    assert workflow["permissions"] == {}
    assert workflow["concurrency"] == {
        "group": "release-artifact-${{ github.repository_id }}",
        "cancel-in-progress": False,
    }
    jobs = cast(dict[str, object], workflow["jobs"])
    assert set(jobs) == {"preflight", "build", "attest"}
    assert all("if" not in cast(dict[str, object], job) for job in jobs.values())


def test_preflight_binds_exact_repository_source_event_and_full_check() -> None:
    preflight = _job(_workflow(), "preflight")
    steps = _steps(preflight)

    assert preflight["permissions"] == {"actions": "read", "contents": "read"}
    assert [step.get("uses") for step in steps[:2]] == [CHECKOUT, SETUP_PYTHON]
    assert steps[0]["with"] == {
        "fetch-depth": 1,
        "persist-credentials": False,
        "ref": "${{ github.sha }}",
    }
    publisher = steps[2]
    assert publisher["id"] == "publisher"
    assert publisher["env"] == {
        "CI_COORDINATOR_RELEASE_REPOSITORY_ID": "${{ vars.CI_COORDINATOR_RELEASE_REPOSITORY_ID }}",
        "CI_COORDINATOR_FULL_CHECK_WORKFLOW_ID": (
            "${{ vars.CI_COORDINATOR_FULL_CHECK_WORKFLOW_ID }}"
        ),
    }
    assert publisher["run"] == (
        'set -euo pipefail\npython3 -m scripts.release_publisher_identity >> "${GITHUB_OUTPUT}"\n'
    )
    assert preflight["outputs"] == {
        "repository": "${{ steps.publisher.outputs.repository }}",
        "image": "${{ steps.publisher.outputs.image }}",
        "signer_workflow": "${{ steps.publisher.outputs.signer_workflow }}",
        "repair_predicate": "${{ steps.publisher.outputs.repair_predicate }}",
        "gate_run_attempt": "${{ steps.identity.outputs.gate_run_attempt }}",
        "gate_run_id": "${{ steps.identity.outputs.gate_run_id }}",
        "release_identity": "${{ steps.identity.outputs.release_identity }}",
        "source_commit": "${{ steps.identity.outputs.source_commit }}",
    }
    assert steps[3]["env"] == {
        "ACTUAL_EVENT": "${{ github.event_name }}",
        "ACTUAL_REF": "${{ github.ref }}",
        "ACTUAL_REPOSITORY": "${{ github.repository }}",
        "ACTUAL_REPOSITORY_ID": "${{ github.repository_id }}",
        "ACTUAL_SHA": "${{ github.sha }}",
        "ACTUAL_WORKFLOW_REF": "${{ github.workflow_ref }}",
        "ACTUAL_WORKFLOW_SHA": "${{ github.workflow_sha }}",
        "EXPECTED_REPOSITORY": "${{ steps.publisher.outputs.repository }}",
        "EXPECTED_REPOSITORY_ID": "${{ steps.publisher.outputs.repository_id }}",
        "EXPECTED_WORKFLOW_REF": "${{ steps.publisher.outputs.workflow_ref }}",
    }
    context_command = cast(str, steps[3]["run"])
    for required in (
        'test "${ACTUAL_EVENT}" = "workflow_dispatch"',
        'test "${ACTUAL_REF}" = "refs/heads/master"',
        'test "${ACTUAL_REPOSITORY}" = "${EXPECTED_REPOSITORY}"',
        'test "${ACTUAL_REPOSITORY_ID}" = "${EXPECTED_REPOSITORY_ID}"',
        'test "${ACTUAL_WORKFLOW_REF}" = "${EXPECTED_WORKFLOW_REF}"',
        'test "${ACTUAL_WORKFLOW_SHA}" = "${ACTUAL_SHA}"',
        'test "$(git rev-parse HEAD)" = "${ACTUAL_SHA}"',
    ):
        assert required in context_command
    identity_command = cast(str, steps[4]["run"])
    for required in (
        "X-GitHub-Api-Version: 2026-03-10",
        "/actions/workflows/${GATE_WORKFLOW_ID}/runs",
        "--raw-field event=workflow_dispatch",
        '--raw-field head_sha="${SOURCE_COMMIT}"',
        "--raw-field page=1",
        "--raw-field per_page=10",
        "--raw-field status=success",
        "python3 -m scripts.release_artifact_identity",
        "--gate-workflow-path .github/workflows/python-persistence.yml",
    ):
        assert required in identity_command
    assert "--paginate" not in identity_command
    assert steps[4]["env"] == {
        "GATE_WORKFLOW_ID": "${{ steps.publisher.outputs.gate_workflow_id }}",
        "GH_TOKEN": "${{ github.token }}",
        "RELEASE_RUN_ATTEMPT": "${{ github.run_attempt }}",
        "RELEASE_RUN_ID": "${{ github.run_id }}",
        "REPOSITORY": "${{ steps.publisher.outputs.repository }}",
        "REPOSITORY_ID": "${{ steps.publisher.outputs.repository_id }}",
        "SOURCE_COMMIT": "${{ github.sha }}",
    }


def test_build_uses_exact_source_and_registry_digest_is_the_only_authority() -> None:
    build = _job(_workflow(), "build")
    steps = _steps(build)

    assert build["permissions"] == {"contents": "read", "packages": "write"}
    assert build["needs"] == "preflight"
    assert cast(dict[str, object], build["env"])["IMAGE_NAME"] == (
        "${{ needs.preflight.outputs.image }}"
    )
    assert [step.get("uses") for step in steps] == [
        CHECKOUT,
        SETUP_PYTHON,
        SETUP_UV,
        SETUP_BUILDX,
        LOGIN,
        BUILD_PUSH,
        None,
        None,
        None,
        None,
        UPLOAD_ARTIFACT,
        UPLOAD_ARTIFACT,
        None,
    ]
    assert steps[0]["with"] == {
        "fetch-depth": 1,
        "persist-credentials": False,
        "ref": "${{ needs.preflight.outputs.source_commit }}",
    }
    assert steps[1]["with"] == {"python-version": "3.13.15"}
    assert steps[2]["with"] == {"enable-cache": False, "version": "0.12.17"}
    assert cast(dict[str, object], steps[3]["with"])["cache-binary"] is False
    build_inputs = cast(dict[str, object], steps[5]["with"])
    assert build_inputs["platforms"] == "linux/amd64"
    assert build_inputs["provenance"] == "mode=max,version=v1"
    assert build_inputs["pull"] is True
    assert build_inputs["push"] is True
    assert build_inputs["sbom"] == (
        "generator=docker/buildkit-syft-scanner@sha256:"
        "ae4f3b554449e7e25548e7d8ccc029d17357348e30c6e3df01b92bc93654d6a9"
    )
    assert build_inputs["github-token"] == ""
    assert build_inputs["tags"] == (
        "${{ env.IMAGE_NAME }}:run-${{ github.run_id }}-${{ github.run_attempt }}"
    )
    assert build["outputs"] == {
        "artifact_digest": "${{ steps.build.outputs.digest }}",
        "provenance_sha256": "${{ steps.predicates.outputs.provenance_sha256 }}",
        "sbom_sha256": "${{ steps.predicates.outputs.sbom_sha256 }}",
        "vulnerability_evidence_sha256": (
            "${{ steps.vulnerability.outputs.vulnerability_evidence_sha256 }}"
        ),
        "repair_evidence_sha256": "${{ steps.vulnerability.outputs.repair_evidence_sha256 }}",
    }
    grype_download = cast(str, steps[6]["run"])
    for required in (
        "GRYPE_RELEASE_SHA256",
        "sha256sum --check --strict",
    ):
        assert required in grype_download
    assert steps[6]["env"] == {
        "GRYPE_VERSION": "0.119.0",
        "GRYPE_RELEASE_URL": (
            "https://github.com/anchore/grype/releases/download/v0.119.0/"
            "grype_0.119.0_linux_amd64.tar.gz"
        ),
        "GRYPE_RELEASE_SHA256": (
            "3fa2dc4b924621ab65404cf08d0b8438d896d80ab949c9d5a4ca283c36004c9b"
        ),
    }

    assert steps[7]["name"] == "Provision the independent locked predicate validator"
    assert steps[8]["name"] == "Collect exact runtime repair evidence"
    assert "scripts.release_repair_evidence" in cast(str, steps[8]["run"])
    assert steps[9]["name"] == "Admit final-image vulnerability evidence"
    vulnerability = cast(str, steps[9]["run"])
    for required in (
        "--manifest-inspection",
        "--from registry",
        "--platform linux/amd64",
        "--scope squashed",
        "--fail-on high",
        '"max-allowed-built-age": "24h"',
        "backend/.venv/bin/python -c",
        '"auto-update": False',
        '"validate-age": True',
        '"validate-by-hash-on-start": True',
        '"require-update-check": True',
        '"match-upstream-kernel-headers": True',
        'env -i HOME="${HOME}" PATH="${PATH}"',
        '"${GRYPE_BIN}" -c "${grype_config}" "$@"',
        "scripts.release_vulnerability_admission",
        '--expected-db-cache-dir "${grype_cache}"',
        "--repair-policy docker/runtime/security/repaired-matches.v1.json",
        '--repair-evidence "${repair_evidence}"',
        '--source-commit "${SOURCE_COMMIT}"',
    ):
        assert required in vulnerability
    for forbidden in ("--only-fixed", "--only-notfixed", "--ignore", "--vex", "--exclude", "--db-"):
        assert forbidden not in vulnerability

    upload = steps[10]
    assert upload["uses"] == UPLOAD_ARTIFACT
    upload_with = cast(dict[str, object], upload["with"])
    assert upload_with["retention-days"] == 7
    assert upload["if"] == "always()"
    assert upload_with["if-no-files-found"] == "error"
    assert upload_with["path"] == "${{ runner.temp }}/release-vulnerability-evidence.json"

    provisioning = cast(str, steps[7]["run"])
    assert "uv sync" in provisioning
    assert "--frozen" in provisioning
    assert "--no-dev" in provisioning
    assert "--no-install-project" in provisioning

    assert steps[11]["uses"] == UPLOAD_ARTIFACT
    assert (
        cast(dict[str, object], steps[11]["with"])["path"]
        == "${{ runner.temp }}/release-runtime-repairs.json"
    )
    predicate_validation = cast(str, steps[12]["run"])
    for required in (
        "{{json .Provenance.SLSA}}",
        "{{json .SBOM.SPDX}}",
        "backend/.venv/bin/python -m scripts.release_predicate_admission",
        "--schema-directory docs/specs/ci-coordinator-release",
        '>> "${GITHUB_OUTPUT}"',
    ):
        assert required in predicate_validation


def test_attestation_requires_successful_build_qualification_without_failure_masking() -> None:
    workflow = _workflow()
    build = _job(workflow, "build")
    attest = _job(workflow, "attest")
    assert attest["needs"] == ["preflight", "build"]
    for job in (build, attest):
        assert "if" not in job
        assert "continue-on-error" not in job
        assert all("continue-on-error" not in step for step in _steps(job))

    qualifications = {
        "Collect exact runtime repair evidence",
        "Admit final-image vulnerability evidence",
        "Validate registry predicates without signing authority",
    }
    observed = {step["name"] for step in _steps(build) if step.get("name") in qualifications}
    assert observed == qualifications
    for step in _steps(build):
        if step.get("name") in qualifications:
            assert "if" not in step
    assert all("if" not in step for step in _steps(attest))


def test_attestation_job_has_no_checkout_and_never_executes_the_image() -> None:
    attest = _job(_workflow(), "attest")
    steps = _steps(attest)
    uses = [step.get("uses") for step in steps]

    assert attest["permissions"] == {
        "actions": "read",
        "artifact-metadata": "write",
        "attestations": "write",
        "contents": "read",
        "id-token": "write",
        "packages": "write",
    }
    assert attest["needs"] == ["preflight", "build"]
    environment = cast(dict[str, object], attest["env"])
    for name, output in (
        ("IMAGE_NAME", "image"),
        ("REPOSITORY", "repository"),
        ("SIGNER_WORKFLOW", "signer_workflow"),
        ("REPAIR_PREDICATE", "repair_predicate"),
    ):
        assert environment[name] == "${{ needs.preflight.outputs." + output + " }}"
    assert CHECKOUT not in uses
    assert SETUP_PYTHON not in uses
    assert uses == [
        SETUP_BUILDX,
        LOGIN,
        DOWNLOAD_ARTIFACT,
        DOWNLOAD_ARTIFACT,
        None,
        ATTEST,
        ATTEST,
        ATTEST,
        None,
    ]
    assert cast(dict[str, object], steps[0]["with"])["cache-binary"] is False

    download = steps[2]
    assert download["with"] == {
        "name": "${{ env.VULNERABILITY_ARTIFACT_NAME }}",
        "path": "${{ runner.temp }}/vulnerability-evidence",
    }

    inspection = cast(str, steps[4]["run"])
    assert "docker pull" in inspection
    assert "docker create" in inspection
    assert "docker cp" in inspection
    assert "docker run" not in inspection
    assert "cmp" in inspection
    assert "{{json .Provenance.SLSA}}" in inspection
    assert "{{json .SBOM.SPDX}}" in inspection
    assert '[[ "${PROVENANCE_SHA256}" =~ ^[0-9a-f]{64}$ ]]' in inspection
    assert '[[ "${SBOM_SHA256}" =~ ^[0-9a-f]{64}$ ]]' in inspection
    assert '[[ "${VULNERABILITY_EVIDENCE_SHA256}" =~ ^[0-9a-f]{64}$ ]]' in inspection
    assert "release-vulnerability-evidence.json" in inspection
    assert inspection.count("sha256sum") == 4
    assert "REPAIR_EVIDENCE_SHA256" in inspection

    provenance = cast(dict[str, object], steps[5]["with"])
    sbom = cast(dict[str, object], steps[6]["with"])
    assert provenance["create-storage-record"] is True
    assert provenance["push-to-registry"] is True
    assert "sbom-path" not in provenance
    assert sbom["create-storage-record"] is False
    assert sbom["push-to-registry"] is True
    assert sbom["sbom-path"] == "${{ runner.temp }}/sbom.spdx.json"

    repairs = cast(dict[str, object], steps[7]["with"])
    assert (
        repairs["predicate-path"]
        == "${{ runner.temp }}/repair-evidence/release-runtime-repairs.json"
    )
    assert repairs["push-to-registry"] is True
    assert repairs["predicate-type"] == "${{ env.REPAIR_PREDICATE }}"
    verification = cast(str, steps[8]["run"])
    for required in (
        "--deny-self-hosted-runners",
        '--repo "${REPOSITORY}"',
        '--signer-workflow "${SIGNER_WORKFLOW}"',
        '--signer-digest "${SOURCE_COMMIT}"',
        '--source-digest "${SOURCE_COMMIT}"',
        "--source-ref refs/heads/master",
        "--predicate-type https://slsa.dev/provenance/v1",
        "--predicate-type https://spdx.dev/Document/v2.3",
    ):
        assert required in verification
    assert verification.count("--bundle-from-oci") == 3
    assert verification.count("--predicate-type https://slsa.dev/provenance/v1") == 2
    assert verification.count("--predicate-type https://spdx.dev/Document/v2.3") == 2
    assert verification.count('--predicate-type "${REPAIR_PREDICATE}"') == 2


def test_shell_steps_are_fail_closed_except_the_exact_cleanup_command() -> None:
    workflow = _workflow()
    shell_steps = [
        cast(str, step["run"])
        for job_name in ("preflight", "build", "attest")
        for step in _steps(_job(workflow, job_name))
        if "run" in step
    ]

    assert "continue-on-error" not in WORKFLOW_PATH.read_text()
    for command in shell_steps:
        assert command.startswith("set -euo pipefail\n")
        masked_failures = [line.strip() for line in command.splitlines() if "|| true" in line]
        assert masked_failures in (
            [],
            ['docker rm --force "${container_id}" > /dev/null 2>&1 || true'],
        )


def test_every_external_action_is_immutable_and_current() -> None:
    workflow = _workflow()
    action_refs = [
        cast(str, step["uses"])
        for job_name in ("preflight", "build", "attest")
        for step in _steps(_job(workflow, job_name))
        if "uses" in step
    ]

    assert set(action_refs) == {
        CHECKOUT,
        SETUP_PYTHON,
        SETUP_UV,
        SETUP_BUILDX,
        LOGIN,
        BUILD_PUSH,
        UPLOAD_ARTIFACT,
        DOWNLOAD_ARTIFACT,
        ATTEST,
    }
    assert all(len(reference.rsplit("@", 1)[1]) == 40 for reference in action_refs)
