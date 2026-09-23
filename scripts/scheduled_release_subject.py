from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ci_coordinator.runtime_settings.build_identity import parse_build_identity
from scripts.release_predicate_admission import _timestamp
from scripts.release_publisher_identity import IMAGE, REPOSITORY, repository_id
from scripts.release_publisher_identity import WORKFLOW_PATH as WORKFLOW_PATH
from scripts.release_repair_admission import REPAIR_PREDICATE, RepairEvidence, evidence_bytes

SHA = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Positive = Annotated[int, Field(gt=0)]


class _Provider(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")


class Repository(_Provider):
    id: Positive
    full_name: str

    @model_validator(mode="after")
    def admitted_publisher(self) -> Self:
        if self.id != repository_id() or self.full_name != REPOSITORY:
            raise ValueError("release-repository-identity")
        return self


class ReleaseRun(_Provider):
    id: Positive
    run_attempt: Positive
    head_sha: SHA
    head_branch: Literal["master"]
    name: Literal["Release Artifact"]
    path: Literal[".github/workflows/release-artifact.yml"]
    event: Literal["workflow_dispatch"]
    status: Literal["completed"]
    conclusion: Literal["success"]
    created_at: str
    repository: Repository
    head_repository: Repository

    @property
    def invocation(self) -> str:
        return f"https://github.com/{REPOSITORY}/actions/runs/{self.id}/attempts/{self.run_attempt}"

    @property
    def tag(self) -> str:
        return f"{IMAGE}:run-{self.id}-{self.run_attempt}"


class RunPage(_Provider):
    total_count: Annotated[int, Field(ge=1)]
    workflow_runs: list[ReleaseRun] = Field(min_length=1, max_length=100)


def latest_release(value: object, *, observed_at: str) -> ReleaseRun:
    page = RunPage.model_validate(value)
    if len(page.workflow_runs) != min(page.total_count, 100):
        raise ValueError("release-discovery-page-incomplete")
    observed = _timestamp(observed_at, "observation")
    dates: list[datetime] = [
        _timestamp(run.created_at, "release created") for run in page.workflow_runs
    ]
    if any(value > observed for value in dates) or len(
        {run.id for run in page.workflow_runs}
    ) != len(dates):
        raise ValueError("release-discovery-invalid-order")
    if dates != sorted(dates, reverse=True):
        raise ValueError("release-discovery-not-newest-first")
    if len(dates) > 1 and dates[0] == dates[1]:
        raise ValueError("release-discovery-ambiguous-newest")
    return page.workflow_runs[0]


def registry_subject(value: object) -> str:
    if (
        not isinstance(value, dict)
        or value.get("mediaType") != "application/vnd.oci.image.index.v1+json"
    ):
        raise ValueError("released-subject-not-oci-index")
    digest = value.get("digest")
    if not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
        raise ValueError("released-subject-digest-invalid")
    return f"{IMAGE}@{digest}"


def admit_attestation(
    value: object,
    run: ReleaseRun,
    subject: str,
    *,
    predicate_type: str = "https://slsa.dev/provenance/v1",
) -> None:
    if re.fullmatch(re.escape(IMAGE) + r"@sha256:[0-9a-f]{64}", subject) is None:
        raise ValueError("release-attestation-subject-binding")
    if predicate_type not in {"https://slsa.dev/provenance/v1", REPAIR_PREDICATE}:
        raise ValueError("release-attestation-predicate")
    if not isinstance(value, list) or not 1 <= len(value) <= 30:
        raise ValueError("release-attestation-empty-or-unbounded")
    expected_extensions = {
        "runInvocationURI": run.invocation,
        "sourceRepositoryIdentifier": str(repository_id()),
        "sourceRepositoryURI": f"https://github.com/{REPOSITORY}",
        "sourceRepositoryDigest": run.head_sha,
        "sourceRepositoryRef": "refs/heads/master",
        "buildConfigURI": f"https://github.com/{REPOSITORY}/{WORKFLOW_PATH}@refs/heads/master",
        "buildConfigDigest": run.head_sha,
        "runnerEnvironment": "github-hosted",
    }
    for item in value:
        try:
            result = item["verificationResult"]
            extensions = result["signature"]["certificate"]
            statement = result["statement"]
            subjects = statement["subject"]
        except (KeyError, TypeError) as error:
            raise ValueError("release-attestation-shape") from error
        if not isinstance(extensions, dict) or any(
            extensions.get(key) != expected for key, expected in expected_extensions.items()
        ):
            raise ValueError("release-attestation-certificate-binding")
        if (
            not isinstance(statement, dict)
            or statement.get("predicateType") != predicate_type
            or subjects != [{"name": IMAGE, "digest": {"sha256": subject.rsplit(":", 1)[1]}}]
        ):
            raise ValueError("release-attestation-subject-binding")


def admit_repair_attestation(value: object, run: ReleaseRun, subject: str) -> bytes:
    admit_attestation(value, run, subject, predicate_type=REPAIR_PREDICATE)
    if not isinstance(value, list):
        raise ValueError("release-attestation-shape")
    payloads = set()
    for item in value:
        statement = item["verificationResult"]["statement"]
        evidence = RepairEvidence.model_validate(statement.get("predicate"))
        if evidence.subject != subject or evidence.sourceCommit != run.head_sha:
            raise ValueError("repair-attestation-subject")
        payloads.add(evidence_bytes(evidence))
    if len(payloads) != 1:
        raise ValueError("repair-attestation-ambiguous")
    return payloads.pop()


def admit_build_identity(content: bytes, run: ReleaseRun) -> str:
    identity = parse_build_identity(content)
    if not identity.production_eligible or identity.source_commit != run.head_sha:
        raise ValueError("release-build-identity-binding")
    return identity.release_identity


def strict_json(content: str) -> object:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate-provider-json-key")
            result[key] = value
        return result

    def nonfinite(_: str) -> object:
        raise ValueError("nonfinite-provider-json")

    return json.loads(content, object_pairs_hook=unique, parse_constant=nonfinite)
