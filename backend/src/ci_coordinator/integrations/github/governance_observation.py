"""Bounded GitHub adapter for effective default-branch rules."""

from __future__ import annotations

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_observation import (
    MAX_GOVERNANCE_AGGREGATE_RULE_BYTES,
    MAX_GOVERNANCE_RULES,
    EffectiveGovernanceRule,
    GovernanceObservationUnavailable,
    GovernanceReadResult,
    GovernanceRepository,
    GovernanceState,
)
from ci_coordinator.integrations.github._rate_limits import retry_after_seconds
from ci_coordinator.integrations.github._routes import (
    GitHubPage,
    repository_id_path,
)
from ci_coordinator.integrations.github.app_transport import GitHubAppTransportFactory
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.contracts import (
    GitHubIncomplete,
    GitHubOutcome,
    GitHubQueryParameter,
    GitHubSuccess,
    GitHubUnavailable,
)
from ci_coordinator.integrations.github.governance_observation_client import (
    GovernanceObservationClient,
    effective_branch_rules_path,
)
from ci_coordinator.integrations.github.governance_observation_decoding import (
    DecodedGovernanceRepository,
    GovernanceDecodeFailure,
    decode_effective_rule_page,
    decode_governance_repository,
)
from ci_coordinator.integrations.github.installation_request_admission import (
    governance_observation_rules_path_is_admitted,
)
from ci_coordinator.integrations.github.reconciliation_observer_pagination import (
    next_page_number,
    terminal_pagination,
)

_MAX_REPOSITORY_JSON_BYTES = 1_048_576
_RULE_PAGE_SIZE = 100
_MAX_RULE_PAGES = 10


class GitHubGovernanceObservationReader:
    def __init__(
        self,
        transport_factory: GitHubAppTransportFactory,
        *,
        api_version: str = GITHUB_API_VERSION,
    ) -> None:
        if type(api_version) is not str or not api_version:
            raise ValueError("governance observation API version must be non-empty")
        self._transport_factory = transport_factory
        self._api_version = api_version

    async def read(self, *, scope: RepositoryScope) -> GovernanceReadResult:
        client = GovernanceObservationClient(
            self._transport_factory.for_installation(scope.installation_id),
            api_version=self._api_version,
        )
        repository = await self._repository(client, scope.repository_id)
        if isinstance(repository, GovernanceObservationUnavailable):
            return repository
        rules = await self._rules(client, repository)
        if isinstance(rules, GovernanceObservationUnavailable):
            return rules
        rebound = await self._repository(client, scope.repository_id)
        if isinstance(rebound, GovernanceObservationUnavailable):
            return rebound
        if rebound != repository:
            return GovernanceObservationUnavailable("provider_binding_mismatch")
        try:
            bound_repository = GovernanceRepository(
                scope=scope,
                owner_id=repository.owner_id,
                owner=repository.repository.owner,
                name=repository.repository.name,
                full_name=repository.full_name,
                default_branch=repository.default_branch,
            )
            return GovernanceState(
                repository=bound_repository,
                api_version=self._api_version,
                rules=tuple(sorted(rules, key=lambda rule: rule.canonical_json)),
            )
        except (TypeError, ValueError):
            return GovernanceObservationUnavailable("malformed_provider_response")

    async def _repository(
        self,
        client: GovernanceObservationClient,
        repository_id: int,
    ) -> DecodedGovernanceRepository | GovernanceObservationUnavailable:
        outcome = await client.get_repository(repository_id)
        if failure := _outcome_failure(
            outcome,
            operation="governance_observation.get_repository",
            path=repository_id_path(repository_id),
            query=(),
            api_version=self._api_version,
            paginated=False,
        ):
            return failure
        if not isinstance(outcome, GitHubSuccess):
            return GovernanceObservationUnavailable("provider_binding_mismatch")
        repository = decode_governance_repository(
            outcome.response.body,
            max_json_bytes=_MAX_REPOSITORY_JSON_BYTES,
        )
        if repository is None:
            return GovernanceObservationUnavailable("malformed_provider_response")
        if repository.repository_id != repository_id:
            return GovernanceObservationUnavailable("provider_binding_mismatch")
        return repository

    async def _rules(
        self,
        client: GovernanceObservationClient,
        repository: DecodedGovernanceRepository,
    ) -> tuple[EffectiveGovernanceRule, ...] | GovernanceObservationUnavailable:
        rules: list[EffectiveGovernanceRule] = []
        aggregate_bytes = 0
        page_number = 1
        path = effective_branch_rules_path(repository.repository, repository.default_branch)
        if not governance_observation_rules_path_is_admitted(path):
            return GovernanceObservationUnavailable("malformed_provider_response")
        while True:
            page = GitHubPage(page_number, _RULE_PAGE_SIZE)
            outcome = await client.list_effective_branch_rules(
                repository.repository,
                branch=repository.default_branch,
                page=page,
            )
            if failure := _outcome_failure(
                outcome,
                operation="governance_observation.list_effective_branch_rules",
                path=path,
                query=page.query(),
                api_version=self._api_version,
                paginated=True,
            ):
                return failure
            if not isinstance(outcome, GitHubSuccess | GitHubIncomplete):
                return GovernanceObservationUnavailable("provider_binding_mismatch")
            decoded = decode_effective_rule_page(outcome.response.body)
            if isinstance(decoded, GovernanceDecodeFailure):
                return GovernanceObservationUnavailable(decoded.reason)
            rules.extend(decoded)
            aggregate_bytes += sum(len(rule.canonical_json) for rule in decoded)
            if (
                len(rules) > MAX_GOVERNANCE_RULES
                or aggregate_bytes > MAX_GOVERNANCE_AGGREGATE_RULE_BYTES
            ):
                return GovernanceObservationUnavailable("observation_limit_exceeded")
            if isinstance(outcome, GitHubSuccess):
                if not terminal_pagination(outcome.response.pagination):
                    return GovernanceObservationUnavailable("malformed_provider_response")
                break
            next_page = next_page_number(
                outcome.response.pagination,
                expected_path=path,
                current_page=page_number,
                page_size=_RULE_PAGE_SIZE,
                repository_id=repository.repository_id,
            )
            if next_page is None:
                return GovernanceObservationUnavailable("malformed_provider_response")
            if page_number >= _MAX_RULE_PAGES:
                return GovernanceObservationUnavailable("observation_limit_exceeded")
            page_number = next_page
        canonical = tuple(rule.canonical_json for rule in rules)
        if len(canonical) != len(set(canonical)):
            return GovernanceObservationUnavailable("malformed_provider_response")
        return tuple(rules)


def _outcome_failure(
    outcome: GitHubOutcome,
    *,
    operation: str,
    path: str,
    query: tuple[GitHubQueryParameter, ...],
    api_version: str,
    paginated: bool,
) -> GovernanceObservationUnavailable | None:
    if isinstance(outcome, GitHubUnavailable):
        return _unavailable(outcome)
    if not isinstance(outcome, GitHubSuccess | GitHubIncomplete):
        return GovernanceObservationUnavailable("provider_binding_mismatch")
    request = outcome.request
    if (
        request.operation != operation
        or request.method != "GET"
        or request.path != path
        or request.api_version != api_version
        or request.query != query
        or request.body is not None
        or (not paginated and isinstance(outcome, GitHubIncomplete))
    ):
        return GovernanceObservationUnavailable("provider_binding_mismatch")
    if not paginated and not terminal_pagination(outcome.response.pagination):
        return GovernanceObservationUnavailable("malformed_provider_response")
    return None


def _unavailable(outcome: GitHubUnavailable) -> GovernanceObservationUnavailable:
    failure = outcome.failure
    if failure.kind == "rate_limited":
        return GovernanceObservationUnavailable(
            "rate_limited",
            retry_after_seconds(failure),
        )
    if failure.kind == "not_found":
        return GovernanceObservationUnavailable("not_found")
    if failure.kind in {
        "missing_api_version_provenance",
        "api_version_provenance_mismatch",
    }:
        return GovernanceObservationUnavailable("provider_binding_mismatch")
    return GovernanceObservationUnavailable("unavailable")
