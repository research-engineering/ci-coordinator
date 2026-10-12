import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal

import pytest

from ci_coordinator.ci_economics.history_administration import (
    RepositoryCreationObserved,
    RepositoryCreationUnavailable,
)
from ci_coordinator.integrations.github.ci_history_repository_creation import (
    GitHubHistoryRepositoryCreation,
)
from ci_coordinator.integrations.github.contracts import (
    GitHubResponse,
    GitHubTransportFailure,
    GitHubTransportResult,
)

from ._economics_source_support import SCOPE, Provider, repository, response


def _read(
    *results: GitHubTransportResult,
) -> tuple[RepositoryCreationObserved | RepositoryCreationUnavailable, Provider]:
    provider = Provider(list(results))
    observed = asyncio.run(
        GitHubHistoryRepositoryCreation(provider).read_repository_created_at(SCOPE)
    )
    return observed, provider


def test_reads_one_scoped_repository_by_id_with_an_installation_binding() -> None:
    observed, provider = _read(response({**repository(), "created_at": "2019-03-04T12:34:56Z"}))
    assert observed == RepositoryCreationObserved(
        SCOPE, datetime(2019, 3, 4, 12, 34, 56, tzinfo=UTC)
    )
    assert provider.bindings == [(SCOPE.installation_id, SCOPE.repository_id)]
    assert [(request.method, request.operation, request.path) for request in provider.requests] == [
        ("GET", "repositories.get_by_id", f"/repositories/{SCOPE.repository_id}")
    ]


@pytest.mark.parametrize("raw", ["absent", None])
def test_absent_or_null_creation_metadata_is_observed_as_missing(raw: object) -> None:
    # Kills: inventing a fallback floor, or reporting missing metadata as unavailability.
    body = repository() if raw == "absent" else {**repository(), "created_at": raw}
    observed, _ = _read(response(body))
    assert observed == RepositoryCreationObserved(SCOPE, None)


@pytest.mark.parametrize(
    "created_at",
    ["2019-03-04", "2019-03-04T12:34:56+02:00", "2019-02-30T00:00:00Z", 1551702896, ""],
)
def test_malformed_creation_metadata_uses_the_shared_grammar(created_at: object) -> None:
    # Kills: decoder drift from the provider-inventory creation-date grammar.
    observed, _ = _read(response({**repository(), "created_at": created_at}))
    assert observed == RepositoryCreationUnavailable("provider_malformed")


def test_fractional_creation_metadata_keeps_its_exact_instant() -> None:
    observed, _ = _read(response({**repository(), "created_at": "2019-03-04T12:34:56.5Z"}))
    assert observed == RepositoryCreationObserved(
        SCOPE, datetime(2019, 3, 4, 12, 34, 56, 500000, tzinfo=UTC)
    )


@pytest.mark.parametrize(
    "result,reason",
    [
        (
            response({**repository(), "id": 999, "created_at": "2019-03-04T12:34:56Z"}),
            "provider_binding_mismatch",
        ),
        (response({**repository(), "id": True}), "provider_malformed"),
        (response(["not", "an", "object"]), "provider_malformed"),
        (
            replace(response({**repository(), "created_at": "2019-03-04T12:34:56Z"}), status=201),
            "provider_malformed",
        ),
        (replace(response({"message": "Not Found"}), status=404), "provider_unavailable"),
        (
            replace(
                response({**repository(), "created_at": "2019-03-04T12:34:56Z"}),
                api_version="2022-11-28",
            ),
            "provider_unavailable",
        ),
        (GitHubTransportFailure("timeout", "bounded provider timeout"), "provider_unavailable"),
    ],
)
def test_foreign_or_failed_provider_outcomes_are_unavailable(
    result: GitHubResponse | GitHubTransportFailure,
    reason: Literal["provider_unavailable", "provider_malformed", "provider_binding_mismatch"],
) -> None:
    # Kills: a missing repository-id binding check or treating provider failure as metadata.
    observed, _ = _read(result)
    assert observed == RepositoryCreationUnavailable(reason)
