from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.persistence.config_epoch_repository import _PostgresConfigEpochRepository
from ci_coordinator.persistence.proposal_review_repository import (
    _PostgresProposalReviewRepository,
)
from ci_coordinator.persistence.schema_capabilities import (
    proposal_review_registration_requirements,
)
from ci_coordinator.persistence.unit_of_work import PostgresUnitOfWork
from ci_coordinator.proposal_review import ProposalReviewStore


class PostgresProposalReviewUnitOfWork(PostgresUnitOfWork):
    """Expose review registration only after its complete capability is admitted."""

    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)
        self._required_capabilities = proposal_review_registration_requirements(self._profile)
        self._proposal_reviews: _PostgresProposalReviewRepository | None = None

    @property
    def proposal_reviews(self) -> ProposalReviewStore:
        self._require_active()
        return self._require_initialized(self._proposal_reviews, "proposal review repository")

    def _initialize_repositories(self) -> None:
        super()._initialize_repositories()
        connection = self._require_initialized(self._connection, "database connection")
        audit_events = self._require_initialized(self._audit_events, "audit event repository")
        config_epochs = _PostgresConfigEpochRepository(
            connection,
            audit_events,
            self._require_active,
            self._mark_rollback_required,
        )
        self._proposal_reviews = _PostgresProposalReviewRepository(
            connection,
            audit_events,
            config_epochs,
            self._require_active,
            self._mark_rollback_required,
        )
