"""Deterministic, non-authoritative workflow discovery proposal manifest."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, Self

from ci_coordinator.config_control import PolicyDiagnostic, ValidatedEpochDraft
from ci_coordinator.kernel import hash_object, sha256_hex, utf16_sort_key
from ci_coordinator.workflow_discovery._validation import (
    MAX_TEXT_BYTES,
    require_canonical_text_tuple,
    require_exact_tuple,
    require_identifier,
    require_sha256,
    require_text,
    require_workflow_path,
)
from ci_coordinator.workflow_discovery.report import DiscoveryReport

type ProposalState = Literal["reviewable", "blocked"]
type ProposalEvent = Literal["merge_group", "pull_request", "push"]

PROPOSAL_GENERATOR_VERSION: Final = "workflow-discovery-proposal/v2"
SUPPORTED_CI_EVENTS: Final[tuple[ProposalEvent, ...]] = (
    "merge_group",
    "pull_request",
    "push",
)
PROPOSAL_NON_CLAIMS: Final = tuple(
    sorted(
        (
            "proposal is not active policy",
            "proposal is not owner approval",
            "proposal is not omission authority",
            "proposal is not provider mutation authority",
            "proposal is not proof of FullCI completeness",
        ),
        key=utf16_sort_key,
    )
)


@dataclass(frozen=True, slots=True)
class ProposalManifest:
    manifest_id: str
    state: ProposalState
    generator_version: str
    inventory_digest: str
    selected_workflow_path: str | None
    selected_job_id: str | None
    selected_job_name: str | None
    selected_events: tuple[ProposalEvent, ...]
    policy_source: bytes | None
    admission: ValidatedEpochDraft | None
    diagnostics: tuple[PolicyDiagnostic, ...]
    blockers: tuple[str, ...]
    unknown_ids: tuple[str, ...]
    non_claims: tuple[str, ...]

    @classmethod
    def reviewable(
        cls,
        *,
        report: DiscoveryReport,
        workflow_path: str,
        job_id: str,
        job_name: str,
        selected_events: tuple[ProposalEvent, ...],
        policy_source: bytes,
        admission: ValidatedEpochDraft,
    ) -> Self:
        return cls._create(
            state="reviewable",
            report=report,
            selected_workflow_path=workflow_path,
            selected_job_id=job_id,
            selected_job_name=job_name,
            selected_events=selected_events,
            policy_source=policy_source,
            admission=admission,
            diagnostics=(),
            blockers=(),
        )

    @classmethod
    def blocked(
        cls,
        *,
        report: DiscoveryReport,
        blockers: tuple[str, ...],
        selected_workflow_path: str | None = None,
        selected_job_id: str | None = None,
        selected_job_name: str | None = None,
        selected_events: tuple[ProposalEvent, ...] = (),
        diagnostics: tuple[PolicyDiagnostic, ...] = (),
    ) -> Self:
        return cls._create(
            state="blocked",
            report=report,
            selected_workflow_path=selected_workflow_path,
            selected_job_id=selected_job_id,
            selected_job_name=selected_job_name,
            selected_events=selected_events,
            policy_source=None,
            admission=None,
            diagnostics=diagnostics,
            blockers=blockers,
        )

    @classmethod
    def _create(
        cls,
        *,
        state: ProposalState,
        report: DiscoveryReport,
        selected_workflow_path: str | None,
        selected_job_id: str | None,
        selected_job_name: str | None,
        selected_events: tuple[ProposalEvent, ...],
        policy_source: bytes | None,
        admission: ValidatedEpochDraft | None,
        diagnostics: tuple[PolicyDiagnostic, ...],
        blockers: tuple[str, ...],
    ) -> Self:
        ordered_blockers = tuple(sorted(set(blockers), key=utf16_sort_key))
        unknown_ids = tuple(unknown.unknown_id for unknown in report.unknowns)
        manifest_id = _proposal_id(
            state=state,
            generator_version=PROPOSAL_GENERATOR_VERSION,
            inventory_digest=report.inventory_digest,
            selected_workflow_path=selected_workflow_path,
            selected_job_id=selected_job_id,
            selected_job_name=selected_job_name,
            selected_events=selected_events,
            policy_source=policy_source,
            admission=admission,
            diagnostics=diagnostics,
            blockers=ordered_blockers,
            unknown_ids=unknown_ids,
            non_claims=PROPOSAL_NON_CLAIMS,
        )
        return cls(
            manifest_id=manifest_id,
            state=state,
            generator_version=PROPOSAL_GENERATOR_VERSION,
            inventory_digest=report.inventory_digest,
            selected_workflow_path=selected_workflow_path,
            selected_job_id=selected_job_id,
            selected_job_name=selected_job_name,
            selected_events=selected_events,
            policy_source=policy_source,
            admission=admission,
            diagnostics=diagnostics,
            blockers=ordered_blockers,
            unknown_ids=unknown_ids,
            non_claims=PROPOSAL_NON_CLAIMS,
        )

    def __post_init__(self) -> None:
        require_identifier(self.manifest_id, "proposal:", "proposal manifest id")
        if self.state not in {"reviewable", "blocked"}:
            raise ValueError("proposal state is not admitted")
        if self.generator_version != PROPOSAL_GENERATOR_VERSION:
            raise ValueError("proposal generator version is not admitted")
        require_sha256(self.inventory_digest, "proposal inventory digest")
        if self.selected_workflow_path is not None:
            require_workflow_path(self.selected_workflow_path)
        for field_name, value in (
            ("selected job id", self.selected_job_id),
            ("selected job name", self.selected_job_name),
        ):
            if value is not None:
                require_text(value, field_name, maximum_bytes=MAX_TEXT_BYTES)
        require_canonical_text_tuple(self.selected_events, "proposal events", allow_empty=True)
        if any(event not in SUPPORTED_CI_EVENTS for event in self.selected_events):
            raise ValueError("proposal event is not admitted")
        require_exact_tuple(self.diagnostics, PolicyDiagnostic, "proposal diagnostics")
        require_canonical_text_tuple(self.blockers, "proposal blockers", allow_empty=True)
        require_canonical_text_tuple(self.unknown_ids, "proposal unknown ids", allow_empty=True)
        require_canonical_text_tuple(self.non_claims, "proposal non-claims", allow_empty=False)
        if self.non_claims != PROPOSAL_NON_CLAIMS:
            raise ValueError("proposal non-claims must use the closed catalog")
        _require_proposal_state(self)
        expected = _proposal_id(
            state=self.state,
            generator_version=self.generator_version,
            inventory_digest=self.inventory_digest,
            selected_workflow_path=self.selected_workflow_path,
            selected_job_id=self.selected_job_id,
            selected_job_name=self.selected_job_name,
            selected_events=self.selected_events,
            policy_source=self.policy_source,
            admission=self.admission,
            diagnostics=self.diagnostics,
            blockers=self.blockers,
            unknown_ids=self.unknown_ids,
            non_claims=self.non_claims,
        )
        if self.manifest_id != expected:
            raise ValueError("proposal manifest id does not bind its canonical projection")

    @property
    def proposal_digest(self) -> str:
        """Return the complete content identity behind the compact manifest id."""
        return _proposal_digest(
            state=self.state,
            generator_version=self.generator_version,
            inventory_digest=self.inventory_digest,
            selected_workflow_path=self.selected_workflow_path,
            selected_job_id=self.selected_job_id,
            selected_job_name=self.selected_job_name,
            selected_events=self.selected_events,
            policy_source=self.policy_source,
            admission=self.admission,
            diagnostics=self.diagnostics,
            blockers=self.blockers,
            unknown_ids=self.unknown_ids,
            non_claims=self.non_claims,
        )


def _require_proposal_state(manifest: ProposalManifest) -> None:
    if manifest.state == "reviewable":
        if (
            type(manifest.policy_source) is not bytes
            or not manifest.policy_source
            or type(manifest.admission) is not ValidatedEpochDraft
            or manifest.diagnostics
            or manifest.blockers
            or manifest.selected_workflow_path is None
            or manifest.selected_job_id is None
            or manifest.selected_job_name is None
            or not manifest.selected_events
        ):
            raise ValueError("reviewable proposal must carry one admitted policy and selection")
        if manifest.policy_source != manifest.admission.source_bytes:
            raise ValueError("proposal source must equal the admitted source bytes")
    elif (
        manifest.policy_source is not None
        or manifest.admission is not None
        or not manifest.blockers
    ):
        raise ValueError("blocked proposal must carry blockers and no policy bytes")


def _proposal_id(
    *,
    state: ProposalState,
    generator_version: str,
    inventory_digest: str,
    selected_workflow_path: str | None,
    selected_job_id: str | None,
    selected_job_name: str | None,
    selected_events: tuple[ProposalEvent, ...],
    policy_source: bytes | None,
    admission: ValidatedEpochDraft | None,
    diagnostics: tuple[PolicyDiagnostic, ...],
    blockers: tuple[str, ...],
    unknown_ids: tuple[str, ...],
    non_claims: tuple[str, ...],
) -> str:
    return f"proposal:{
        _proposal_digest(
            state=state,
            generator_version=generator_version,
            inventory_digest=inventory_digest,
            selected_workflow_path=selected_workflow_path,
            selected_job_id=selected_job_id,
            selected_job_name=selected_job_name,
            selected_events=selected_events,
            policy_source=policy_source,
            admission=admission,
            diagnostics=diagnostics,
            blockers=blockers,
            unknown_ids=unknown_ids,
            non_claims=non_claims,
        )[:32]
    }"


def _proposal_digest(
    *,
    state: ProposalState,
    generator_version: str,
    inventory_digest: str,
    selected_workflow_path: str | None,
    selected_job_id: str | None,
    selected_job_name: str | None,
    selected_events: tuple[ProposalEvent, ...],
    policy_source: bytes | None,
    admission: ValidatedEpochDraft | None,
    diagnostics: tuple[PolicyDiagnostic, ...],
    blockers: tuple[str, ...],
    unknown_ids: tuple[str, ...],
    non_claims: tuple[str, ...],
) -> str:
    projection = {
        "state": state,
        "generatorVersion": generator_version,
        "inventoryDigest": inventory_digest,
        "selectedWorkflowPath": selected_workflow_path,
        "selectedJobId": selected_job_id,
        "selectedJobName": selected_job_name,
        "selectedEvents": list(selected_events),
        "policySourceHash": None if policy_source is None else sha256_hex(policy_source),
        "admittedEpochId": None if admission is None else admission.epoch_id,
        "diagnostics": [
            {
                "code": item.code,
                "phase": item.phase,
                "ruleId": item.rule_id,
                "instancePointer": item.instance_pointer,
            }
            for item in diagnostics
        ],
        "blockers": list(blockers),
        "unknownIds": list(unknown_ids),
        "nonClaims": list(non_claims),
    }
    return hash_object(projection)
