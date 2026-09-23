"""Canonical immutable identity for one reconciled GitHub Actions decision."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

from ci_coordinator.kernel import hash_object, is_safe_json_integer

RECONCILIATION_SUBJECT_SCHEMA_VERSION: Final[Literal["ci-reconciliation-subject/v1"]] = (
    "ci-reconciliation-subject/v1"
)
_EVENT_NAMES: Final = frozenset({"pull_request", "push", "merge_group"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40,64}$")


@dataclass(frozen=True, slots=True)
class ReconciliationSubject:
    """A content-addressed provider decision, never a caller-chosen opaque key."""

    schema_version: Literal["ci-reconciliation-subject/v1"]
    installation_id: int
    repository_id: int
    event_name: str
    ref: str
    base_sha: str
    head_sha: str
    workflow_run_id: int
    run_attempt: int
    subject_id: str

    def __post_init__(self) -> None:
        if self.schema_version != RECONCILIATION_SUBJECT_SCHEMA_VERSION:
            raise ValueError("reconciliation subject schema version is unsupported")
        for value, field_name in (
            (self.installation_id, "installation_id"),
            (self.repository_id, "repository_id"),
            (self.workflow_run_id, "workflow_run_id"),
            (self.run_attempt, "run_attempt"),
        ):
            if type(value) is not int or value < 1 or not is_safe_json_integer(value):
                raise ValueError(f"{field_name} must be a positive JSON safe integer")
        if self.event_name not in _EVENT_NAMES:
            raise ValueError("reconciliation subject event_name is unsupported")
        if type(self.ref) is not str or not self.ref:
            raise ValueError("reconciliation subject ref must be non-empty text")
        for sha, field_name in ((self.base_sha, "base_sha"), (self.head_sha, "head_sha")):
            if type(sha) is not str or _GIT_SHA.fullmatch(sha) is None:
                raise ValueError(f"reconciliation subject {field_name} is invalid")
        if type(self.subject_id) is not str or _SHA256.fullmatch(self.subject_id) is None:
            raise ValueError("reconciliation subject id must be a lowercase SHA-256 digest")
        if self.subject_id != hash_object(self.identity_mapping()):
            raise ValueError("reconciliation subject id does not match its canonical identity")

    @classmethod
    def create(
        cls,
        *,
        installation_id: int,
        repository_id: int,
        event_name: str,
        ref: str,
        base_sha: str,
        head_sha: str,
        workflow_run_id: int,
        run_attempt: int,
    ) -> ReconciliationSubject:
        identity = {
            "schemaVersion": RECONCILIATION_SUBJECT_SCHEMA_VERSION,
            "installationId": installation_id,
            "repositoryId": repository_id,
            "eventName": event_name,
            "ref": ref,
            "baseSha": base_sha,
            "headSha": head_sha,
            "workflowRunId": workflow_run_id,
            "runAttempt": run_attempt,
        }
        return cls(
            schema_version=RECONCILIATION_SUBJECT_SCHEMA_VERSION,
            installation_id=installation_id,
            repository_id=repository_id,
            event_name=event_name,
            ref=ref,
            base_sha=base_sha,
            head_sha=head_sha,
            workflow_run_id=workflow_run_id,
            run_attempt=run_attempt,
            subject_id=hash_object(identity),
        )

    def identity_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "installationId": self.installation_id,
            "repositoryId": self.repository_id,
            "eventName": self.event_name,
            "ref": self.ref,
            "baseSha": self.base_sha,
            "headSha": self.head_sha,
            "workflowRunId": self.workflow_run_id,
            "runAttempt": self.run_attempt,
        }
