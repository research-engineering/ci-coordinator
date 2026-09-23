from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Literal

from ci_coordinator.kernel import hash_object
from ci_coordinator.plan_issuance.execution_contract import (
    FullCiExecution,
    SelectedExecution,
    SignedExecution,
)

PLAN_REQUEST_SCHEMA_VERSION: Final[Literal["dynamic-ci-plan-request/v2"]] = (
    "dynamic-ci-plan-request/v2"
)
SIGNED_PLAN_PAYLOAD_SCHEMA_VERSION: Final[Literal["dynamic-ci-signed-plan-payload/v2"]] = (
    "dynamic-ci-signed-plan-payload/v2"
)
SIGNED_PLAN_ENVELOPE_SCHEMA_VERSION: Final[Literal["dynamic-ci-signed-plan-envelope/v1"]] = (
    "dynamic-ci-signed-plan-envelope/v1"
)
SIGNED_PLAN_ALGORITHM: Final[Literal["Ed25519"]] = "Ed25519"


@dataclass(frozen=True, slots=True)
class PlanRequest:
    schema_version: Literal["dynamic-ci-plan-request/v2"]
    request_id: str
    installation_id: int
    repository_id: int
    owner: str
    repository: str
    event_name: Literal["pull_request", "push", "merge_group"]
    ref: str
    base_sha: str
    head_sha: str
    workflow_run_id: int
    run_attempt: int
    execution_sha: str = field(kw_only=True)
    pull_request_number: int | None = None
    merge_group_head_ref: str | None = None

    def __post_init__(self) -> None:
        _require_plan_request_schema(self)
        _require_plan_request_scalars(self)
        _require_plan_request_optional_identity(self)
        _require_plan_request_event_identity(self)

    def identity_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "requestId": self.request_id,
            "installationId": self.installation_id,
            "repositoryId": self.repository_id,
            "owner": self.owner,
            "repository": self.repository,
            "eventName": self.event_name,
            "ref": self.ref,
            "baseSha": self.base_sha,
            "headSha": self.head_sha,
            "executionSha": self.execution_sha,
            "workflowRunId": self.workflow_run_id,
            "runAttempt": self.run_attempt,
            "pullRequestNumber": self.pull_request_number,
            "mergeGroupHeadRef": self.merge_group_head_ref,
        }


def _require_plan_request_schema(request: PlanRequest) -> None:
    if request.schema_version != PLAN_REQUEST_SCHEMA_VERSION:
        raise ValueError("plan request schema version is unsupported")


def _require_plan_request_scalars(request: PlanRequest) -> None:
    for identifier in (
        request.installation_id,
        request.repository_id,
        request.workflow_run_id,
        request.run_attempt,
    ):
        if type(identifier) is not int or identifier < 1:
            raise ValueError("plan request identifiers must be positive integers")
    for text in (request.request_id, request.owner, request.repository, request.ref):
        if type(text) is not str or not text:
            raise ValueError("plan request text fields must be non-empty")
    if request.event_name not in {"pull_request", "push", "merge_group"}:
        raise ValueError("plan request event is unsupported")
    for value in (request.base_sha, request.head_sha, request.execution_sha):
        if not _is_git_sha(value):
            raise ValueError("plan request git SHA is invalid")


def _require_plan_request_optional_identity(request: PlanRequest) -> None:
    if request.pull_request_number is not None and (
        type(request.pull_request_number) is not int or request.pull_request_number < 1
    ):
        raise ValueError("pull request number must be absent or positive")
    if request.merge_group_head_ref is not None and (
        type(request.merge_group_head_ref) is not str or not request.merge_group_head_ref
    ):
        raise ValueError("merge group head ref must be absent or non-empty")


def _require_plan_request_event_identity(request: PlanRequest) -> None:
    if request.event_name == "pull_request":
        _require_pull_request_identity(request)
    elif request.event_name == "merge_group":
        _require_merge_group_identity(request)
    elif request.pull_request_number is not None or request.merge_group_head_ref is not None:
        raise ValueError("push events cannot carry pull request or merge group identity")


def _require_pull_request_identity(request: PlanRequest) -> None:
    if request.pull_request_number is None:
        raise ValueError("pull request events require a pull request number")
    if request.ref != f"refs/pull/{request.pull_request_number}/merge":
        raise ValueError("pull request ref does not match its pull request number")
    if request.merge_group_head_ref is not None:
        raise ValueError("pull request events cannot carry merge group identity")


def _require_merge_group_identity(request: PlanRequest) -> None:
    if request.merge_group_head_ref is None:
        raise ValueError("merge group events require a merge group head ref")
    if request.pull_request_number is not None:
        raise ValueError("merge group events cannot carry pull request identity")


@dataclass(frozen=True, slots=True)
class RepositoryBinding:
    installation_id: int
    repository_id: int
    owner: str
    repository: str

    def __post_init__(self) -> None:
        if type(self.installation_id) is not int or self.installation_id < 1:
            raise ValueError("repository installation id must be positive")
        if type(self.repository_id) is not int or self.repository_id < 1:
            raise ValueError("repository id must be positive")
        if type(self.owner) is not str or not self.owner:
            raise ValueError("repository owner must be non-empty")
        if type(self.repository) is not str or not self.repository:
            raise ValueError("repository name must be non-empty")


@dataclass(frozen=True, slots=True)
class AuthenticatedRunBinding:
    issuer: str
    audience: str
    repository: str
    repository_id: int
    ref: str
    run_id: int
    run_attempt: int
    event_name: str
    workflow_ref: str | None
    workflow_sha: str | None
    job_workflow_ref: str | None
    job_workflow_sha: str | None
    check_run_id: str | None
    verified_at: datetime
    verifier_version: str
    claim_hash: str | None
    execution_sha: str = field(kw_only=True)

    def __post_init__(self) -> None:
        for text in (
            self.issuer,
            self.audience,
            self.repository,
            self.ref,
            self.execution_sha,
            self.event_name,
            self.verifier_version,
        ):
            if type(text) is not str or not text:
                raise ValueError("authenticated run text fields must be non-empty")
        for identifier in (self.repository_id, self.run_id, self.run_attempt):
            if type(identifier) is not int or identifier < 1:
                raise ValueError("authenticated run identifiers must be positive")
        if not _is_git_sha(self.execution_sha):
            raise ValueError("authenticated run execution SHA is invalid")
        if self.verified_at.tzinfo is None or self.verified_at.utcoffset() is None:
            raise ValueError("authenticated run verification time must be timezone-aware")

    def identity_mapping(self) -> dict[str, object]:
        return {
            "issuer": self.issuer,
            "audience": self.audience,
            "repository": self.repository,
            "repositoryId": self.repository_id,
            "ref": self.ref,
            "executionSha": self.execution_sha,
            "runId": self.run_id,
            "runAttempt": self.run_attempt,
            "eventName": self.event_name,
            "workflowRef": self.workflow_ref,
            "workflowSha": self.workflow_sha,
            "jobWorkflowRef": self.job_workflow_ref,
            "jobWorkflowSha": self.job_workflow_sha,
            "checkRunId": self.check_run_id,
            "verifiedAt": self.verified_at.isoformat(),
            "verifierVersion": self.verifier_version,
            "claimHash": self.claim_hash,
        }


def _is_git_sha(value: object) -> bool:
    return (
        type(value) is str
        and 40 <= len(value) <= 64
        and all(character in "0123456789abcdef" for character in value)
    )


@dataclass(frozen=True, slots=True)
class SignedPlanPayload:
    schema_version: Literal["dynamic-ci-signed-plan-payload/v2"]
    plan_id: str
    repository: RepositoryBinding
    request: PlanRequest
    authenticated_run: AuthenticatedRunBinding
    verified_plan_id: str | None
    production_admission_receipt_id: str | None
    execution: SignedExecution
    verifier_version: str | None
    fallback_reason: str | None

    def __post_init__(self) -> None:
        if self.schema_version != SIGNED_PLAN_PAYLOAD_SCHEMA_VERSION:
            raise ValueError("signed plan payload schema version is unsupported")
        if type(self.plan_id) is not str or not self.plan_id:
            raise ValueError("signed plan payload id must be non-empty")
        if self.verified_plan_id is None:
            if (
                self.verifier_version is not None
                or self.production_admission_receipt_id is not None
                or not self.fallback_reason
                or type(self.execution) is not FullCiExecution
                or self.execution.reason != self.fallback_reason
            ):
                raise ValueError("fallback signed plans require a reason and no verifier")
        elif (
            self.verified_plan_id != self.plan_id
            or type(self.production_admission_receipt_id) is not str
            or re.fullmatch(
                r"production_admission_[0-9a-f]{32}",
                self.production_admission_receipt_id,
            )
            is None
            or self.verifier_version is None
            or self.fallback_reason is not None
            or type(self.execution) is not SelectedExecution
            or self.execution.verified_plan_id != self.verified_plan_id
        ):
            raise ValueError("selected signed plans require the matching verified plan identity")

    def identity_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "planId": self.plan_id,
            "repository": {
                "installationId": self.repository.installation_id,
                "repositoryId": self.repository.repository_id,
                "owner": self.repository.owner,
                "repository": self.repository.repository,
            },
            "request": self.request.identity_mapping(),
            "authenticatedRun": self.authenticated_run.identity_mapping(),
            "verifiedPlanId": self.verified_plan_id,
            "productionAdmissionReceiptId": self.production_admission_receipt_id,
            "execution": self.execution.to_identity_mapping(),
            "verifierVersion": self.verifier_version,
            "fallbackReason": self.fallback_reason,
        }


@dataclass(frozen=True, slots=True)
class SignedPlanEnvelope:
    schema_version: Literal["dynamic-ci-signed-plan-envelope/v1"]
    key_id: str
    algorithm: Literal["Ed25519"]
    issued_at: datetime
    expires_at: datetime
    payload: SignedPlanPayload
    signature: str

    def unsigned_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "keyId": self.key_id,
            "algorithm": self.algorithm,
            "issuedAt": self.issued_at.isoformat(),
            "expiresAt": self.expires_at.isoformat(),
            "payload": self.payload.identity_mapping(),
        }


@dataclass(frozen=True, slots=True)
class IssuedPlanRecord:
    record_id: str
    idempotency_key: str
    request_hash: str
    envelope: SignedPlanEnvelope

    @classmethod
    def create(
        cls,
        idempotency_key: str,
        request: PlanRequest,
        envelope: SignedPlanEnvelope,
    ) -> IssuedPlanRecord:
        request_hash = hash_object(request.identity_mapping())
        return cls(
            record_id="issued_plan_"
            + hash_object({"idempotencyKey": idempotency_key, "requestHash": request_hash})[:32],
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            envelope=envelope,
        )


@dataclass(frozen=True, slots=True)
class Issued:
    record: IssuedPlanRecord
    duplicate: bool


@dataclass(frozen=True, slots=True)
class IssuanceRejected:
    reason: str


@dataclass(frozen=True, slots=True)
class IssuanceConflict:
    existing: IssuedPlanRecord
    attempted: IssuedPlanRecord


type IssuanceResult = Issued | IssuanceRejected | IssuanceConflict
