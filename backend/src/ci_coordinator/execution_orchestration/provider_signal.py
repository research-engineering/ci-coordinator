"""Derived provider signal identities and exact-run occurrences."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ci_coordinator.kernel import hash_object

_IDENTIFIER = re.compile(r"[a-z][a-z0-9._-]{0,63}")
_SHARD_ID = re.compile(r"ci_shard_[0-9a-f]{32}")
_JOB_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,127}")
_WORKFLOW_PATH = re.compile(r"\.github/workflows/[^/\\]+\.ya?ml")


@dataclass(frozen=True, slots=True)
class ProviderSignal:
    signal_id: str
    job_name: str
    kind: Literal["derived-shard", "declared-native"] = "derived-shard"
    execution_profile_id: str | None = None
    shard_id: str | None = None
    workflow_path: str | None = None
    job_id: str | None = None

    @classmethod
    def derive(cls, *, execution_profile_id: str, shard_id: str) -> ProviderSignal:
        signal_id, job_name = _derived_values(execution_profile_id, shard_id)
        return cls(
            signal_id=signal_id,
            job_name=job_name,
            execution_profile_id=execution_profile_id,
            shard_id=shard_id,
        )

    @classmethod
    def declared_native(
        cls,
        *,
        workflow_path: str,
        job_id: str,
        job_name: str,
    ) -> ProviderSignal:
        signal_id = _declared_signal_id(
            workflow_path=workflow_path,
            job_id=job_id,
            job_name=job_name,
        )
        return cls(
            signal_id=signal_id,
            job_name=job_name,
            kind="declared-native",
            workflow_path=workflow_path,
            job_id=job_id,
        )

    def __post_init__(self) -> None:
        if self.kind == "derived-shard":
            if self.workflow_path is not None or self.job_id is not None:
                raise ValueError("derived provider signals cannot carry workflow coordinates")
            if self.execution_profile_id is None or self.shard_id is None:
                raise ValueError("derived provider signals require shard coordinates")
            signal_id, job_name = _derived_values(self.execution_profile_id, self.shard_id)
            if self.signal_id != signal_id or self.job_name != job_name:
                raise ValueError("provider signal fields do not match their derived identity")
            return
        if self.kind != "declared-native":
            raise ValueError("provider signal kind is not admitted")
        if self.execution_profile_id is not None or self.shard_id is not None:
            raise ValueError("native provider signals cannot carry shard coordinates")
        if self.workflow_path is None or self.job_id is None:
            raise ValueError("native provider signals require exact workflow coordinates")
        signal_id = _declared_signal_id(
            workflow_path=self.workflow_path,
            job_id=self.job_id,
            job_name=self.job_name,
        )
        if self.signal_id != signal_id:
            raise ValueError("native provider signal fields do not match their declared identity")

    def to_identity_mapping(self) -> dict[str, str | None]:
        return {
            "signalId": self.signal_id,
            "executionProfileId": self.execution_profile_id,
            "shardId": self.shard_id,
            "jobName": self.job_name,
            "kind": self.kind,
            "workflowPath": self.workflow_path,
            "jobId": self.job_id,
        }


def _derived_values(execution_profile_id: str, shard_id: str) -> tuple[str, str]:
    if _IDENTIFIER.fullmatch(execution_profile_id) is None:
        raise ValueError("provider signal profile id is not canonical")
    if _SHARD_ID.fullmatch(shard_id) is None:
        raise ValueError("provider signal shard id is not canonical")
    digest = hash_object(
        {
            "schemaVersion": "provider-signal/v1",
            "executionProfileId": execution_profile_id,
            "shardId": shard_id,
        }
    )
    return (
        "provider_signal_" + digest[:32],
        f"ci/{execution_profile_id}/{digest[:20]}",
    )


def _declared_signal_id(
    *,
    workflow_path: str,
    job_id: str,
    job_name: str,
) -> str:
    if (
        type(workflow_path) is not str
        or _WORKFLOW_PATH.fullmatch(workflow_path) is None
        or len(workflow_path.encode("utf-8")) > 256
    ):
        raise ValueError("native provider signal workflow path is invalid")
    if _JOB_ID.fullmatch(job_id) is None:
        raise ValueError("native provider signal job id is invalid")
    if (
        type(job_name) is not str
        or not job_name
        or len(job_name.encode("utf-8")) > 256
        or any(0xD800 <= ord(character) <= 0xDFFF for character in job_name)
    ):
        raise ValueError("native provider signal job name is invalid")
    projection = {
        "schemaVersion": "declared-native-provider-signal/v1",
        "workflowPath": workflow_path,
        "jobId": job_id,
        "jobName": job_name,
    }
    identity = hash_object(projection)
    return "provider_signal_" + identity[:32]


@dataclass(frozen=True, slots=True)
class ProviderOccurrence:
    signal: ProviderSignal
    workflow_run_id: int
    run_attempt: int
    job_id: int

    def __post_init__(self) -> None:
        if type(self.signal) is not ProviderSignal:
            raise TypeError("provider occurrence requires an exact signal")
        for name, value in (
            ("workflow_run_id", self.workflow_run_id),
            ("run_attempt", self.run_attempt),
            ("job_id", self.job_id),
        ):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")

    def belongs_to(self, *, workflow_run_id: int, run_attempt: int) -> bool:
        return self.workflow_run_id == workflow_run_id and self.run_attempt == run_attempt
