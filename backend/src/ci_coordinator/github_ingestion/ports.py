from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, NoReturn, Protocol

from ci_coordinator.github_ingestion.events import DurableWorkflowObservation
from ci_coordinator.github_ingestion.provenance import WebhookProvenance


@dataclass(frozen=True, slots=True)
class DeliveryIdempotencyKey:
    delivery_id: str
    body_sha256: str


_PREPARED_DELIVERY_CLAIM_TOKEN = object()


class PreparedDeliveryClaim(DeliveryIdempotencyKey):
    """Immutable proof that a delivery key came from admitted provenance."""

    _event_name: str
    _verified_at: datetime
    _verifier_version: str
    __slots__ = ("_event_name", "_verified_at", "_verifier_version")

    def __init__(
        self,
        token: object,
        *,
        delivery_id: str,
        event_name: str,
        body_sha256: str,
        verified_at: datetime,
        verifier_version: str,
    ) -> None:
        if token is not _PREPARED_DELIVERY_CLAIM_TOKEN:
            raise TypeError("PreparedDeliveryClaim cannot be constructed directly")
        super().__init__(delivery_id=delivery_id, body_sha256=body_sha256)
        object.__setattr__(self, "_event_name", event_name)
        object.__setattr__(
            self,
            "_verified_at",
            verified_at,
        )
        object.__setattr__(self, "_verifier_version", verifier_version)

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        del kwargs
        raise TypeError("PreparedDeliveryClaim cannot be subclassed")

    def __copy__(self) -> NoReturn:
        raise TypeError("PreparedDeliveryClaim cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        del memo
        raise TypeError("PreparedDeliveryClaim cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("PreparedDeliveryClaim cannot be serialized")

    @property
    def key(self) -> DeliveryIdempotencyKey:
        return DeliveryIdempotencyKey(
            delivery_id=self.delivery_id,
            body_sha256=self.body_sha256,
        )

    @property
    def verified_at(self) -> datetime:
        return self._verified_at

    @property
    def event_name(self) -> str:
        return self._event_name

    @property
    def verifier_version(self) -> str:
        return self._verifier_version

    def matches_observation(self, observation: DurableWorkflowObservation) -> bool:
        if observation.kind != self.event_name:
            return False
        provenance = observation.provenance
        if type(provenance) is not WebhookProvenance:
            return False
        try:
            verified_at = _aware_utc(provenance.verified_at)
        except ValueError:
            return False
        return (
            provenance.delivery_id == self.delivery_id
            and provenance.event_name == self.event_name
            and provenance.body_sha256 == self.body_sha256
            and verified_at == self.verified_at
            and provenance.verifier_version == self.verifier_version
        )


def prepare_delivery_claim(provenance: WebhookProvenance) -> PreparedDeliveryClaim:
    if type(provenance) is not WebhookProvenance:
        raise TypeError("delivery claim requires exact webhook provenance")
    _bounded_text(provenance.delivery_id, 128, "delivery id")
    _bounded_text(provenance.event_name, 128, "event name")
    _bounded_text(provenance.verifier_version, 256, "verifier version")
    if (
        type(provenance.body_sha256) is not str
        or len(provenance.body_sha256) != 64
        or any(character not in "0123456789abcdef" for character in provenance.body_sha256)
    ):
        raise ValueError("delivery body hash must be a lowercase SHA-256 digest")
    verified_at = _aware_utc(provenance.verified_at)
    return PreparedDeliveryClaim(
        _PREPARED_DELIVERY_CLAIM_TOKEN,
        delivery_id=provenance.delivery_id,
        event_name=provenance.event_name,
        body_sha256=provenance.body_sha256,
        verified_at=verified_at,
        verifier_version=provenance.verifier_version,
    )


def _aware_utc(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("delivery claim verification time must be timezone-aware")
    return value.astimezone(UTC)


def _bounded_text(value: object, maximum: int, name: str) -> None:
    if type(value) is not str or not 1 <= len(value) <= maximum:
        raise ValueError(f"{name} is outside its admitted bound")


@dataclass(frozen=True, slots=True)
class DeliveryClaimed:
    key: DeliveryIdempotencyKey
    kind: Literal["claimed"] = field(default="claimed", init=False)


@dataclass(frozen=True, slots=True)
class DeliveryDuplicate:
    key: DeliveryIdempotencyKey
    kind: Literal["duplicate"] = field(default="duplicate", init=False)


@dataclass(frozen=True, slots=True)
class DeliveryConflict:
    delivery_id: str
    existing_body_sha256: str
    kind: Literal["conflict"] = field(default="conflict", init=False)


@dataclass(frozen=True, slots=True)
class DeliveryStoreUnavailable:
    kind: Literal["unavailable"] = field(default="unavailable", init=False)


type DeliveryIdempotencyResult = (
    DeliveryClaimed | DeliveryDuplicate | DeliveryConflict | DeliveryStoreUnavailable
)


class WebhookIngestionStore(Protocol):
    async def commit(
        self,
        key: PreparedDeliveryClaim,
        observation: DurableWorkflowObservation | None,
    ) -> DeliveryIdempotencyResult: ...
