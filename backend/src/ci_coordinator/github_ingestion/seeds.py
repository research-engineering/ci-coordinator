from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ci_coordinator.github_ingestion.provenance import GitHubRepository, WebhookProvenance


@dataclass(frozen=True, slots=True)
class ExactShaRange:
    kind: Literal["exact"] = field(default="exact", init=False)


@dataclass(frozen=True, slots=True)
class FullCiInvalidatingRange:
    reason_codes: tuple[str, ...]
    kind: Literal["full-ci-invalidating"] = field(default="full-ci-invalidating", init=False)

    def __post_init__(self) -> None:
        if not self.reason_codes:
            raise ValueError("full-CI-invalidating range requires at least one reason")
        if tuple(sorted(set(self.reason_codes))) != self.reason_codes:
            raise ValueError("full-CI-invalidating reasons must be sorted and unique")


type ShaRangeBinding = ExactShaRange | FullCiInvalidatingRange


@dataclass(frozen=True, slots=True)
class DynamicCiSeed:
    provenance: WebhookProvenance
    repository: GitHubRepository
    action: str | None
    base_sha: str
    head_sha: str
    range_binding: ShaRangeBinding


@dataclass(frozen=True, slots=True)
class PushSeed(DynamicCiSeed):
    ref: str
    event_name: Literal["push"] = field(default="push", init=False)


@dataclass(frozen=True, slots=True)
class PullRequestSeed(DynamicCiSeed):
    ref: str
    pull_request_number: int
    event_name: Literal["pull_request"] = field(default="pull_request", init=False)


@dataclass(frozen=True, slots=True)
class MergeGroupSeed(DynamicCiSeed):
    ref: str | None
    merge_group_head_ref: str | None
    event_name: Literal["merge_group"] = field(default="merge_group", init=False)
