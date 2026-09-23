import asyncio
from dataclasses import dataclass
from typing import Final, Protocol

from ci_coordinator.app.ci_economics import (
    CiEconomicsAuthorizer,
    CiEconomicsReadForbidden,
    CiEconomicsReadUnavailable,
)
from ci_coordinator.ci_economics.catalog import require_catalog_page
from ci_coordinator.ci_economics.observation_commands import (
    ConfigureObservation,
    ObservationCommitted,
    ObservationConflict,
    ObservationWriteResult,
)
from ci_coordinator.ci_economics.observation_gaps import (
    MAX_OBSERVATION_GAP_PAGE_SIZE,
    InvalidObservationGapCursor,
    ObservationGapCursor,
)
from ci_coordinator.ci_economics.observation_ports import (
    ObservationConfigurationStore,
    ObservationGapPage,
    ObservationQuery,
    ObservationStatus,
)
from ci_coordinator.ci_economics.observation_workflows import (
    ObservationWorkflowCatalog,
    ObservationWorkflowPage,
    require_workflow_page,
)
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.operator_controls.auth import RepositoryAccessUnavailable

type ObservationAccessFailure = CiEconomicsReadForbidden | CiEconomicsReadUnavailable
type ObservationConfigurationResult = ObservationWriteResult | ObservationAccessFailure
type ObservationStatusResult = ObservationStatus | ObservationAccessFailure


@dataclass(frozen=True, slots=True)
class ObservationCursorRejected:
    pass


type ObservationGapsResult = (
    ObservationGapPage | ObservationAccessFailure | ObservationCursorRejected
)
type ObservationWorkflowsResult = ObservationWorkflowPage | ObservationAccessFailure
OBSERVATION_WORKFLOW_DEADLINE_SECONDS: Final = 20


class CiObservationUseCase(Protocol):
    async def workflows(
        self, *, actor: str, scope: RepositoryScope, page_number: int
    ) -> ObservationWorkflowsResult: ...

    async def configure(self, command: ConfigureObservation) -> ObservationConfigurationResult: ...

    async def status(self, *, actor: str, scope: RepositoryScope) -> ObservationStatusResult: ...

    async def gaps(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        after_cursor: str | None,
        limit: int,
    ) -> ObservationGapsResult: ...


class CiObservationService:
    def __init__(
        self,
        *,
        authorizer: CiEconomicsAuthorizer,
        configuration_store: ObservationConfigurationStore,
        query: ObservationQuery,
        workflow_catalog: ObservationWorkflowCatalog,
    ) -> None:
        self._authorizer = authorizer
        self._configuration_store = configuration_store
        self._query = query
        self._workflow_catalog = workflow_catalog

    async def workflows(
        self, *, actor: str, scope: RepositoryScope, page_number: int
    ) -> ObservationWorkflowsResult:
        require_workflow_page(page_number)
        try:
            async with asyncio.timeout(OBSERVATION_WORKFLOW_DEADLINE_SECONDS):
                failure = await self._authorize(actor=actor, scope=scope)
                if failure is not None:
                    return failure
                result = await self._workflow_catalog.workflow_page(scope, page_number=page_number)
        except TimeoutError:
            return CiEconomicsReadUnavailable()
        if (
            type(result) is ObservationWorkflowPage
            and result.scope == scope
            and result.page_number == page_number
        ):
            return result
        return CiEconomicsReadUnavailable()

    async def configure(self, command: ConfigureObservation) -> ObservationConfigurationResult:
        if type(command) is not ConfigureObservation:
            raise TypeError("observation configuration requires an exact command")
        failure = await self._authorize(actor=command.actor, scope=command.scope)
        if failure is not None:
            return failure
        try:
            result = await self._configuration_store.configure_observation(command)
        except CiEconomicsStoreUnavailable:
            return CiEconomicsReadUnavailable()
        if type(result) is ObservationConflict:
            return result
        if type(result) is ObservationCommitted and (
            result.snapshot.scope == command.scope
            and result.snapshot.revision == command.expected_revision + 1
            and result.snapshot.configuration == command.configuration
        ):
            return result
        return CiEconomicsReadUnavailable()

    async def status(self, *, actor: str, scope: RepositoryScope) -> ObservationStatusResult:
        failure = await self._authorize(actor=actor, scope=scope)
        if failure is not None:
            return failure
        try:
            result = await self._query.observation_status(scope)
        except CiEconomicsStoreUnavailable:
            return CiEconomicsReadUnavailable()
        if type(result) is ObservationStatus and result.scope == scope:
            return result
        return CiEconomicsReadUnavailable()

    async def gaps(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        after_cursor: str | None,
        limit: int,
    ) -> ObservationGapsResult:
        require_catalog_page(scope, limit)
        if limit > MAX_OBSERVATION_GAP_PAGE_SIZE:
            raise ValueError("observation gap page exceeds its bound")
        failure = await self._authorize(actor=actor, scope=scope)
        if failure is not None:
            return failure
        try:
            cursor = None if after_cursor is None else ObservationGapCursor.parse(after_cursor)
            if cursor is not None and cursor.scope != scope:
                return ObservationCursorRejected()
            result = await self._query.observation_gaps(
                scope, after_cursor=after_cursor, limit=limit
            )
        except InvalidObservationGapCursor:
            return ObservationCursorRejected()
        except CiEconomicsStoreUnavailable:
            return CiEconomicsReadUnavailable()
        if (
            type(result) is ObservationGapPage
            and result.scope == scope
            and len(result.gaps) <= limit
            and all(cursor is None or gap.gap_id > cursor.gap_id for gap in result.gaps)
        ):
            return result
        return CiEconomicsReadUnavailable()

    async def _authorize(
        self, *, actor: str, scope: RepositoryScope
    ) -> ObservationAccessFailure | None:
        if type(scope) is not RepositoryScope:
            raise TypeError("observation operation requires an exact repository scope")
        try:
            allowed = await self._authorizer.allows_scope(actor=actor, scope=scope)
        except RepositoryAccessUnavailable:
            return CiEconomicsReadUnavailable()
        if type(allowed) is not bool:
            return CiEconomicsReadUnavailable()
        return None if allowed else CiEconomicsReadForbidden()
