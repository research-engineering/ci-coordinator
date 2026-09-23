"""Canonical reusable-workflow call-graph values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Self

from ci_coordinator.kernel import hash_object, utf16_sort_key
from ci_coordinator.workflow_discovery._validation import (
    MAX_CALL_EDGES,
    MAX_TEXT_BYTES,
    require_exact_tuple,
    require_identifier,
    require_text,
    require_workflow_path,
)
from ci_coordinator.workflow_discovery.evidence import (
    Fact,
    Provenance,
    Unknown,
    require_canonical_evidence,
)

type CallKind = Literal["local", "remote", "dynamic", "unknown"]
type CallStatus = Literal[
    "resolved",
    "missing",
    "remote",
    "dynamic",
    "invalid",
    "cycle",
    "depth_exceeded",
]


@dataclass(frozen=True, slots=True)
class CallEdge:
    edge_id: str
    caller_workflow_path: str
    caller_job_id: str
    uses: str
    kind: CallKind
    status: CallStatus
    target_path: str | None
    remote_ref: str | None
    remote_ref_immutable: bool | None
    provenance: Provenance

    @classmethod
    def create(
        cls,
        *,
        caller_workflow_path: str,
        caller_job_id: str,
        uses: str,
        kind: CallKind,
        status: CallStatus,
        target_path: str | None,
        remote_ref: str | None,
        remote_ref_immutable: bool | None,
        provenance: Provenance,
    ) -> Self:
        edge_id = _call_edge_id(
            caller_workflow_path=caller_workflow_path,
            caller_job_id=caller_job_id,
            uses=uses,
            kind=kind,
            status=status,
            target_path=target_path,
            remote_ref=remote_ref,
            remote_ref_immutable=remote_ref_immutable,
            provenance=provenance,
        )
        return cls(
            edge_id=edge_id,
            caller_workflow_path=caller_workflow_path,
            caller_job_id=caller_job_id,
            uses=uses,
            kind=kind,
            status=status,
            target_path=target_path,
            remote_ref=remote_ref,
            remote_ref_immutable=remote_ref_immutable,
            provenance=provenance,
        )

    def __post_init__(self) -> None:
        require_identifier(self.edge_id, "edge:", "call edge id")
        require_workflow_path(self.caller_workflow_path)
        require_text(self.caller_job_id, "caller job id", maximum_bytes=128)
        require_text(self.uses, "call coordinate", maximum_bytes=MAX_TEXT_BYTES)
        if self.kind not in {"local", "remote", "dynamic", "unknown"}:
            raise ValueError("call kind is not admitted")
        if self.status not in {
            "resolved",
            "missing",
            "remote",
            "dynamic",
            "invalid",
            "cycle",
            "depth_exceeded",
        }:
            raise ValueError("call status is not admitted")
        if self.target_path is not None:
            require_workflow_path(self.target_path)
        if self.remote_ref is not None:
            require_text(self.remote_ref, "remote ref", maximum_bytes=256)
        if self.remote_ref_immutable is not None and type(self.remote_ref_immutable) is not bool:
            raise TypeError("remote ref immutability must be an exact boolean or absent")
        if type(self.provenance) is not Provenance:
            raise TypeError("call edge requires exact provenance")
        if (
            self.provenance.workflow_path != self.caller_workflow_path
            or self.provenance.location.path != "job.uses"
        ):
            raise ValueError("call edge provenance must identify its caller job.uses predicate")
        _require_call_shape(self)
        expected = _call_edge_id(
            caller_workflow_path=self.caller_workflow_path,
            caller_job_id=self.caller_job_id,
            uses=self.uses,
            kind=self.kind,
            status=self.status,
            target_path=self.target_path,
            remote_ref=self.remote_ref,
            remote_ref_immutable=self.remote_ref_immutable,
            provenance=self.provenance,
        )
        if self.edge_id != expected:
            raise ValueError("call edge id does not bind its canonical projection")

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "edgeId": self.edge_id,
            **_call_edge_projection(
                caller_workflow_path=self.caller_workflow_path,
                caller_job_id=self.caller_job_id,
                uses=self.uses,
                kind=self.kind,
                status=self.status,
                target_path=self.target_path,
                remote_ref=self.remote_ref,
                remote_ref_immutable=self.remote_ref_immutable,
                provenance=self.provenance,
            ),
        }


@dataclass(frozen=True, slots=True)
class GraphAnalysis:
    edges: tuple[CallEdge, ...]
    facts: tuple[Fact, ...]
    unknowns: tuple[Unknown, ...]
    local_graph_closed: bool

    def __post_init__(self) -> None:
        require_exact_tuple(self.edges, CallEdge, "call edges")
        if len(self.edges) > MAX_CALL_EDGES:
            raise ValueError("call graph exceeds its edge-count bound")
        edge_ids = tuple(edge.edge_id for edge in self.edges)
        if edge_ids != tuple(sorted(set(edge_ids), key=utf16_sort_key)):
            raise ValueError("call edges must have unique canonical ids")
        require_exact_tuple(self.facts, Fact, "graph facts")
        require_exact_tuple(self.unknowns, Unknown, "graph unknowns")
        require_canonical_evidence(self.facts, self.unknowns)
        if type(self.local_graph_closed) is not bool:
            raise TypeError("local graph closure must be an exact boolean")
        expected_closed = all(
            edge.kind == "remote" or (edge.kind == "local" and edge.status == "resolved")
            for edge in self.edges
        )
        if self.local_graph_closed != expected_closed:
            raise ValueError("local graph closure must be derived from every call edge")


def _call_edge_id(
    *,
    caller_workflow_path: str,
    caller_job_id: str,
    uses: str,
    kind: CallKind,
    status: CallStatus,
    target_path: str | None,
    remote_ref: str | None,
    remote_ref_immutable: bool | None,
    provenance: Provenance,
) -> str:
    return (
        "edge:"
        + hash_object(
            _call_edge_projection(
                caller_workflow_path=caller_workflow_path,
                caller_job_id=caller_job_id,
                uses=uses,
                kind=kind,
                status=status,
                target_path=target_path,
                remote_ref=remote_ref,
                remote_ref_immutable=remote_ref_immutable,
                provenance=provenance,
            )
        )[:32]
    )


def _call_edge_projection(
    *,
    caller_workflow_path: str,
    caller_job_id: str,
    uses: str,
    kind: CallKind,
    status: CallStatus,
    target_path: str | None,
    remote_ref: str | None,
    remote_ref_immutable: bool | None,
    provenance: Provenance,
) -> dict[str, object]:
    return {
        "callerWorkflowPath": caller_workflow_path,
        "callerJobId": caller_job_id,
        "uses": uses,
        "kind": kind,
        "status": status,
        "targetPath": target_path,
        "remoteRef": remote_ref,
        "remoteRefImmutable": remote_ref_immutable,
        "provenance": provenance.to_identity_mapping(),
    }


def _require_call_shape(edge: CallEdge) -> None:
    if edge.kind == "remote":
        if (
            edge.status != "remote"
            or edge.target_path is not None
            or edge.remote_ref is None
            or edge.remote_ref_immutable is None
        ):
            raise ValueError("remote call edge shape is inconsistent")
    elif edge.kind == "local":
        if (
            edge.target_path is None
            or edge.remote_ref is not None
            or edge.remote_ref_immutable is not None
            or edge.status not in {"resolved", "missing", "cycle", "depth_exceeded"}
        ):
            raise ValueError("local call edge shape is inconsistent")
    elif edge.kind == "dynamic":
        if (
            edge.status != "dynamic"
            or edge.target_path is not None
            or edge.remote_ref is not None
            or edge.remote_ref_immutable is not None
        ):
            raise ValueError("dynamic call edge shape is inconsistent")
    elif (
        edge.status != "invalid"
        or edge.target_path is not None
        or edge.remote_ref is not None
        or edge.remote_ref_immutable is not None
    ):
        raise ValueError("unknown call edge shape is inconsistent")
