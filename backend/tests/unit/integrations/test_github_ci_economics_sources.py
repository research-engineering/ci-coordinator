from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from ci_coordinator.ci_economics.discovery import ProviderRunDiscoveryPage, RunDiscoveryWindow
from ci_coordinator.ci_economics.ports import ProviderAttemptDeferred
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.ci_economics_admission import bound_economics_response
from ci_coordinator.integrations.github.ci_economics_sources import (
    GitHubCiEconomicsSources,
)
from ci_coordinator.integrations.github.contracts import (
    GitHubQueryParameter,
    GitHubRequest,
    GitHubSuccess,
    GitHubTransportFailure,
)

from ._economics_source_support import ATTEMPT, PATH, RUN_ID, SCOPE
from ._economics_source_support import Provider as _Provider
from ._economics_source_support import repository as _repository
from ._economics_source_support import response as _response
from ._economics_source_support import run as _run

REQUEST = GitHubRequest("actions.get_workflow_run_attempt", "GET", PATH, GITHUB_API_VERSION)


def _resolve(provider: _Provider) -> ProviderRunCollectionSource | ProviderAttemptDeferred:
    return asyncio.run(GitHubCiEconomicsSources(provider).resolve_attempt(SCOPE, RUN_ID, ATTEMPT))


def test_exact_source_needs_no_reconciliation_and_includes_failed_run() -> None:
    provider = _Provider([_response(_repository()), _response(_run()), _response(_run())])
    source = _resolve(provider)

    assert isinstance(source, ProviderRunCollectionSource)
    assert source.attempt.scope == SCOPE
    assert (source.attempt.workflow_run_id, source.attempt.run_attempt) == (RUN_ID, ATTEMPT)
    assert source.attempt.head_sha == "b" * 40
    assert source.run_created_at == datetime(2026, 9, 7, 12, tzinfo=UTC)
    assert source.provider_api_version == GITHUB_API_VERSION
    assert provider.installations == [SCOPE.installation_id]
    assert [request.path for request in provider.requests] == [
        "/repositories/202",
        PATH,
        "/repos/acme/service/actions/runs/303",
    ]
    assert provider.responses == []
    assert all(request.method == "GET" and not request.query for request in provider.requests)


@pytest.mark.parametrize(
    "field_name,changed",
    [
        ("status", "in_progress"),
        ("conclusion", None),
        ("updated_at", "2026-09-08T13:00:00Z"),
        ("run_started_at", "2026-09-08T12:00:00Z"),
        ("extra", {"future": True}),
    ],
)
def test_mutable_provider_fields_do_not_change_source(field_name: str, changed: object) -> None:
    original = _resolve(_Provider([_response(_repository()), _response(_run()), _response(_run())]))
    value = {**_run(), field_name: changed}
    modified = _resolve(_Provider([_response(_repository()), _response(value), _response(value)]))
    assert isinstance(original, ProviderRunCollectionSource)
    assert modified == original


@pytest.mark.parametrize(
    "field_name,changed",
    [
        ("head_sha", "c" * 40),
        ("created_at", "2026-09-07T12:00:01Z"),
    ],
)
def test_immutable_source_operands_change_evidence(field_name: str, changed: object) -> None:
    original = _resolve(_Provider([_response(_repository()), _response(_run()), _response(_run())]))
    modified = _resolve(
        _Provider(
            [
                _response(_repository()),
                _response({**_run(), field_name: changed}),
                _response({**_run(), field_name: changed}),
            ]
        )
    )
    assert isinstance(original, ProviderRunCollectionSource)
    assert isinstance(modified, ProviderRunCollectionSource)
    assert original.source_evidence_digest != modified.source_evidence_digest
    if field_name == "created_at":
        assert original.source_id == modified.source_id


@pytest.mark.parametrize(
    "field_name,changed",
    [
        ("repository", {"id": 203}),
        ("id", 304),
        ("run_attempt", 3),
    ],
)
def test_wrong_provider_identity_is_not_registered(field_name: str, changed: object) -> None:
    result = _resolve(
        _Provider([_response(_repository()), _response({**_run(), field_name: changed})])
    )
    assert result == ProviderAttemptDeferred("provider_binding_mismatch")


@pytest.mark.parametrize(
    "field_name", ["id", "run_attempt", "repository", "head_sha", "created_at"]
)
@pytest.mark.parametrize("edge", ["attempt", "run"])
def test_each_source_operand_is_required(field_name: str, edge: str) -> None:
    value = _run()
    del value[field_name]
    prefix = [_response(_repository())]
    if edge == "run":
        prefix.append(_response(_run()))
    assert _resolve(_Provider([*prefix, _response(value)])) == ProviderAttemptDeferred(
        "provider_malformed"
    )


@pytest.mark.parametrize(
    "field_name,changed",
    [
        ("id", True),
        ("id", 0),
        ("id", "303"),
        ("id", 2**53),
        ("run_attempt", None),
        ("run_attempt", 2.0),
        ("repository", {"id": True}),
        ("repository", {}),
        ("repository", []),
        ("head_sha", None),
        ("head_sha", "B" * 40),
        ("head_sha", "b" * 41),
        ("created_at", None),
        ("created_at", 0),
        ("created_at", "2026-02-30T12:00:00Z"),
        ("created_at", "2026-09-07T12:00:00"),
        ("created_at", "2026-09-07 12:00:00Z"),
        ("created_at", "20260907T120000Z"),
        ("created_at", "2026-09-07T12:00:00.1234567Z"),
    ],
)
@pytest.mark.parametrize("edge", ["attempt", "run"])
def test_malformed_source_facts_are_not_coerced(
    field_name: str, changed: object, edge: str
) -> None:
    prefix = [_response(_repository())]
    if edge == "run":
        prefix.append(_response(_run()))
    result = _resolve(_Provider([*prefix, _response({**_run(), field_name: changed})]))
    assert result == ProviderAttemptDeferred("provider_malformed")


@pytest.mark.parametrize("fraction", [".1", ".123", ".123456"])
def test_fractional_source_time_retains_precision(fraction: str) -> None:
    timestamp = f"2026-09-07T12:00:00{fraction}Z"
    result = _resolve(
        _Provider(
            [
                _response(_repository()),
                _response({**_run(), "created_at": timestamp}),
                _response({**_run(), "created_at": timestamp}),
            ]
        )
    )
    assert isinstance(result, ProviderRunCollectionSource)
    assert result.run_created_at == datetime.fromisoformat(timestamp)


@pytest.mark.parametrize(
    "body",
    [b"{}", b"[]", b'{"id":303,"id":303}', b"\xff", b" " * 1_048_577],
    ids=["empty", "array", "duplicate", "encoding", "oversize"],
)
@pytest.mark.parametrize("edge", ["attempt", "run"])
def test_malformed_or_oversized_body_is_rejected(body: bytes, edge: str) -> None:
    response = replace(_response(_run()), body=body)
    prefix = [_response(_repository())]
    if edge == "run":
        prefix.append(_response(_run()))
    assert _resolve(_Provider([*prefix, response])) == ProviderAttemptDeferred("provider_malformed")


@pytest.mark.parametrize("operand", ["id", "full_name", "owner"])
def test_repository_resolution_failure_stops_before_attempt_read(operand: str) -> None:
    value = _repository()
    value[operand] = {"id": 203, "full_name": "other/service", "owner": None}[operand]
    provider = _Provider([_response(value)])
    result = _resolve(provider)
    assert isinstance(result, ProviderAttemptDeferred)
    assert len(provider.requests) == 1


@pytest.mark.parametrize(
    "candidate",
    [
        replace(REQUEST, operation="actions.list_workflow_runs"),
        replace(REQUEST, method="POST"),
        replace(REQUEST, path=PATH + "/jobs"),
        replace(REQUEST, api_version="2022-11-28"),
        replace(REQUEST, query=(GitHubQueryParameter("page", "1"),)),
        replace(REQUEST, body=b"{}"),
    ],
    ids=["operation", "method", "path", "api-version", "query", "body"],
)
def test_source_read_checks_every_request_operand(candidate: GitHubRequest) -> None:
    outcome = GitHubSuccess(candidate, _response(_run()))
    assert bound_economics_response(
        outcome, operation=REQUEST.operation, path=PATH, api_version=GITHUB_API_VERSION
    ) == ProviderAttemptDeferred("provider_binding_mismatch")


@pytest.mark.parametrize("edge", [0, 1, 2], ids=["repository", "attempt", "run"])
def test_provider_failure_and_cancellation_do_not_become_sources(edge: int) -> None:
    prefix = [_response(_repository()), _response(_run())][:edge]
    unavailable = _Provider([*prefix, GitHubTransportFailure("unavailable", "offline")])
    assert _resolve(unavailable) == ProviderAttemptDeferred("provider_unavailable")
    cancelled = _Provider([*prefix, asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        _resolve(cancelled)


@pytest.mark.parametrize("delay", [timedelta(seconds=1), timedelta(days=30)])
def test_attempt_creation_does_not_renew_run_source_time(delay: timedelta) -> None:
    created = datetime(2026, 9, 7, 12, tzinfo=UTC)
    attempt = {**_run(), "created_at": (created + delay).isoformat().replace("+00:00", "Z")}
    provider = _Provider([_response(_repository()), _response(attempt), _response(_run())])
    result = _resolve(provider)
    assert isinstance(result, ProviderRunCollectionSource)
    assert result.run_created_at == created and result.attempt.run_attempt == ATTEMPT
    canonical = _resolve(
        _Provider([_response(_repository()), _response(_run()), _response(_run())])
    )
    assert result == canonical and len(provider.requests) == 3 and not provider.responses


def test_newer_active_rerun_does_not_replace_requested_attempt_identity() -> None:
    latest = {**_run(), "run_attempt": ATTEMPT + 1, "status": "in_progress", "conclusion": None}
    result = _resolve(_Provider([_response(_repository()), _response(_run()), _response(latest)]))
    assert isinstance(result, ProviderRunCollectionSource)
    assert result.attempt.run_attempt == ATTEMPT
    assert result.run_created_at == datetime(2026, 9, 7, 12, tzinfo=UTC)


def test_resolution_and_discovery_produce_equal_sources_for_the_same_attempt() -> None:
    created = datetime(2026, 9, 7, 12, tzinfo=UTC)
    latest = _run()
    attempt = {**latest, "created_at": "2026-09-08T12:00:00Z"}
    resolved = _resolve(
        _Provider([_response(_repository()), _response(attempt), _response(latest)])
    )
    provider = _Provider(
        [_response(_repository()), _response({"total_count": 1, "workflow_runs": [latest]})]
    )
    discovered = asyncio.run(
        GitHubCiEconomicsSources(provider).discover_page(
            SCOPE, RunDiscoveryWindow(created, created + timedelta(seconds=1)), page_number=1
        )
    )
    assert isinstance(resolved, ProviderRunCollectionSource)
    assert isinstance(discovered, ProviderRunDiscoveryPage)
    assert discovered.sources == (resolved,)
    assert len(provider.requests) == 2 and not provider.responses


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("repository", {"id": 203}, "provider_binding_mismatch"),
        ("id", 304, "provider_binding_mismatch"),
        ("head_sha", "c" * 40, "provider_binding_mismatch"),
        ("run_attempt", ATTEMPT - 1, "provider_binding_mismatch"),
        ("run_attempt", True, "provider_malformed"),
        ("run_attempt", 2.0, "provider_malformed"),
        ("created_at", None, "provider_malformed"),
        ("created_at", "2026-02-30T12:00:00Z", "provider_malformed"),
        ("created_at", "2026-09-07T12:00:00", "provider_malformed"),
    ],
)
def test_containing_run_must_independently_agree_with_exact_attempt(
    field: str, value: object, reason: str
) -> None:
    provider = _Provider(
        [_response(_repository()), _response(_run()), _response({**_run(), field: value})]
    )
    result = _resolve(provider)
    assert isinstance(result, ProviderAttemptDeferred) and result.reason == reason
    assert len(provider.requests) == 3 and not provider.responses


@pytest.mark.parametrize("ids", [(True, 1), (0, 1), (1, 0), (1, 2**53)])
def test_invalid_requested_identity_never_selects_credentials(ids: tuple[int, int]) -> None:
    provider = _Provider([])
    with pytest.raises(ValueError):
        asyncio.run(GitHubCiEconomicsSources(provider).resolve_attempt(SCOPE, *ids))
    assert not provider.installations and not provider.requests


def test_wrong_scope_type_never_selects_credentials() -> None:
    provider = _Provider([])
    with pytest.raises(TypeError):
        asyncio.run(
            GitHubCiEconomicsSources(provider).resolve_attempt(cast(RepositoryScope, None), 1, 1)
        )
    assert not provider.installations
