import re
from collections.abc import Mapping
from datetime import datetime
from typing import Literal, Self

from pydantic import model_validator

from ci_coordinator.api.http.model_contracts import ResponseModel
from ci_coordinator.ci_economics.archive_analytics_models import (
    AnalyticsQuery,
    AnalyticsReport,
    AnalyticsUnavailable,
)


class HistoryAnalyticsResponse(ResponseModel):
    outcome: Literal["available", "unavailable"]
    report: AnalyticsReport | None
    unavailable: AnalyticsUnavailable | None

    @model_validator(mode="after")
    def exclusive_outcome(self) -> Self:
        if self.outcome == "available":
            if self.report is None or self.unavailable is not None:
                raise ValueError("available analytics response requires only a report")
        elif self.report is not None or self.unavailable is None:
            raise ValueError("unavailable analytics response requires only its reason")
        return self


def parse_analytics_query(
    installation_id: str, repository_id: str, parameters: Mapping[str, str]
) -> AnalyticsQuery:
    fields = AnalyticsQuery.model_fields
    aliases = {field.alias or name: name for name, field in fields.items()}
    allowed = aliases.keys() - {"installationId", "repositoryId"}
    if parameters.keys() - allowed:
        raise ValueError("unknown analytics query parameter")
    numeric = {
        "installationId",
        "repositoryId",
        "generation",
        "workflowId",
        "horizonDays",
        "minimumDailySamples",
        "degradationRelativeBps",
        "degradationAbsoluteMs",
        "persistenceDays",
    }
    data: dict[str, object] = {}
    for key, value in {
        **parameters,
        "installationId": installation_id,
        "repositoryId": repository_id,
    }.items():
        if key in numeric:
            if re.fullmatch(r"[0-9]{1,16}", value) is None:
                raise ValueError("analytics integer parameter is invalid")
            data[key] = int(value)
        elif key in {"createdFrom", "createdUntil"}:
            data[key] = datetime.fromisoformat(value)
        else:
            data[key] = value
    return AnalyticsQuery.model_validate(data)


def analytics_openapi_parameters() -> list[dict[str, object]]:
    schema = AnalyticsQuery.model_json_schema()

    def inline(value: object, ancestors: frozenset[str] = frozenset()) -> object:
        if isinstance(value, list):
            return [inline(item, ancestors) for item in value]
        if not isinstance(value, dict):
            return value
        reference = value.get("$ref")
        if reference is not None:
            if (
                not isinstance(reference, str)
                or not reference.startswith("#/$defs/")
                or reference in ancestors
            ):
                raise ValueError("analytics parameter schema contains an unsupported reference")
            definition = schema["$defs"][reference.removeprefix("#/$defs/")]
            return inline(
                {**definition, **{key: item for key, item in value.items() if key != "$ref"}},
                ancestors | {reference},
            )
        return {key: inline(item, ancestors) for key, item in value.items()}

    return [
        {
            "name": name,
            "in": "query",
            "required": name in schema["required"],
            "schema": inline(value),
        }
        for name, value in schema["properties"].items()
        if name not in {"installationId", "repositoryId"}
    ]
