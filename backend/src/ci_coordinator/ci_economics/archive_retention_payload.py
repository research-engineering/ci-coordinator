from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from ci_coordinator.ci_economics.archive_retention import (
    MAX_ARCHIVE_DETAIL_DAYS,
    DetailRetentionPolicy,
)
from ci_coordinator.ci_economics.payload_model import EconomicsPayloadModel


class DisabledDetailRetentionPayload(EconomicsPayloadModel):
    mode: Literal["disabled"]

    def to_policy(self) -> DetailRetentionPolicy:
        return DetailRetentionPolicy(self.mode)


class ForeverDetailRetentionPayload(EconomicsPayloadModel):
    mode: Literal["forever"]

    def to_policy(self) -> DetailRetentionPolicy:
        return DetailRetentionPolicy(self.mode)


class FiniteDetailRetentionPayload(EconomicsPayloadModel):
    mode: Literal["days"]
    days: int = Field(ge=1, le=MAX_ARCHIVE_DETAIL_DAYS)
    anchor: Literal["first_successful_detail_import"]

    def to_policy(self) -> DetailRetentionPolicy:
        return DetailRetentionPolicy(self.mode, self.days)


type DetailRetentionPayload = Annotated[
    DisabledDetailRetentionPayload | ForeverDetailRetentionPayload | FiniteDetailRetentionPayload,
    Field(discriminator="mode"),
]

DETAIL_RETENTION_ADAPTER = TypeAdapter[DetailRetentionPayload](DetailRetentionPayload)
