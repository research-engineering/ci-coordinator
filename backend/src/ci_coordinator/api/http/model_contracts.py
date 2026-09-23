"""Owned Pydantic policies for public HTTP representation models."""

from __future__ import annotations

from typing import cast, final

from pydantic import AliasGenerator, BaseModel, ConfigDict, JsonValue
from pydantic.alias_generators import to_camel

__all__ = ["ProjectedResponseModel", "RequestModel", "ResponseModel"]

_RESPONSE_ALIASES = AliasGenerator(serialization_alias=to_camel)


class _WireModel(BaseModel):
    @final
    def to_wire_mapping(self) -> dict[str, JsonValue]:
        return cast(
            dict[str, JsonValue],
            self.model_dump(mode="json", warnings="error"),
        )


class RequestModel(_WireModel):
    """Strict wire-only admission for untrusted request objects."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        loc_by_alias=True,
        serialize_by_alias=True,
        strict=True,
        validate_by_alias=True,
        validate_by_name=False,
        validate_default=True,
    )


class ResponseModel(_WireModel):
    """Strict internal projection to canonical public JSON objects."""

    model_config = ConfigDict(
        alias_generator=_RESPONSE_ALIASES,
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        json_schema_serialization_defaults_required=True,
        loc_by_alias=False,
        revalidate_instances="always",
        serialize_by_alias=True,
        strict=True,
        validate_by_alias=False,
        validate_by_name=True,
        validate_default=True,
    )


class ProjectedResponseModel(ResponseModel):
    """Response projection that explicitly admits trusted object attributes."""

    model_config = ConfigDict(from_attributes=True)
