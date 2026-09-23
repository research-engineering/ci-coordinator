"""Add generation-fenced production authority after retiring predecessor capabilities."""

from collections.abc import Mapping, Sequence
from typing import Final, cast

import sqlalchemy as sa
from alembic import op

from ci_coordinator.persistence.compatibility_contracts import (
    CapabilityDeclaration,
    RevisionDeclaration,
)
from ci_coordinator.persistence.compatibility_profile import load_bundled_profile
from ci_coordinator.persistence.issued_plan_codec import project_legacy_expiry
from ci_coordinator.persistence.migration_protocol import apply_forward_declaration
from ci_coordinator.persistence.migration_result_attestation import attest_resulting_capabilities
from ci_coordinator.persistence.runtime_state_profile import (
    load_bundled_runtime_state_profile,
)

revision: str = "20260906_0005"
down_revision: str | None = "20260906_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DECLARATION: Final = RevisionDeclaration(
    generation=5,
    lineage_id="ci-coordinator-postgresql/v1",
    revision_id=revision,
    parent_revision_id=down_revision,
    transition_kind="expand",
    protocol_version=1,
    capabilities=(
        CapabilityDeclaration(
            "audit-ledger/v1",
            "fe3386e46ad68c320bdf906cc242ddb9d6ad7f3a211ae39550ef9d973dbe0d76",
        ),
        CapabilityDeclaration(
            "ci-economics-evidence/v1",
            "7db6d4777e7dcfb8bcbb2d2d2fa7aac7dd56d70e10e1bd61118bb65a98f15fa5",
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
            "production-generation-cutover/v1",
            "6f8c714276233ea0773c60fbe1f03ba7b8de79d5519d632761715e5d1ef13cb0",
        ),
        CapabilityDeclaration(
            "proposal-review-registration/v1",
            "a02e8cb4121864bcc8e497409704c575ce8eb3ba28d8340fec7020048703e0dd",
        ),
        CapabilityDeclaration(
            "runtime-ingress-issuance-state/v2",
            "087559a4e11a31f18b9a3bbe45a93ced7e2af2d9a23205a4795a7eb2c07da533",
        ),
        CapabilityDeclaration(
            "runtime-shadow-reconciliation-state/v2",
            "3470a8d7ed2cb1d2d35e287ac532b1c40d9d8f25962becbc2db4bfe56f4c8af0",
        ),
        CapabilityDeclaration(
            "webhook-body-identity/v1",
            "5f193c2af0d781808fd92df0c172301a741750455114be8faa6be67424d0eee5",
        ),
    ),
    declaration_hash="f138c00ec5316c0b94b820e206cc4c89986a255d9a3d6503bde229be4f8be3bd",
)

# Frozen migration SQL is independent of future runtime guard implementations.
_PRODUCTION_EVIDENCE_INSERT_BODY: Final = """
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended('ci-coordinator-production-evidence-capacity/v1', 0)
    );
    IF NOT EXISTS (
        SELECT 1 FROM ci_coordinator.production_evidence_bundles
        WHERE bundle_digest = NEW.bundle_digest
    ) AND EXISTS (
        SELECT 1 FROM ci_coordinator.production_evidence_bundles
        HAVING count(*) >= 1024
            OR coalesce(sum(byte_count), 0) + NEW.byte_count > 1073741824
    ) THEN
        RAISE EXCEPTION 'production evidence capacity exhausted'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
""".strip()

_PRODUCTION_STAGE_INSERT_BODY: Final = """
DECLARE
    matched_count bigint;
BEGIN
    SELECT count(*) INTO matched_count
    FROM ci_coordinator.production_admission_authorities AS authority,
         LATERAL jsonb_array_elements(
             convert_from(authority.envelope_canonical_json, 'UTF8')::jsonb
             -> 'receipt' -> 'scopeGrants'
         ) AS grant_row
    WHERE authority.authority_id = NEW.authority_id
      AND (grant_row ->> 'installationId')::bigint = NEW.installation_id
      AND (grant_row ->> 'repositoryId')::bigint = NEW.repository_id
      AND (grant_row -> 'relation' ->> 'generation')::bigint = NEW.generation
      AND grant_row -> 'relation' ->> 'evidenceBundleDigest' = NEW.bundle_digest;
    IF matched_count <> 1 THEN
        RAISE EXCEPTION 'production staged grant differs from its signed scope'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
""".strip()

_PRODUCTION_STATE_TRANSITION_BODY: Final = """
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.revision <> 1 OR NEW.generation <> 0
           OR NEW.revoked_through_generation <> 0
           OR NEW.active_authority_id IS NOT NULL
           OR NEW.latch_override_id IS NOT NULL
           OR NEW.staged_authority_id IS NULL THEN
            RAISE EXCEPTION 'production initial state is invalid'
                USING ERRCODE = '23514';
        END IF;
    ELSE
        IF NEW.installation_id <> OLD.installation_id
           OR NEW.repository_id <> OLD.repository_id
           OR NEW.revision <> OLD.revision + 1
           OR NEW.revoked_through_generation < OLD.revoked_through_generation THEN
            RAISE EXCEPTION 'production state identity or revision is invalid'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.generation = OLD.generation THEN
            IF NEW.active_authority_id IS DISTINCT FROM OLD.active_authority_id
               OR (OLD.latch_override_id IS NOT NULL AND (
                   NEW.latch_override_id IS DISTINCT FROM OLD.latch_override_id
                   OR NEW.latch_applied_at IS DISTINCT FROM OLD.latch_applied_at
               ))
               OR (NEW.revoked_through_generation > OLD.revoked_through_generation
                   AND NEW.latch_override_id IS NULL) THEN
                RAISE EXCEPTION 'production active authority cannot be rewritten'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF NEW.generation = OLD.generation + 1 THEN
            IF OLD.latch_override_id IS NULL
               OR OLD.revoked_through_generation <> OLD.generation
               OR OLD.staged_authority_id IS NULL
               OR NEW.active_authority_id IS DISTINCT FROM OLD.staged_authority_id
               OR NEW.staged_authority_id IS NOT NULL
               OR NEW.latch_override_id IS NOT NULL
               OR NEW.revoked_through_generation <> OLD.revoked_through_generation THEN
                RAISE EXCEPTION 'production generation transition is invalid'
                    USING ERRCODE = '23514';
            END IF;
        ELSE
            RAISE EXCEPTION 'production generation must be stable or its exact successor'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    IF NEW.staged_authority_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM ci_coordinator.production_staged_grants
        WHERE installation_id = NEW.installation_id
          AND repository_id = NEW.repository_id
          AND authority_id = NEW.staged_authority_id
          AND generation = NEW.generation + 1
    ) THEN
        RAISE EXCEPTION 'production staged generation is not the exact successor'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.latch_override_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM ci_coordinator.operator_overrides
        WHERE override_id = NEW.latch_override_id
          AND installation_id = NEW.installation_id
          AND repository_id = NEW.repository_id
          AND kind = 'disable_omission'
          AND applied_at = NEW.latch_applied_at
    ) THEN
        RAISE EXCEPTION 'production latch does not bind the exact repository override'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
""".strip()


def upgrade() -> None:
    connection = op.get_bind()
    _create_production_tables(connection)
    _backfill_plan_expiry(connection)
    _add_execution_origin(connection)
    _install_guards(connection)
    apply_forward_declaration(
        connection,
        load_bundled_profile(),
        previous_revision_id="20260906_0004",
        proposed=_DECLARATION,
        attest_resulting_capabilities=lambda declaration: attest_resulting_capabilities(
            connection, declaration
        ),
    )


def downgrade() -> None:
    raise RuntimeError("production cutover requires forward repair or an admitted database restore")


def _create_production_tables(connection: sa.Connection) -> None:
    for statement in (
        """
        CREATE TABLE ci_coordinator.production_evidence_bundles (
            bundle_digest varchar(64) PRIMARY KEY,
            content_sha256 varchar(64) NOT NULL,
            canonical_json bytea NOT NULL,
            byte_count bigint NOT NULL,
            retained_at timestamptz NOT NULL,
            CONSTRAINT ck_production_evidence_bundles_identity CHECK (
                bundle_digest ~ '^[0-9a-f]{64}$'
                AND content_sha256 = encode(sha256(canonical_json), 'hex')
            ),
            CONSTRAINT ck_production_evidence_bundles_bytes CHECK (
                byte_count BETWEEN 1 AND 8388608 AND byte_count = octet_length(canonical_json)
            )
        )
        """,
        """
        CREATE TABLE ci_coordinator.production_staged_grants (
            installation_id bigint NOT NULL,
            repository_id bigint NOT NULL,
            authority_id varchar(53) NOT NULL,
            generation bigint NOT NULL,
            bundle_digest varchar(64) NOT NULL,
            lookup_canonical_json bytea NOT NULL,
            staged_at timestamptz NOT NULL,
            CONSTRAINT pk_production_staged_grants
                PRIMARY KEY (installation_id, repository_id, authority_id),
            CONSTRAINT uq_production_staged_grants_generation
                UNIQUE (installation_id, repository_id, authority_id, generation),
            CONSTRAINT fk_production_staged_grants_scope
                FOREIGN KEY (authority_id, installation_id, repository_id)
                REFERENCES ci_coordinator.production_admission_scope_bindings
                    (authority_id, installation_id, repository_id) ON DELETE RESTRICT,
            CONSTRAINT fk_production_staged_grants_bundle FOREIGN KEY (bundle_digest)
                REFERENCES ci_coordinator.production_evidence_bundles(bundle_digest)
                ON DELETE RESTRICT,
            CONSTRAINT ck_production_staged_grants_generation
                CHECK (generation BETWEEN 1 AND 9007199254740991),
            CONSTRAINT ck_production_staged_grants_lookup_bytes
                CHECK (octet_length(lookup_canonical_json) BETWEEN 1 AND 262144)
        )
        """,
        """
        CREATE TABLE ci_coordinator.production_scope_states (
            installation_id bigint NOT NULL,
            repository_id bigint NOT NULL,
            revision bigint NOT NULL,
            generation bigint NOT NULL,
            revoked_through_generation bigint NOT NULL,
            active_authority_id varchar(53),
            staged_authority_id varchar(53),
            latch_override_id varchar(41),
            latch_applied_at timestamptz,
            CONSTRAINT pk_production_scope_states PRIMARY KEY (installation_id, repository_id),
            CONSTRAINT fk_production_scope_states_active
                FOREIGN KEY (installation_id, repository_id, active_authority_id, generation)
                REFERENCES ci_coordinator.production_staged_grants
                    (installation_id, repository_id, authority_id, generation) ON DELETE RESTRICT,
            CONSTRAINT fk_production_scope_states_staged
                FOREIGN KEY (installation_id, repository_id, staged_authority_id)
                REFERENCES ci_coordinator.production_staged_grants
                    (installation_id, repository_id, authority_id) ON DELETE RESTRICT,
            CONSTRAINT fk_production_scope_states_latch FOREIGN KEY (latch_override_id)
                REFERENCES ci_coordinator.operator_overrides(override_id) ON DELETE RESTRICT,
            CONSTRAINT ck_production_scope_states_counters CHECK (
                installation_id BETWEEN 1 AND 9007199254740991
                AND repository_id BETWEEN 1 AND 9007199254740991
                AND revision BETWEEN 1 AND 9007199254740991
                AND generation BETWEEN 0 AND 9007199254740991
                AND revoked_through_generation BETWEEN 0 AND generation
            ),
            CONSTRAINT ck_production_scope_states_active CHECK (
                (generation = 0 AND active_authority_id IS NULL)
                OR (generation > 0 AND active_authority_id IS NOT NULL)
            ),
            CONSTRAINT ck_production_scope_states_latch CHECK (
                (latch_override_id IS NULL AND latch_applied_at IS NULL)
                OR (latch_override_id IS NOT NULL AND latch_applied_at IS NOT NULL
                    AND revoked_through_generation = generation)
            )
        )
        """,
    ):
        connection.execute(sa.text(statement))


def _backfill_plan_expiry(connection: sa.Connection) -> None:
    op.add_column(
        "issued_plan_envelopes",
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        schema="ci_coordinator",
    )
    profile = load_bundled_runtime_state_profile()
    after: str | None = None
    while True:
        rows = tuple(
            connection.execute(
                sa.text("""
            SELECT idempotency_key, record_id, request_hash, installation_id, repository_id,
                   production_admission_authority_id, envelope_canonical_json, issued_at
            FROM ci_coordinator.issued_plan_envelopes
            WHERE CAST(:after AS text) IS NULL OR idempotency_key > CAST(:after AS text)
            ORDER BY idempotency_key LIMIT 16
        """),
                {"after": after},
            ).mappings()
        )
        if not rows:
            break
        for row in rows:
            expires_at = project_legacy_expiry(cast(Mapping[str, object], row), profile)
            connection.execute(
                sa.text("""
                UPDATE ci_coordinator.issued_plan_envelopes
                SET expires_at = :expires_at WHERE idempotency_key = :key
            """),
                {"expires_at": expires_at, "key": row["idempotency_key"]},
            )
        after = rows[-1]["idempotency_key"]
    op.alter_column("issued_plan_envelopes", "expires_at", nullable=False, schema="ci_coordinator")
    connection.execute(
        sa.text(
            "ALTER TABLE ci_coordinator.issued_plan_envelopes "
            "ADD CONSTRAINT ck_issued_plan_envelopes_exact_expiry CHECK (("
            "isfinite(expires_at) AND expires_at > issued_at AND "
            "jsonb_typeof(convert_from(envelope_canonical_json, 'UTF8')::jsonb "
            "-> 'expiresAt') = 'string' AND "
            "(convert_from(envelope_canonical_json, 'UTF8')::jsonb ->> 'expiresAt') "
            "~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
            "(\\.[0-9]{6})?[+-][0-9]{2}:[0-9]{2}(:[0-9]{2}(\\.[0-9]{6})?)?$' AND "
            "expires_at = (convert_from(envelope_canonical_json, 'UTF8')::jsonb "
            "->> 'expiresAt')::timestamptz) IS TRUE)"
        )
    )
    op.create_index(
        "ix_issued_plan_envelopes_selected_expiry",
        "issued_plan_envelopes",
        ["installation_id", "repository_id", "expires_at"],
        schema="ci_coordinator",
        postgresql_where=sa.text("production_admission_authority_id IS NOT NULL"),
    )


def _add_execution_origin(connection: sa.Connection) -> None:
    connection.execute(
        sa.text("""
        ALTER TABLE ci_coordinator.reconciliation_subjects
        ADD COLUMN execution_origin varchar(8) NOT NULL DEFAULT 'legacy',
        ADD COLUMN production_generation bigint,
        ADD COLUMN production_authority_id varchar(53),
        ADD CONSTRAINT ck_reconciliation_subjects_production_origin CHECK (
            (execution_origin IN ('legacy', 'full_ci')
                AND production_generation IS NULL AND production_authority_id IS NULL)
            OR (execution_origin = 'selected' AND production_generation IS NOT NULL
                AND production_generation BETWEEN 1 AND 9007199254740991
                AND production_authority_id IS NOT NULL)
        ),
        ADD CONSTRAINT fk_reconciliation_subjects_production_generation
            FOREIGN KEY (installation_id, repository_id, production_authority_id,
                         production_generation)
            REFERENCES ci_coordinator.production_staged_grants
                (installation_id, repository_id, authority_id, generation) ON DELETE RESTRICT
    """)
    )
    op.create_index(
        "ix_reconciliation_subjects_production_drain",
        "reconciliation_subjects",
        ["installation_id", "repository_id", "subject_id"],
        schema="ci_coordinator",
        postgresql_where=sa.text("execution_origin <> 'full_ci'"),
    )


def _install_guards(connection: sa.Connection) -> None:
    for relation, name, events, body in (
        (
            "production_evidence_bundles",
            "guard_production_evidence_insert_v1",
            "INSERT",
            _PRODUCTION_EVIDENCE_INSERT_BODY,
        ),
        (
            "production_staged_grants",
            "guard_production_stage_insert_v1",
            "INSERT",
            _PRODUCTION_STAGE_INSERT_BODY,
        ),
        (
            "production_scope_states",
            "guard_production_state_transition_v1",
            "INSERT OR UPDATE",
            _PRODUCTION_STATE_TRANSITION_BODY,
        ),
    ):
        connection.execute(
            sa.text(
                f"CREATE FUNCTION ci_coordinator.{name}() RETURNS trigger "
                "LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog "
                f"AS $guard${body}$guard$"
            )
        )
        connection.execute(
            sa.text(
                f"CREATE TRIGGER {name} BEFORE {events} ON ci_coordinator.{relation} "
                f"FOR EACH ROW EXECUTE FUNCTION ci_coordinator.{name}()"
            )
        )
        connection.execute(sa.text(f"REVOKE ALL ON FUNCTION ci_coordinator.{name}() FROM PUBLIC"))
        connection.execute(sa.text(f"REVOKE ALL ON TABLE ci_coordinator.{relation} FROM PUBLIC"))
        connection.execute(sa.text(f"REVOKE ALL ON TYPE ci_coordinator.{relation} FROM PUBLIC"))
