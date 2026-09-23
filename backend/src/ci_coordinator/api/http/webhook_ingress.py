"""HTTP-shaped ingress contract for duplicate-capable GitHub webhook requests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from ci_coordinator.github_ingestion import IngestionResult
from ci_coordinator.identity_admission import RejectedIdentity

type WebhookHeaderLines = tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True, slots=True)
class WebhookAdmissionUnavailable:
    kind: Literal["admission-unavailable"] = field(default="admission-unavailable", init=False)


@dataclass(frozen=True, slots=True)
class WebhookPreparationOffered:
    kind: Literal["preparation-offered"] = field(default="preparation-offered", init=False)


type WebhookIngressResult = (
    IngestionResult | RejectedIdentity | WebhookAdmissionUnavailable | WebhookPreparationOffered
)


@dataclass(frozen=True, slots=True)
class WebhookIngressCommand:
    headers: WebhookHeaderLines
    raw_body: bytes

    def __post_init__(self) -> None:
        if type(self.headers) is not tuple or type(self.raw_body) is not bytes:
            raise TypeError("webhook ingress requires immutable headers and exact body bytes")
        names: list[str] = []
        for item in self.headers:
            if (
                type(item) is not tuple
                or len(item) != 2
                or type(item[0]) is not str
                or not item[0]
                or item[0] != item[0].lower()
                or type(item[1]) is not tuple
                or not item[1]
                or any(type(value) is not str for value in item[1])
            ):
                raise ValueError("webhook headers must be canonical duplicate-capable lines")
            names.append(item[0])
        if names != sorted(set(names)):
            raise ValueError("webhook header names must be sorted and unique")


class WebhookIngressUseCase(Protocol):
    async def __call__(self, command: WebhookIngressCommand, /) -> WebhookIngressResult: ...
