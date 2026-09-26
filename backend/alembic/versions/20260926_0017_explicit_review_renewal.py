from collections.abc import Sequence

from alembic import op

from ci_coordinator.persistence.review_renewal_migration import expand_explicit_review_authority

revision: str = "20260926_0017"
down_revision: str | None = "20260926_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    expand_explicit_review_authority(op.get_bind())


def downgrade() -> None:
    raise RuntimeError("Review renewal requires forward repair or an admitted restore")
