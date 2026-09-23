from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ci_coordinator.github_ingestion.events import (
    NormalizedWorkflowJobEvent,
    NormalizedWorkflowRunEvent,
)
from ci_coordinator.github_ingestion.provenance import GitHubRepository, WebhookProvenance
from ci_coordinator.github_ingestion.seeds import DynamicCiSeed


@dataclass(frozen=True, slots=True)
class IngestionRejection:
    code: str
    kind: Literal["rejected"] = field(default="rejected", init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not str or not self.code:
            raise ValueError("rejection code must be a non-empty string")


@dataclass(frozen=True, slots=True)
class UnsupportedWebhook:
    provenance: WebhookProvenance
    repository: GitHubRepository
    action: str | None
    reason_code: str
    kind: Literal["unsupported"] = field(default="unsupported", init=False)


@dataclass(frozen=True, slots=True)
class NoopDelivery:
    provenance: WebhookProvenance
    repository: GitHubRepository
    reason_code: str
    kind: Literal["noop"] = field(default="noop", init=False)


@dataclass(frozen=True, slots=True)
class PingDelivery:
    provenance: WebhookProvenance
    kind: Literal["ping"] = field(default="ping", init=False)


@dataclass(frozen=True, slots=True)
class DuplicateDelivery:
    provenance: WebhookProvenance
    kind: Literal["duplicate"] = field(default="duplicate", init=False)


@dataclass(frozen=True, slots=True)
class DeliveryUnavailable:
    provenance: WebhookProvenance
    kind: Literal["unavailable"] = field(default="unavailable", init=False)


@dataclass(frozen=True, slots=True)
class SeedIngestion:
    seed: DynamicCiSeed
    kind: Literal["seed"] = field(default="seed", init=False)


@dataclass(frozen=True, slots=True)
class WorkflowRunObservation:
    event: NormalizedWorkflowRunEvent
    kind: Literal["workflow-run-observation"] = field(
        default="workflow-run-observation",
        init=False,
    )


@dataclass(frozen=True, slots=True)
class WorkflowJobObservation:
    event: NormalizedWorkflowJobEvent
    kind: Literal["workflow-job-observation"] = field(
        default="workflow-job-observation",
        init=False,
    )
