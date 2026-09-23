from dataclasses import dataclass
from typing import Final, Protocol

from ci_coordinator.ci_economics._observation_values import positive_id
from ci_coordinator.ci_economics.discovery import DiscoveryPageTermination
from ci_coordinator.ci_economics.ports import ProviderAttemptDeferred
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

OBSERVATION_WORKFLOW_PAGE_SIZE: Final = 100
MAX_OBSERVATION_WORKFLOW_PAGES: Final = 20


def require_workflow_page(page_number: int) -> None:
    if type(page_number) is not int or not 1 <= page_number <= MAX_OBSERVATION_WORKFLOW_PAGES:
        raise ValueError("workflow catalogue page exceeds its bound")


@dataclass(frozen=True, slots=True)
class ObservationWorkflowChoice:
    workflow_id: int
    name: str
    path: str
    state: str

    def __post_init__(self) -> None:
        positive_id(self.workflow_id, "workflow ID")
        for value, limit in ((self.name, 256), (self.path, 1024), (self.state, 64)):
            if (
                type(value) is not str
                or not value.strip()
                or len(value) > limit
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise ValueError("workflow display metadata must be bounded text")


@dataclass(frozen=True, slots=True)
class ObservationWorkflowPage:
    scope: RepositoryScope
    page_number: int
    provider_total: int
    workflows: tuple[ObservationWorkflowChoice, ...]
    termination: DiscoveryPageTermination

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("workflow catalogue requires an exact scope")
        require_workflow_page(self.page_number)
        if (
            type(self.provider_total) is not int
            or not 0 <= self.provider_total <= MAX_SAFE_JSON_INTEGER
        ):
            raise ValueError("workflow total must be a non-negative safe integer")
        if type(self.workflows) is not tuple or any(
            type(workflow) is not ObservationWorkflowChoice for workflow in self.workflows
        ):
            raise TypeError("workflow catalogue requires an exact tuple of choices")
        count = len(self.workflows)
        if (
            count > OBSERVATION_WORKFLOW_PAGE_SIZE
            or len({workflow.workflow_id for workflow in self.workflows}) != count
        ):
            raise ValueError("workflow page must contain at most100 unique identities")
        end_offset = (self.page_number - 1) * OBSERVATION_WORKFLOW_PAGE_SIZE + count
        if self.termination == "next_page":
            if (
                count != OBSERVATION_WORKFLOW_PAGE_SIZE
                or end_offset >= self.provider_total
                or self.page_number == MAX_OBSERVATION_WORKFLOW_PAGES
            ):
                raise ValueError("next workflow page needs a full page and remaining budget")
        elif self.termination == "exhausted":
            if end_offset != self.provider_total:
                raise ValueError("exhausted workflow page must match the observed total")
        elif self.termination != "truncated":
            raise ValueError("workflow page termination is unsupported")


class ObservationWorkflowCatalog(Protocol):
    async def workflow_page(
        self, scope: RepositoryScope, *, page_number: int
    ) -> ObservationWorkflowPage | ProviderAttemptDeferred: ...
