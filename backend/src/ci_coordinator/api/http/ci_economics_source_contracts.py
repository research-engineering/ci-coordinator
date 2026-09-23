from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from ci_coordinator.api.http.ci_economics_contracts import CiEconomicsAttemptIdentityResponse
from ci_coordinator.api.http.model_contracts import RequestModel, ResponseModel
from ci_coordinator.ci_economics.discovery import (
    DISCOVERY_PAGE_SIZE,
    MAX_DISCOVERY_PAGES,
    DiscoveryPageTermination,
    ProviderRunDiscoveryPage,
    RunDiscoveryWindow,
)
from ci_coordinator.ci_economics.sources import (
    ProviderRunCollectionSource,
    ProviderSourceRegistrationResult,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

_INSTANT = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
_DIGEST = r"^[0-9a-f]{64}$"


class EconomicsSourceScopeBody(RequestModel):
    installation_id: int = Field(validation_alias="installationId", ge=1, le=MAX_SAFE_JSON_INTEGER)
    repository_id: int = Field(validation_alias="repositoryId", ge=1, le=MAX_SAFE_JSON_INTEGER)

    @property
    def scope(self) -> RepositoryScope:
        return RepositoryScope(self.installation_id, self.repository_id)


class EconomicsSourceRegistrationBody(EconomicsSourceScopeBody):
    workflow_run_id: int = Field(validation_alias="workflowRunId", ge=1, le=MAX_SAFE_JSON_INTEGER)
    run_attempt: int = Field(validation_alias="runAttempt", ge=1, le=MAX_SAFE_JSON_INTEGER)


class EconomicsSourceDiscoveryBody(EconomicsSourceScopeBody):
    created_from: str = Field(
        validation_alias="createdFrom", min_length=20, max_length=20, pattern=_INSTANT
    )
    created_through: str = Field(
        validation_alias="createdThrough", min_length=20, max_length=20, pattern=_INSTANT
    )
    page_number: int = Field(validation_alias="pageNumber", ge=1, le=MAX_DISCOVERY_PAGES)

    @property
    def window(self) -> RunDiscoveryWindow:
        return RunDiscoveryWindow(
            datetime.fromisoformat(self.created_from),
            datetime.fromisoformat(self.created_through),
        )

    @model_validator(mode="after")
    def admit_window(self) -> Self:
        _ = self.window
        return self


class EconomicsProviderSourceResponse(ResponseModel):
    source_kind: Literal["provider_run"] = Field(alias="sourceKind")
    source_id: str = Field(pattern=_DIGEST, min_length=64, max_length=64)
    attempt: CiEconomicsAttemptIdentityResponse
    run_created_at: datetime
    provider_api_version: str = Field(min_length=10, max_length=10)
    source_evidence_digest: str = Field(pattern=_DIGEST, min_length=64, max_length=64)


class EconomicsSourceDiscoveryResponse(ResponseModel):
    schema_version: Literal["ci-economics-source-discovery/v2"]
    ok: Literal[True]
    installation_id: int = Field(ge=1, le=MAX_SAFE_JSON_INTEGER)
    repository_id: int = Field(ge=1, le=MAX_SAFE_JSON_INTEGER)
    created_from: datetime
    created_through: datetime
    page_number: int = Field(ge=1, le=MAX_DISCOVERY_PAGES)
    provider_total: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)
    termination: DiscoveryPageTermination
    sources: tuple[EconomicsProviderSourceResponse, ...] = Field(max_length=DISCOVERY_PAGE_SIZE)


class EconomicsSourceRegistrationResponse(ResponseModel):
    schema_version: Literal["ci-economics-source-registration/v2"]
    source: EconomicsProviderSourceResponse
    outcome: ProviderSourceRegistrationResult


type EconomicsSourceErrorCode = Literal[
    "invalid_request",
    "unauthenticated",
    "forbidden",
    "unavailable",
    "overloaded",
    "provider_unavailable",
    "provider_binding_mismatch",
    "provider_malformed",
    "provider_incomplete",
    "provider_not_terminal",
    "provider_unstable",
]


class EconomicsSourceErrorResponse(ResponseModel):
    ok: Literal[False]
    error: EconomicsSourceErrorCode


def provider_source_response(
    source: ProviderRunCollectionSource,
) -> EconomicsProviderSourceResponse:
    attempt = source.attempt
    return EconomicsProviderSourceResponse(
        source_kind=source.kind,
        source_id=source.source_id,
        attempt=CiEconomicsAttemptIdentityResponse(
            installation_id=attempt.scope.installation_id,
            repository_id=attempt.scope.repository_id,
            workflow_run_id=attempt.workflow_run_id,
            run_attempt=attempt.run_attempt,
            head_sha=attempt.head_sha,
        ),
        run_created_at=source.run_created_at,
        provider_api_version=source.provider_api_version,
        source_evidence_digest=source.source_evidence_digest,
    )


def discovery_response(page: ProviderRunDiscoveryPage) -> EconomicsSourceDiscoveryResponse:
    return EconomicsSourceDiscoveryResponse(
        schema_version="ci-economics-source-discovery/v2",
        ok=True,
        installation_id=page.scope.installation_id,
        repository_id=page.scope.repository_id,
        created_from=page.window.created_from,
        created_through=page.window.created_through,
        page_number=page.page_number,
        provider_total=page.provider_total,
        termination=page.termination,
        sources=tuple(provider_source_response(source) for source in page.sources),
    )
