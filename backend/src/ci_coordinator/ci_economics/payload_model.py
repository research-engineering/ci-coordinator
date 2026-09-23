"""Closed economics payloads shared by durable and HTTP admission."""

from typing import Annotated, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    ModelWrapValidatorHandler,
    ValidationInfo,
    model_validator,
)


def _json_tuple(value: object, info: ValidationInfo) -> object:
    return tuple(value) if info.mode == "json" and type(value) is list else value


type JsonTuple[T] = Annotated[tuple[T, ...], BeforeValidator(_json_tuple)]


class EconomicsPayloadModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
        validate_by_alias=True,
        validate_by_name=False,
        serialize_by_alias=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    @model_validator(mode="wrap")
    @classmethod
    def revalidate_instance_fields(
        cls, value: object, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        if isinstance(value, cls):
            fields = cls.model_fields
            stored = dict(iter(value))
            if stored.keys() - fields.keys():
                raise ValueError("economics payload instance contains undeclared fields")
            value = {fields[name].alias or name: item for name, item in stored.items()}
        return handler(value)
