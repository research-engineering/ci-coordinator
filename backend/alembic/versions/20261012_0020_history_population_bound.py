from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ci_coordinator.persistence.compatibility_contracts import build_declaration
from ci_coordinator.persistence.compatibility_profile import load_bundled_profile
from ci_coordinator.persistence.migration_protocol import apply_forward_declaration
from ci_coordinator.persistence.migration_result_attestation import attest_resulting_capabilities
from ci_coordinator.persistence.review_renewal_migration import renewal_declarations
from ci_coordinator.persistence.schema_capabilities import (
    CI_HISTORY_POPULATION_BOUND,
    CONTROL_PLANE_LOGOUT_FENCE,
    REGISTERED_EPOCH_REVIEW,
)

revision: str = "20261012_0020"
down_revision: str | None = "20261011_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
_SCHEMA = "ci_coordinator"
_SAFE_INTEGER_MAX = 9_007_199_254_740_991
_TABLE = "ci_history_population_bounds"
_RENEWAL = renewal_declarations()[2]
_LOGOUT_FENCE = build_declaration(
    load_bundled_profile(),
    generation=_RENEWAL.generation + 1,
    revision_id="20261011_0018",
    parent_revision_id=_RENEWAL.revision_id,
    transition_kind="expand",
    capabilities=(*_RENEWAL.capabilities, CONTROL_PLANE_LOGOUT_FENCE.declaration()),
)
# Exactly the published 20261011_0019 registered-epoch review declaration.
_PREDECESSOR = build_declaration(
    load_bundled_profile(),
    generation=_LOGOUT_FENCE.generation + 1,
    revision_id="20261011_0019",
    parent_revision_id=_LOGOUT_FENCE.revision_id,
    transition_kind="expand",
    capabilities=(*_LOGOUT_FENCE.capabilities, REGISTERED_EPOCH_REVIEW.declaration()),
)
_SUCCESSOR = build_declaration(
    load_bundled_profile(),
    generation=_PREDECESSOR.generation + 1,
    revision_id=revision,
    parent_revision_id=_PREDECESSOR.revision_id,
    transition_kind="expand",
    capabilities=(*_PREDECESSOR.capabilities, CI_HISTORY_POPULATION_BOUND.declaration()),
)


def upgrade() -> None:
    # Only the relation is created: the runtime grant belongs to `database-access apply`
    # of the successor image, and no provenance is guessed for existing datasets.
    op.create_table(
        _TABLE,
        sa.Column("installation_id", sa.BigInteger, nullable=False),
        sa.Column("repository_id", sa.BigInteger, nullable=False),
        sa.Column("generation", sa.BigInteger, nullable=False),
        sa.Column("created_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("repository_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("configuration_revision", sa.BigInteger, nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"installation_id BETWEEN 1 AND {_SAFE_INTEGER_MAX} "
            f"AND repository_id BETWEEN 1 AND {_SAFE_INTEGER_MAX} "
            f"AND generation BETWEEN 1 AND {_SAFE_INTEGER_MAX} "
            f"AND configuration_revision BETWEEN 1 AND {_SAFE_INTEGER_MAX} "
            "AND isfinite(created_from) AND isfinite(recorded_at) "
            "AND created_from = date_trunc('second', created_from) "
            "AND created_from <= recorded_at",
            name="ck_ci_history_population_bounds_identity",
        ),
        sa.CheckConstraint(
            "source = 'explicit' AND repository_created_at IS NULL OR "
            "source = 'repository_created_at' AND repository_created_at IS NOT NULL",
            name="ck_ci_history_population_bounds_source",
        ),
        sa.CheckConstraint(
            "repository_created_at IS NULL OR "
            "isfinite(repository_created_at) AND repository_created_at <= recorded_at AND "
            "created_from = "
            "(date_trunc('day', repository_created_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')",
            name="ck_ci_history_population_bounds_catalog_floor",
        ),
        sa.PrimaryKeyConstraint(
            "installation_id",
            "repository_id",
            "generation",
            "created_from",
            name="pk_ci_history_population_bounds",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id", "repository_id"],
            [
                f"{_SCHEMA}.ci_history_datasets.installation_id",
                f"{_SCHEMA}.ci_history_datasets.repository_id",
            ],
            ondelete="RESTRICT",
            name="fk_ci_history_population_bounds_dataset",
        ),
        schema=_SCHEMA,
    )
    op.execute(sa.text(f"REVOKE ALL ON TABLE {_SCHEMA}.{_TABLE} FROM PUBLIC"))
    # PostgreSQL creates a separately privileged composite row type for every table.
    op.execute(sa.text(f"REVOKE ALL ON TYPE {_SCHEMA}.{_TABLE} FROM PUBLIC"))
    connection = op.get_bind()
    apply_forward_declaration(
        connection,
        load_bundled_profile(),
        previous_revision_id=_PREDECESSOR.revision_id,
        proposed=_SUCCESSOR,
        attest_resulting_capabilities=lambda admitted: attest_resulting_capabilities(
            connection, admitted
        ),
    )


def downgrade() -> None:
    raise RuntimeError("History population-bound provenance requires forward repair")
