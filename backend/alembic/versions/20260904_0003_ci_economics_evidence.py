"""Add bounded CI economics evidence and terminal attempt snapshots."""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

from ci_coordinator.persistence.compatibility_contracts import (
    CapabilityDeclaration,
    RevisionDeclaration,
)
from ci_coordinator.persistence.compatibility_profile import load_bundled_profile
from ci_coordinator.persistence.migration_protocol import (
    apply_forward_declaration,
    validate_pre_retention_downgrade,
)
from ci_coordinator.persistence.migration_result_attestation import (
    attest_resulting_capabilities,
)

revision: str = "20260904_0003"
_DOWN_REVISION: Final = "20260901_0002"
down_revision: str | None = _DOWN_REVISION
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA: Final = "ci_coordinator"
_SAFE_INTEGER_MAX: Final = 9_007_199_254_740_991
_PREDECESSOR_CAPABILITIES: Final = (
    CapabilityDeclaration(
        "audit-ledger/v1",
        "fe3386e46ad68c320bdf906cc242ddb9d6ad7f3a211ae39550ef9d973dbe0d76",
    ),
    CapabilityDeclaration(
        "config-epoch-lifecycle/v1",
        "32b47f8c1cd882db591841227bb567ce85dc7a3f9d2b35fa89b8c00f0c1af23c",
    ),
    CapabilityDeclaration(
        "config-epoch-registration-operations/v1",
        "20ac999afe91574c95e910602f74ff8352b6d5adab31c3f0a7120e14225b178b",
    ),
    CapabilityDeclaration(
        "control-plane-identity-state/v1",
        "3e0eca4e06e8665a4f545fc36fc87f2f5debd205da0a7fac8942a0a2477b8194",
    ),
    CapabilityDeclaration(
        "database-compatibility-protocol/v1",
        "e23360125c88fb7fc1dd9e13ae51f283f7a40e496aee548c71efe51da1a0965f",
    ),
    CapabilityDeclaration(
        "governance-baseline-state/v1",
        "fcd08237e4849e63aae5c24a33ebca0a64812795572a0f708b969e76d225e024",
    ),
    CapabilityDeclaration(
        "operator-override-state/v1",
        "402d4fe7fc73e871d385c9e00cde93c34bf6d5a7fdef6d3f62c27554585148e0",
    ),
    CapabilityDeclaration(
        "proposal-review-registration/v1",
        "a02e8cb4121864bcc8e497409704c575ce8eb3ba28d8340fec7020048703e0dd",
    ),
    CapabilityDeclaration(
        "runtime-ingress-issuance-state/v1",
        "b7d443ade4e57c4d66ee1f36c67dcec829bcc0a1f3bb7ae6547cb9936e64f592",
    ),
    CapabilityDeclaration(
        "runtime-shadow-reconciliation-state/v1",
        "bbc8af8936bb6520a97eeb9c95de04f3a0660122e74710dade78e3791f186703",
    ),
    CapabilityDeclaration(
        "webhook-body-identity/v1",
        "5f193c2af0d781808fd92df0c172301a741750455114be8faa6be67424d0eee5",
    ),
)
_ECONOMICS_CAPABILITY: Final = CapabilityDeclaration(
    "ci-economics-evidence/v1",
    "7db6d4777e7dcfb8bcbb2d2d2fa7aac7dd56d70e10e1bd61118bb65a98f15fa5",
)
_PREDECESSOR: Final = RevisionDeclaration(
    generation=2,
    lineage_id="ci-coordinator-postgresql/v1",
    revision_id=_DOWN_REVISION,
    parent_revision_id="20260716_0001",
    transition_kind="expand",
    protocol_version=1,
    capabilities=_PREDECESSOR_CAPABILITIES,
    declaration_hash="5807087ac7f7bdeedb024f0b7c69a910617425882061a398041d01e9ef8e9f8e",
)
_SUCCESSOR: Final = RevisionDeclaration(
    generation=3,
    lineage_id="ci-coordinator-postgresql/v1",
    revision_id=revision,
    parent_revision_id=down_revision,
    transition_kind="expand",
    protocol_version=1,
    capabilities=tuple(sorted((*_PREDECESSOR_CAPABILITIES, _ECONOMICS_CAPABILITY))),
    declaration_hash="02e279558508a4701212366d7abaf79f77b9123c6c6859ca84a9b2e16fe91bf3",
)


def upgrade() -> None:
    op.create_table(
        "ci_workflow_observations",
        sa.Column("delivery_id", sa.String(length=128), nullable=False),
        sa.Column("observation_kind", sa.String(length=32), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("workflow_run_id", sa.BigInteger(), nullable=False),
        sa.Column("run_attempt", sa.BigInteger(), nullable=False),
        sa.Column("head_sha", sa.String(length=40), nullable=False),
        sa.Column("provider_job_id", sa.BigInteger(), nullable=True),
        sa.Column("semantic_hash", sa.String(length=64), nullable=False),
        sa.Column("observation_canonical_json", sa.LargeBinary(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("statement_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "retain_until",
            sa.DateTime(timezone=True),
            server_default=sa.text("statement_timestamp() + INTERVAL '90 days'"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "observation_kind IN ('workflow_job', 'workflow_run')",
            name="ck_ci_workflow_observations_kind",
        ),
        sa.CheckConstraint(
            f"installation_id BETWEEN 1 AND {_SAFE_INTEGER_MAX} AND "
            f"repository_id BETWEEN 1 AND {_SAFE_INTEGER_MAX} AND "
            f"workflow_run_id BETWEEN 1 AND {_SAFE_INTEGER_MAX} AND "
            f"run_attempt BETWEEN 1 AND {_SAFE_INTEGER_MAX} AND "
            f"(provider_job_id IS NULL OR provider_job_id BETWEEN 1 AND {_SAFE_INTEGER_MAX})",
            name="ck_ci_workflow_observations_identity_safe",
        ),
        sa.CheckConstraint(
            "head_sha ~ '^[0-9a-f]{40}$' AND semantic_hash ~ '^[0-9a-f]{64}$'",
            name="ck_ci_workflow_observations_hashes",
        ),
        sa.CheckConstraint(
            "(observation_kind = 'workflow_job' AND provider_job_id IS NOT NULL) OR "
            "(observation_kind = 'workflow_run' AND provider_job_id IS NULL)",
            name="ck_ci_workflow_observations_job_identity",
        ),
        sa.CheckConstraint(
            "octet_length(observation_canonical_json) BETWEEN 1 AND 16384",
            name="ck_ci_workflow_observations_canonical_byte_limit",
        ),
        sa.CheckConstraint(
            "retain_until = recorded_at + INTERVAL '90 days'",
            name="ck_ci_workflow_observations_retention",
        ),
        sa.ForeignKeyConstraint(
            ["delivery_id"],
            [f"{_SCHEMA}.webhook_deliveries.delivery_id"],
            name="fk_ci_workflow_observations_delivery",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("delivery_id", name="pk_ci_workflow_observations"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ci_workflow_observations_attempt_jobs",
        "ci_workflow_observations",
        [
            "installation_id",
            "repository_id",
            "workflow_run_id",
            "run_attempt",
            "head_sha",
            "provider_job_id",
            "semantic_hash",
            "delivery_id",
        ],
        schema=_SCHEMA,
        postgresql_where=sa.text("observation_kind = 'workflow_job'"),
    )
    op.create_index(
        "ix_ci_workflow_observations_retention",
        "ci_workflow_observations",
        ["retain_until", "delivery_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "ci_workflow_attempt_collections",
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tombstone_retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("attempt_count", sa.BigInteger(), nullable=False),
        sa.Column("max_attempts", sa.BigInteger(), nullable=False),
        sa.Column("backoff_seconds", sa.BigInteger(), nullable=False),
        sa.Column("max_backoff_seconds", sa.BigInteger(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_generation", sa.BigInteger(), nullable=False),
        sa.Column("lease_owner_id", sa.String(length=64), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_reason", sa.String(length=64), nullable=True),
        sa.Column("final_outcome", sa.String(length=32), nullable=True),
        sa.Column("terminal_reason", sa.String(length=32), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("statement_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("statement_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "subject_id ~ '^[0-9a-f]{64}$' AND policy_hash ~ '^[0-9a-f]{64}$'",
            name="ck_ci_workflow_attempt_collections_subject",
        ),
        sa.CheckConstraint(
            "source_created_at < deadline_at AND deadline_at < evidence_retain_until "
            "AND evidence_retain_until < tombstone_retain_until "
            "AND deadline_at = source_created_at + INTERVAL '604800 seconds' "
            "AND evidence_retain_until = source_created_at + INTERVAL '90 days' "
            "AND tombstone_retain_until = evidence_retain_until "
            "+ INTERVAL '86400 seconds'",
            name="ck_ci_workflow_attempt_collections_lifetime",
        ),
        sa.CheckConstraint(
            f"revision BETWEEN 0 AND {_SAFE_INTEGER_MAX} "
            "AND attempt_count BETWEEN 0 AND max_attempts "
            "AND max_attempts BETWEEN 1 AND 20 "
            f"AND claim_generation = attempt_count AND claim_generation <= {_SAFE_INTEGER_MAX} "
            "AND revision >= claim_generation "
            "AND backoff_seconds BETWEEN 1 AND max_backoff_seconds "
            "AND max_backoff_seconds BETWEEN 1 AND 3600",
            name="ck_ci_workflow_attempt_collections_counters",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'leased', 'deferred', 'captured', "
            "'terminal_unavailable', 'expired') AND "
            "(last_failure_reason IS NULL OR last_failure_reason IN "
            "('provider_unavailable', 'provider_binding_mismatch', 'provider_malformed', "
            "'provider_incomplete', 'provider_not_terminal', 'provider_unstable', "
            "'evidence_conflict', 'unexpected_error')) AND "
            "(final_outcome IS NULL OR final_outcome IN "
            "('captured', 'terminal_unavailable')) AND "
            "(terminal_reason IS NULL OR terminal_reason IN "
            "('deadline_exceeded', 'attempts_exhausted', 'evidence_conflict'))",
            name="ck_ci_workflow_attempt_collections_enums",
        ),
        sa.CheckConstraint(
            "(last_failure_reason IS NOT DISTINCT FROM 'evidence_conflict') = "
            "(terminal_reason IS NOT DISTINCT FROM 'evidence_conflict') AND "
            "(terminal_reason IS DISTINCT FROM 'attempts_exhausted' OR "
            "attempt_count = max_attempts) AND "
            "(final_outcome IS DISTINCT FROM 'captured' OR attempt_count > 0) AND "
            "(terminal_reason IS DISTINCT FROM 'evidence_conflict' OR attempt_count > 0)",
            name="ck_ci_workflow_attempt_collections_causal_history",
        ),
        sa.CheckConstraint(
            "((lease_owner_id IS NULL AND lease_token IS NULL "
            "AND lease_acquired_at IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner_id ~ '^[0-9a-f]{64}$' AND lease_token ~ '^[0-9a-f]{64}$' "
            "AND lease_acquired_at IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND lease_acquired_at >= source_created_at "
            "AND lease_expires_at > lease_acquired_at "
            "AND lease_expires_at < deadline_at))",
            name="ck_ci_workflow_attempt_collections_lease",
        ),
        sa.CheckConstraint(
            "(next_attempt_at IS NULL OR (next_attempt_at >= source_created_at "
            "AND next_attempt_at < deadline_at)) AND "
            "(completed_at IS NULL OR completed_at >= source_created_at) AND "
            "(expired_at IS NULL OR expired_at >= evidence_retain_until)",
            name="ck_ci_workflow_attempt_collections_transition_times",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND revision = 0 AND attempt_count = 0 "
            "AND next_attempt_at IS NOT NULL AND lease_token IS NULL "
            "AND last_failure_reason IS NULL AND final_outcome IS NULL "
            "AND terminal_reason IS NULL AND completed_at IS NULL AND expired_at IS NULL) OR "
            "(status = 'deferred' AND attempt_count > 0 AND next_attempt_at IS NOT NULL "
            "AND lease_token IS NULL AND last_failure_reason IS NOT NULL "
            "AND final_outcome IS NULL AND terminal_reason IS NULL "
            "AND completed_at IS NULL AND expired_at IS NULL) OR "
            "(status = 'leased' AND attempt_count > 0 AND next_attempt_at IS NULL "
            "AND lease_token IS NOT NULL AND final_outcome IS NULL "
            "AND terminal_reason IS NULL AND completed_at IS NULL AND expired_at IS NULL) OR "
            "(status = 'captured' AND next_attempt_at IS NULL AND lease_token IS NULL "
            "AND final_outcome = 'captured' AND terminal_reason IS NULL "
            "AND completed_at IS NOT NULL AND completed_at < evidence_retain_until "
            "AND expired_at IS NULL) OR "
            "(status = 'terminal_unavailable' AND next_attempt_at IS NULL "
            "AND lease_token IS NULL AND final_outcome = 'terminal_unavailable' "
            "AND terminal_reason IS NOT NULL AND completed_at IS NOT NULL "
            "AND expired_at IS NULL) OR "
            "(status = 'expired' AND next_attempt_at IS NULL AND lease_token IS NULL "
            "AND completed_at IS NOT NULL AND expired_at IS NOT NULL "
            "AND ((final_outcome = 'captured' AND terminal_reason IS NULL "
            "AND completed_at < evidence_retain_until) OR "
            "(final_outcome = 'terminal_unavailable' AND terminal_reason IS NOT NULL)))",
            name="ck_ci_workflow_attempt_collections_state_shape",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name="ck_ci_workflow_attempt_collections_audit_times",
        ),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            [f"{_SCHEMA}.reconciliation_subjects.subject_id"],
            name="fk_ci_workflow_attempt_collections_subject",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "subject_id",
            name="pk_ci_workflow_attempt_collections",
        ),
        sa.UniqueConstraint(
            "subject_id",
            "evidence_retain_until",
            name="uq_ci_workflow_attempt_collections_retention",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ci_workflow_attempt_collections_due",
        "ci_workflow_attempt_collections",
        ["next_attempt_at", "source_created_at", "subject_id"],
        schema=_SCHEMA,
        postgresql_where=sa.text("status IN ('pending', 'deferred')"),
    )
    op.create_index(
        "ix_ci_workflow_attempt_collections_lease_expiry",
        "ci_workflow_attempt_collections",
        ["lease_expires_at", "source_created_at", "subject_id"],
        schema=_SCHEMA,
        postgresql_where=sa.text("status = 'leased'"),
    )
    op.create_index(
        "ix_ci_workflow_attempt_collections_expiry",
        "ci_workflow_attempt_collections",
        ["evidence_retain_until", "subject_id"],
        schema=_SCHEMA,
        postgresql_where=sa.text("status IN ('captured', 'terminal_unavailable')"),
    )
    op.create_index(
        "ix_ci_workflow_attempt_collections_tombstone",
        "ci_workflow_attempt_collections",
        ["tombstone_retain_until", "subject_id"],
        schema=_SCHEMA,
        postgresql_where=sa.text("status = 'expired'"),
    )

    op.create_table(
        "ci_workflow_attempt_snapshots",
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("workflow_run_id", sa.BigInteger(), nullable=False),
        sa.Column("run_attempt", sa.BigInteger(), nullable=False),
        sa.Column("head_sha", sa.String(length=40), nullable=False),
        sa.Column("contract_hash", sa.String(length=64), nullable=False),
        sa.Column("planned_route", sa.String(length=32), nullable=False),
        sa.Column("job_count", sa.BigInteger(), nullable=False),
        sa.Column("snapshot_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("statement_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "retain_until",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "subject_id ~ '^[0-9a-f]{64}$' AND head_sha ~ '^[0-9a-f]{40}$' AND "
            "contract_hash ~ '^[0-9a-f]{64}$' AND snapshot_digest ~ '^[0-9a-f]{64}$'",
            name="ck_ci_workflow_attempt_snapshots_hashes",
        ),
        sa.CheckConstraint(
            f"installation_id BETWEEN 1 AND {_SAFE_INTEGER_MAX} AND "
            f"repository_id BETWEEN 1 AND {_SAFE_INTEGER_MAX} AND "
            f"workflow_run_id BETWEEN 1 AND {_SAFE_INTEGER_MAX} AND "
            f"run_attempt BETWEEN 1 AND {_SAFE_INTEGER_MAX} AND job_count BETWEEN 1 AND 2000",
            name="ck_ci_workflow_attempt_snapshots_identity_safe",
        ),
        sa.CheckConstraint(
            "planned_route IN ('selected', 'full_ci_counterfactual', 'unknown')",
            name="ck_ci_workflow_attempt_snapshots_planned_route",
        ),
        sa.CheckConstraint(
            "recorded_at < retain_until",
            name="ck_ci_workflow_attempt_snapshots_retention",
        ),
        sa.ForeignKeyConstraint(
            ["subject_id", "retain_until"],
            [
                f"{_SCHEMA}.ci_workflow_attempt_collections.subject_id",
                f"{_SCHEMA}.ci_workflow_attempt_collections.evidence_retain_until",
            ],
            name="fk_ci_workflow_attempt_snapshots_collection",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("subject_id", name="pk_ci_workflow_attempt_snapshots"),
        sa.UniqueConstraint(
            "installation_id",
            "repository_id",
            "workflow_run_id",
            "run_attempt",
            "head_sha",
            name="uq_ci_workflow_attempt_snapshots_attempt",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ci_workflow_attempt_snapshots_scope_recorded",
        "ci_workflow_attempt_snapshots",
        [
            "installation_id",
            "repository_id",
            sa.text("recorded_at DESC"),
            sa.text("subject_id DESC"),
        ],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ci_workflow_attempt_snapshots_retention",
        "ci_workflow_attempt_snapshots",
        ["retain_until", "subject_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "ci_workflow_attempt_snapshot_jobs",
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        sa.Column("provider_job_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("conclusion", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("runner_id", sa.BigInteger(), nullable=True),
        sa.Column("runner_name", sa.String(length=256), nullable=True),
        sa.Column("runner_group_id", sa.BigInteger(), nullable=True),
        sa.Column("runner_group_name", sa.String(length=256), nullable=True),
        sa.Column("labels_canonical_json", sa.LargeBinary(), nullable=False),
        sa.Column("semantic_hash", sa.String(length=64), nullable=False),
        sa.Column("job_canonical_json", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(
            f"provider_job_id BETWEEN 1 AND {_SAFE_INTEGER_MAX} AND "
            "char_length(name) BETWEEN 1 AND 512",
            name="ck_ci_workflow_attempt_snapshot_jobs_identity",
        ),
        sa.CheckConstraint(
            "conclusion IN ('success', 'failure', 'cancelled', 'timed_out', 'skipped', "
            "'neutral', 'action_required', 'startup_failure', 'stale')",
            name="ck_ci_workflow_attempt_snapshot_jobs_conclusion",
        ),
        sa.CheckConstraint(
            "(created_at IS NULL OR started_at IS NULL OR created_at <= started_at) AND "
            "(started_at IS NULL OR completed_at IS NULL OR started_at <= completed_at) AND "
            "(created_at IS NULL OR completed_at IS NULL OR created_at <= completed_at)",
            name="ck_ci_workflow_attempt_snapshot_jobs_temporal_order",
        ),
        sa.CheckConstraint(
            f"(runner_id IS NULL OR runner_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}) AND "
            f"(runner_group_id IS NULL OR runner_group_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}) AND "
            "(runner_name IS NULL OR char_length(runner_name) BETWEEN 1 AND 256) AND "
            "(runner_group_name IS NULL OR char_length(runner_group_name) BETWEEN 1 AND 256) AND "
            "((runner_id IS NULL) = (runner_name IS NULL)) AND "
            "((runner_group_id IS NULL) = (runner_group_name IS NULL)) AND "
            "(runner_group_id IS NULL OR runner_id IS NOT NULL)",
            name="ck_ci_workflow_attempt_snapshot_jobs_runner",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(convert_from(labels_canonical_json, 'UTF8')::jsonb) = 'array' AND "
            "jsonb_array_length(convert_from(labels_canonical_json, 'UTF8')::jsonb) <= 32",
            name="ck_ci_workflow_attempt_snapshot_jobs_labels",
        ),
        sa.CheckConstraint(
            "semantic_hash ~ '^[0-9a-f]{64}$' AND "
            "octet_length(job_canonical_json) BETWEEN 1 AND 8192",
            name="ck_ci_workflow_attempt_snapshot_jobs_canonical",
        ),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            [f"{_SCHEMA}.ci_workflow_attempt_snapshots.subject_id"],
            name="fk_ci_workflow_attempt_snapshot_jobs_snapshot",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "subject_id",
            "provider_job_id",
            name="pk_ci_workflow_attempt_snapshot_jobs",
        ),
        schema=_SCHEMA,
    )

    op.execute(
        sa.text(
            f"CREATE FUNCTION {_SCHEMA}.guard_ci_economics_mutation() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "IF TG_TABLE_NAME = 'ci_workflow_attempt_collections' THEN "
            "IF OLD.status <> 'expired' OR "
            "OLD.tombstone_retain_until > statement_timestamp() THEN "
            "RAISE EXCEPTION 'CI economics tombstone has not expired' "
            "USING ERRCODE = '55000'; "
            "END IF; RETURN OLD; END IF; "
            "IF TG_OP = 'UPDATE' THEN "
            "RAISE EXCEPTION 'CI economics evidence is immutable' USING ERRCODE = '55000'; "
            "END IF; "
            "IF OLD.retain_until > statement_timestamp() THEN "
            "RAISE EXCEPTION 'CI economics evidence has not expired' USING ERRCODE = '55000'; "
            "END IF; RETURN OLD; END; $$"
        )
    )
    for relation in ("ci_workflow_observations", "ci_workflow_attempt_snapshots"):
        op.execute(
            sa.text(
                f"CREATE TRIGGER tr_{relation}_retention_guard BEFORE UPDATE OR DELETE "
                f"ON {_SCHEMA}.{relation} FOR EACH ROW EXECUTE FUNCTION "
                f"{_SCHEMA}.guard_ci_economics_mutation()"
            )
        )
    op.execute(
        sa.text(
            "CREATE TRIGGER tr_ci_workflow_attempt_collections_retention_guard BEFORE DELETE "
            f"ON {_SCHEMA}.ci_workflow_attempt_collections FOR EACH ROW EXECUTE FUNCTION "
            f"{_SCHEMA}.guard_ci_economics_mutation()"
        )
    )
    for relation in (
        "ci_workflow_observations",
        "ci_workflow_attempt_collections",
        "ci_workflow_attempt_snapshots",
        "ci_workflow_attempt_snapshot_jobs",
    ):
        op.execute(sa.text(f"REVOKE ALL ON TABLE {_SCHEMA}.{relation} FROM PUBLIC"))
        op.execute(sa.text(f"REVOKE ALL ON TYPE {_SCHEMA}.{relation} FROM PUBLIC"))
    op.execute(
        sa.text(f"REVOKE ALL ON FUNCTION {_SCHEMA}.guard_ci_economics_mutation() FROM PUBLIC")
    )
    bind = op.get_bind()
    apply_forward_declaration(
        bind,
        load_bundled_profile(),
        previous_revision_id=_PREDECESSOR.revision_id,
        proposed=_SUCCESSOR,
        attest_resulting_capabilities=lambda declaration: _attest_result(bind, declaration),
    )


def downgrade() -> None:
    bind = op.get_bind()
    validate_pre_retention_downgrade(bind, load_bundled_profile(), target=_PREDECESSOR)
    retained = bind.scalar(
        sa.text(
            "SELECT (SELECT count(*) FROM ci_coordinator.ci_workflow_observations) + "
            "(SELECT count(*) FROM ci_coordinator.ci_workflow_attempt_snapshots) + "
            "(SELECT count(*) FROM ci_coordinator.ci_workflow_attempt_collections)"
        )
    )
    if retained != 0:
        raise RuntimeError("retained CI economics evidence forbids destructive downgrade")
    for relation in (
        "ci_workflow_observations",
        "ci_workflow_attempt_collections",
        "ci_workflow_attempt_snapshots",
    ):
        op.execute(sa.text(f"DROP TRIGGER tr_{relation}_retention_guard ON {_SCHEMA}.{relation}"))
    op.drop_table("ci_workflow_attempt_snapshot_jobs", schema=_SCHEMA)
    op.drop_table("ci_workflow_attempt_snapshots", schema=_SCHEMA)
    op.drop_table("ci_workflow_attempt_collections", schema=_SCHEMA)
    op.drop_table("ci_workflow_observations", schema=_SCHEMA)
    op.execute(sa.text(f"DROP FUNCTION {_SCHEMA}.guard_ci_economics_mutation()"))
    attest_resulting_capabilities(bind, _PREDECESSOR)


def _attest_result(connection: sa.Connection, declaration: RevisionDeclaration) -> None:
    if declaration != _SUCCESSOR:
        raise RuntimeError("CI economics migration declaration is not exact")
    attest_resulting_capabilities(connection, declaration)
