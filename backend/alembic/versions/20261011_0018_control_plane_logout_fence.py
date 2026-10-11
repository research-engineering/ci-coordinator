from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ci_coordinator.persistence.compatibility_contracts import build_declaration
from ci_coordinator.persistence.compatibility_profile import load_bundled_profile
from ci_coordinator.persistence.migration_protocol import apply_forward_declaration
from ci_coordinator.persistence.migration_result_attestation import attest_resulting_capabilities
from ci_coordinator.persistence.review_renewal_migration import renewal_declarations
from ci_coordinator.persistence.schema_capabilities import CONTROL_PLANE_LOGOUT_FENCE

revision: str = "20261011_0018"
down_revision: str | None = "20260926_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
_SCHEMA = "ci_coordinator"
_TABLE = "control_plane_logout_fences"
_GENERATION_TABLE = "control_plane_logout_fence_generation"
_PREDECESSOR = renewal_declarations()[2]
_SUCCESSOR = build_declaration(
    load_bundled_profile(),
    generation=_PREDECESSOR.generation + 1,
    revision_id=revision,
    parent_revision_id=down_revision,
    transition_kind="expand",
    capabilities=(*_PREDECESSOR.capabilities, CONTROL_PLANE_LOGOUT_FENCE.declaration()),
)


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("issuer", sa.String(512), nullable=False),
        sa.Column("target_kind", sa.String(16), nullable=False),
        sa.Column("target_value", sa.String(512), nullable=False),
        sa.Column("generation", sa.BigInteger, nullable=False),
        sa.Column("fenced_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "octet_length(issuer) BETWEEN 1 AND 512",
            name="ck_control_plane_logout_fences_issuer",
        ),
        sa.CheckConstraint(
            "target_kind IN ('subject', 'sid')",
            name="ck_control_plane_logout_fences_target_kind",
        ),
        sa.CheckConstraint(
            "octet_length(target_value) BETWEEN 1 AND 512",
            name="ck_control_plane_logout_fences_target_value",
        ),
        sa.CheckConstraint(
            "generation BETWEEN 1 AND 9007199254740991",
            name="ck_control_plane_logout_fences_generation",
        ),
        sa.CheckConstraint(
            "isfinite(fenced_at)",
            name="ck_control_plane_logout_fences_fenced_at",
        ),
        sa.PrimaryKeyConstraint(
            "issuer",
            "target_kind",
            "target_value",
            name="pk_control_plane_logout_fences",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_control_plane_logout_fences_fenced_at",
        _TABLE,
        ["fenced_at", "issuer", "target_kind", "target_value"],
        schema=_SCHEMA,
    )
    op.create_table(
        _GENERATION_TABLE,
        sa.Column("singleton", sa.Boolean, nullable=False),
        sa.Column("current_generation", sa.BigInteger, nullable=False),
        sa.Column("deleted_generation_floor", sa.BigInteger, nullable=False),
        sa.CheckConstraint(
            "singleton IS TRUE",
            name="ck_control_plane_logout_fence_generation_singleton",
        ),
        sa.CheckConstraint(
            "deleted_generation_floor BETWEEN 0 AND current_generation "
            "AND current_generation <= 9007199254740991",
            name="ck_control_plane_logout_fence_generation_order",
        ),
        sa.PrimaryKeyConstraint("singleton", name="pk_control_plane_logout_fence_generation"),
        schema=_SCHEMA,
    )
    for table in (_TABLE, _GENERATION_TABLE):
        op.execute(sa.text(f"REVOKE ALL ON TABLE {_SCHEMA}.{table} FROM PUBLIC"))
        op.execute(sa.text(f"REVOKE ALL ON TYPE {_SCHEMA}.{table} FROM PUBLIC"))
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "INSERT INTO ci_coordinator.control_plane_logout_fence_generation "
            "(singleton, current_generation, deleted_generation_floor) VALUES (true, 0, 0)"
        )
    )
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
    raise RuntimeError("Control-plane logout fence requires forward repair or admitted restore")
