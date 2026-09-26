from collections.abc import Sequence

from alembic import op

from ci_coordinator.persistence.review_renewal_migration import retire_pending_review_authority

revision: str = "20260926_0016"
down_revision: str | None = "20260915_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    retire_pending_review_authority(op.get_bind())


def downgrade() -> None:
    raise RuntimeError("Review cutoff requires forward repair or an admitted restore")
