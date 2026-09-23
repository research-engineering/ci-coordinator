"""Create the initial CI Coordinator database schema."""

import json
from collections.abc import Sequence
from hashlib import sha256
from typing import Final

import sqlalchemy as sa
from alembic import op

from ci_coordinator.governance_baseline import MAX_BASELINE_COMMAND_BYTES
from ci_coordinator.governance_observation import MAX_GOVERNANCE_STATE_BYTES
from ci_coordinator.persistence.control_plane_identity_schema_attestation import (
    control_plane_identity_schema_matches_contract_sync,
)
from ci_coordinator.persistence.governance_baseline_schema_attestation import (
    governance_baseline_schema_matches_contract_sync,
)
from ci_coordinator.persistence.proposal_review_schema_attestation import (
    proposal_review_registration_schema_matches_contract_sync,
)
from ci_coordinator.persistence.repository_attestation_schema_attestation import (
    repository_attestation_schema_mismatches_sync,
)
from ci_coordinator.persistence.runtime_state_schema_attestation import (
    webhook_body_identity_schema_matches_contract_sync,
)

revision: str = "20260716_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA: Final = "ci_coordinator"
_SAFE_INTEGER_MAX: Final = 9_007_199_254_740_991
_DECLARATION_HASH: Final = "bf041595a29ddad2f1ca8acac7a159b42d462ef94a281f7315ed0710911a6d66"
_CAPABILITIES: Final = (
    ("audit-ledger/v1", "fe3386e46ad68c320bdf906cc242ddb9d6ad7f3a211ae39550ef9d973dbe0d76"),
    (
        "config-epoch-lifecycle/v1",
        "32b47f8c1cd882db591841227bb567ce85dc7a3f9d2b35fa89b8c00f0c1af23c",
    ),
    (
        "control-plane-identity-state/v1",
        "3e0eca4e06e8665a4f545fc36fc87f2f5debd205da0a7fac8942a0a2477b8194",
    ),
    (
        "database-compatibility-protocol/v1",
        "e23360125c88fb7fc1dd9e13ae51f283f7a40e496aee548c71efe51da1a0965f",
    ),
    (
        "governance-baseline-state/v1",
        "fcd08237e4849e63aae5c24a33ebca0a64812795572a0f708b969e76d225e024",
    ),
    (
        "operator-override-state/v1",
        "402d4fe7fc73e871d385c9e00cde93c34bf6d5a7fdef6d3f62c27554585148e0",
    ),
    (
        "proposal-review-registration/v1",
        "a02e8cb4121864bcc8e497409704c575ce8eb3ba28d8340fec7020048703e0dd",
    ),
    (
        "runtime-ingress-issuance-state/v1",
        "b7d443ade4e57c4d66ee1f36c67dcec829bcc0a1f3bb7ae6547cb9936e64f592",
    ),
    (
        "runtime-shadow-reconciliation-state/v1",
        "bbc8af8936bb6520a97eeb9c95de04f3a0660122e74710dade78e3791f186703",
    ),
    (
        "webhook-body-identity/v1",
        "5f193c2af0d781808fd92df0c172301a741750455114be8faa6be67424d0eee5",
    ),
)
_RETAINED_DATA_TABLES: Final = (
    "active_config_epochs",
    "audit_events",
    "config_epoch_activations",
    "config_epochs",
    "control_plane_logout_replays",
    "control_plane_sessions",
    "governance_baseline_operations",
    "governance_baselines",
    "issued_plan_envelopes",
    "operator_overrides",
    "production_admission_authorities",
    "production_admission_scope_bindings",
    "reconciliation_observations",
    "reconciliation_results",
    "reconciliation_subjects",
    "repository_attestation_transactions",
    "shadow_evidence",
    "webhook_deliveries",
    "workflow_proposal_reviews",
)
_CAPABILITY_TABLES: Final = (
    ("audit-ledger/v1", ("audit_events", "audit_ledger_head")),
    (
        "config-epoch-lifecycle/v1",
        ("active_config_epochs", "config_epoch_activations", "config_epochs"),
    ),
    (
        "control-plane-identity-state/v1",
        ("control_plane_logout_replays", "control_plane_sessions"),
    ),
    (
        "database-compatibility-protocol/v1",
        ("database_compatibility_capabilities", "database_compatibility_declarations"),
    ),
    (
        "governance-baseline-state/v1",
        ("governance_baseline_operations", "governance_baselines"),
    ),
    ("operator-override-state/v1", ("operator_overrides",)),
    (
        "proposal-review-registration/v1",
        ("repository_attestation_transactions", "workflow_proposal_reviews"),
    ),
    (
        "runtime-ingress-issuance-state/v1",
        (
            "issued_plan_envelopes",
            "production_admission_authorities",
            "production_admission_scope_bindings",
            "webhook_deliveries",
        ),
    ),
    (
        "runtime-shadow-reconciliation-state/v1",
        (
            "reconciliation_observations",
            "reconciliation_results",
            "reconciliation_subjects",
            "shadow_evidence",
        ),
    ),
    ("webhook-body-identity/v1", ("webhook_deliveries",)),
)
_ALL_TABLES: Final = tuple(
    sorted({table for _capability_id, tables in _CAPABILITY_TABLES for table in tables})
)

# Each digest binds the ordered PostgreSQL 18.6 core catalog projection. The
# capability-owned additions have separate exact attestors below so their
# contracts stay cohesive with their runtime owners.
_CATALOG_PROJECTIONS: Final = (
    (
        "relations",
        """
        SELECT relation.relkind::text, relation.relname,
               relation.relpersistence::text, relation.relrowsecurity,
               relation.relforcerowsecurity
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = :schema
          AND relation.relname NOT IN (
              'control_plane_logout_replays',
              'control_plane_sessions',
              'ix_control_plane_logout_replays_retain_until',
              'ix_control_plane_sessions_expires_at',
              'ix_control_plane_sessions_identity_issued',
              'ix_control_plane_sessions_issuer_sid',
              'governance_baseline_operations',
              'governance_baselines',
              'pk_control_plane_logout_replays',
              'pk_control_plane_sessions',
              'pk_governance_baseline_operations',
              'pk_governance_baselines',
              'pk_repository_attestation_transactions',
              'pk_workflow_proposal_reviews',
              'repository_attestation_transactions',
              'ix_repository_attestation_transactions_expiry',
              'ix_repository_attestation_transactions_session',
              'uq_repository_attestation_transactions_operation',
              'uq_governance_baselines_audit_event',
              'uq_governance_baselines_baseline_id',
              'uq_governance_baselines_scope_operation',
              'uq_governance_baselines_scope_version_identity',
              'uq_webhook_deliveries_body_sha256',
              'uq_workflow_proposal_reviews_audit_event',
              'uq_workflow_proposal_reviews_attestation_transaction',
              'uq_workflow_proposal_reviews_scope_manifest',
              'workflow_proposal_reviews'
          )
        ORDER BY 1, 2
        """,
        48,
        "6a4971a06a0198a846d347f117d1013f1f8d090cf186a2755b06183647815f7b",
    ),
    (
        "columns",
        """
        SELECT relation.relname, attribute.attnum, attribute.attname,
               format_type(attribute.atttypid, attribute.atttypmod),
               attribute.attnotnull,
               pg_get_expr(default_value.adbin, default_value.adrelid),
               attribute.attstorage::text, attribute.attidentity::text,
               attribute.attgenerated::text, attribute.attcompression::text
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        JOIN pg_attribute AS attribute
          ON attribute.attrelid = relation.oid
         AND attribute.attnum > 0
         AND NOT attribute.attisdropped
        LEFT JOIN pg_attrdef AS default_value
          ON default_value.adrelid = relation.oid
         AND default_value.adnum = attribute.attnum
        WHERE namespace.nspname = :schema AND relation.relkind = 'r'
          AND relation.relname NOT IN (
              'control_plane_logout_replays',
              'control_plane_sessions',
              'governance_baseline_operations',
              'governance_baselines',
              'repository_attestation_transactions',
              'workflow_proposal_reviews'
          )
        ORDER BY 1, 2
        """,
        130,
        "7275bce061ba97ded206b0e3c78db98ed3a137cd6a38edd86e4c664f26930fbc",
    ),
    (
        "constraints",
        """
        SELECT relation.relname, constraint_metadata.conname,
               constraint_metadata.contype::text,
               constraint_metadata.condeferrable,
               constraint_metadata.condeferred,
               constraint_metadata.convalidated,
               constraint_metadata.connoinherit,
               pg_get_constraintdef(constraint_metadata.oid, false)
        FROM pg_constraint AS constraint_metadata
        JOIN pg_class AS relation ON relation.oid = constraint_metadata.conrelid
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = :schema AND constraint_metadata.contype <> 'n'
          AND relation.relname NOT IN (
              'control_plane_logout_replays',
              'control_plane_sessions',
              'governance_baseline_operations',
              'governance_baselines',
              'repository_attestation_transactions',
              'workflow_proposal_reviews'
          )
          AND constraint_metadata.conname <> 'uq_webhook_deliveries_body_sha256'
        ORDER BY 1, 2
        """,
        129,
        "2de8ca21a0a238ee5a82052c528b2dd7a66671b02cbfcd2f764096d45af2f7bf",
    ),
    (
        "indexes",
        """
        SELECT table_metadata.relname, index_relation.relname,
               index_metadata.indisunique, index_metadata.indisprimary,
               index_metadata.indisvalid, index_metadata.indisready,
               index_metadata.indislive,
               pg_get_indexdef(index_relation.oid, 0, false)
        FROM pg_index AS index_metadata
        JOIN pg_class AS index_relation ON index_relation.oid = index_metadata.indexrelid
        JOIN pg_class AS table_metadata ON table_metadata.oid = index_metadata.indrelid
        JOIN pg_namespace AS namespace ON namespace.oid = index_relation.relnamespace
        WHERE namespace.nspname = :schema
          AND table_metadata.relname NOT IN (
              'control_plane_logout_replays',
              'control_plane_sessions',
              'governance_baseline_operations',
              'governance_baselines',
              'repository_attestation_transactions',
              'workflow_proposal_reviews'
          )
          AND index_relation.relname <> 'uq_webhook_deliveries_body_sha256'
        ORDER BY 1, 2
        """,
        32,
        "0b1ec3a40d06eb5ff4c3004cb8f1853e654cc20dd01722060ebc4a2ef9e81459",
    ),
    (
        "triggers",
        """
        SELECT relation.relname, trigger_metadata.tgname,
               trigger_metadata.tgenabled::text,
               function_namespace.nspname, function_metadata.proname,
               pg_get_function_identity_arguments(function_metadata.oid),
               pg_get_triggerdef(trigger_metadata.oid, false)
        FROM pg_trigger AS trigger_metadata
        JOIN pg_class AS relation ON relation.oid = trigger_metadata.tgrelid
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        JOIN pg_proc AS function_metadata
          ON function_metadata.oid = trigger_metadata.tgfoid
        JOIN pg_namespace AS function_namespace
          ON function_namespace.oid = function_metadata.pronamespace
        WHERE namespace.nspname = :schema AND NOT trigger_metadata.tgisinternal
          AND relation.relname NOT IN (
              'control_plane_sessions',
              'governance_baseline_operations',
              'governance_baselines',
              'workflow_proposal_reviews'
          )
        ORDER BY 1, 2
        """,
        4,
        "26b55230db8b6d759da50e73955f2b5efff2efa8558e61da19e15561b0f02f3b",
    ),
    (
        "routines",
        """
        SELECT routine.proname, pg_get_function_identity_arguments(routine.oid),
               pg_get_function_result(routine.oid), language.lanname,
               routine.prokind::text, routine.prosecdef,
               routine.provolatile::text, routine.proisstrict,
               routine.proleakproof, routine.prosrc,
               routine.proconfig
        FROM pg_proc AS routine
        JOIN pg_namespace AS namespace ON namespace.oid = routine.pronamespace
        JOIN pg_language AS language ON language.oid = routine.prolang
        WHERE namespace.nspname = :schema
          AND routine.proname NOT IN (
              'reject_control_plane_session_mutation',
              'reject_governance_baseline_mutation',
              'reject_proposal_review_mutation'
          )
        ORDER BY 1, 2
        """,
        2,
        "9a15ef4276bd404dc1672324b312c12decea87cde1bdeba128db68d12c7d1508",
    ),
    (
        "standalone_types",
        """
        SELECT type_metadata.typname, type_metadata.typtype::text,
               type_metadata.typcategory::text,
               format_type(type_metadata.typbasetype, type_metadata.typtypmod),
               type_metadata.typnotnull
        FROM pg_type AS type_metadata
        JOIN pg_namespace AS namespace ON namespace.oid = type_metadata.typnamespace
        WHERE namespace.nspname = :schema
          AND type_metadata.typrelid = 0
          AND type_metadata.typelem = 0
        ORDER BY 1
        """,
        0,
        "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    ),
)


def upgrade() -> None:
    op.execute(sa.text(f"CREATE SCHEMA {_SCHEMA}"))
    _create_audit_ledger()
    _create_compatibility_protocol()
    _create_config_epoch_lifecycle()
    _create_control_plane_identity_state()
    _create_proposal_review_registration()
    _create_runtime_state()
    _create_shadow_reconciliation_state()
    _create_operator_override_state()
    _create_governance_baseline_state()
    _install_immutability_guards()
    _configure_extended_storage()
    _revoke_public_access()
    _seed_pristine_audit_head()
    _attest_baseline_catalog_and_privileges()
    _assert_prepublication_state()
    _publish_baseline_declaration()


def downgrade() -> None:
    _lock_baseline_tables()
    _attest_baseline_catalog_and_privileges()
    _assert_exact_published_baseline()
    _assert_no_retained_product_data()
    _drop_baseline_schema()


def _create_audit_ledger() -> None:
    subjects = ", ".join(
        f"'{value}'"
        for value in (
            "webhook-delivery",
            "observation",
            "policy-decision",
            "reconciliation-state",
            "coordinator-check",
            "dynamic-ci-plan",
            "release-evidence",
            "policy-drift",
        )
    )
    _execute(
        f"""
        CREATE TABLE {_SCHEMA}.audit_events (
            sequence bigint NOT NULL,
            installation_id bigint,
            repository_id bigint,
            idempotency_key bytea NOT NULL,
            idempotency_key_digest bytea NOT NULL,
            subject_type varchar(64) NOT NULL,
            subject_id bytea NOT NULL,
            event_type bytea NOT NULL,
            created_at varchar(24) NOT NULL,
            actor bytea NOT NULL,
            payload_canonical_json bytea NOT NULL,
            schema_version varchar(64) NOT NULL,
            audit_event_id varchar(38) NOT NULL,
            previous_event_hash bytea,
            payload_hash bytea NOT NULL,
            input_hash bytea NOT NULL,
            event_hash bytea NOT NULL,
            CONSTRAINT pk_audit_events PRIMARY KEY (sequence),
            CONSTRAINT uq_audit_events_idempotency_digest UNIQUE (idempotency_key_digest),
            CONSTRAINT uq_audit_events_audit_event_id UNIQUE (audit_event_id),
            CONSTRAINT uq_audit_events_event_hash UNIQUE (event_hash),
            CONSTRAINT fk_audit_events_previous_event_hash
                FOREIGN KEY (previous_event_hash)
                REFERENCES {_SCHEMA}.audit_events (event_hash),
            CONSTRAINT ck_audit_events_sequence_safe
                CHECK (sequence BETWEEN 1 AND {_SAFE_INTEGER_MAX}),
            CONSTRAINT ck_audit_events_scope_shape CHECK (
                (installation_id IS NULL AND repository_id IS NULL)
                OR (
                    installation_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}
                    AND repository_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}
                )
            ),
            CONSTRAINT ck_audit_events_predecessor_shape CHECK (
                (sequence = 1 AND previous_event_hash IS NULL)
                OR (sequence > 1 AND previous_event_hash IS NOT NULL)
            ),
            CONSTRAINT ck_audit_events_subject_type CHECK (subject_type IN ({subjects})),
            CONSTRAINT ck_audit_events_schema_version
                CHECK (schema_version = 'ci-audit-event/v1'),
            CONSTRAINT ck_audit_events_idempotency_digest_length
                CHECK (octet_length(idempotency_key_digest) = 32),
            CONSTRAINT ck_audit_events_hash_lengths CHECK (
                octet_length(payload_hash) = 32
                AND octet_length(input_hash) = 32
                AND octet_length(event_hash) = 32
            ),
            CONSTRAINT ck_audit_events_previous_hash_length CHECK (
                previous_event_hash IS NULL OR octet_length(previous_event_hash) = 32
            ),
            CONSTRAINT ck_audit_events_idempotency_key_byte_limit
                CHECK (octet_length(idempotency_key) BETWEEN 1 AND 4096),
            CONSTRAINT ck_audit_events_subject_id_byte_limit
                CHECK (octet_length(subject_id) BETWEEN 1 AND 4096),
            CONSTRAINT ck_audit_events_event_type_byte_limit
                CHECK (octet_length(event_type) BETWEEN 1 AND 4096),
            CONSTRAINT ck_audit_events_actor_byte_limit
                CHECK (octet_length(actor) BETWEEN 1 AND 4096),
            CONSTRAINT ck_audit_events_payload_canonical_json_byte_limit
                CHECK (octet_length(payload_canonical_json) BETWEEN 1 AND 1048576)
        )
        """,
        f"""
        CREATE INDEX ix_audit_events_scope_sequence
        ON {_SCHEMA}.audit_events (installation_id, repository_id, sequence)
        """,
        f"""
        CREATE TABLE {_SCHEMA}.audit_ledger_head (
            head_id smallint NOT NULL,
            revision bigint NOT NULL,
            last_sequence bigint,
            last_event_hash bytea,
            CONSTRAINT pk_audit_ledger_head PRIMARY KEY (head_id),
            CONSTRAINT fk_audit_ledger_head_last_event_hash
                FOREIGN KEY (last_event_hash) REFERENCES {_SCHEMA}.audit_events (event_hash),
            CONSTRAINT ck_audit_ledger_head_singleton CHECK (head_id = 1),
            CONSTRAINT ck_audit_ledger_head_revision_safe
                CHECK (revision BETWEEN 0 AND {_SAFE_INTEGER_MAX}),
            CONSTRAINT ck_audit_ledger_head_state CHECK (
                (revision = 0 AND last_sequence IS NULL AND last_event_hash IS NULL)
                OR (revision >= 1 AND revision = last_sequence AND last_event_hash IS NOT NULL)
            ),
            CONSTRAINT ck_audit_ledger_head_hash_length CHECK (
                last_event_hash IS NULL OR octet_length(last_event_hash) = 32
            )
        )
        """,
    )


def _create_compatibility_protocol() -> None:
    _execute(
        f"""
        CREATE TABLE {_SCHEMA}.database_compatibility_declarations (
            generation bigint NOT NULL,
            revision_id varchar(32) NOT NULL,
            parent_revision_id varchar(32),
            transition_kind varchar(16) NOT NULL,
            lineage_id varchar(128) NOT NULL,
            protocol_version smallint NOT NULL,
            declaration_hash varchar(64) NOT NULL,
            CONSTRAINT pk_database_compatibility_declarations PRIMARY KEY (generation),
            CONSTRAINT uq_database_compatibility_revision UNIQUE (revision_id),
            CONSTRAINT fk_database_compatibility_parent_revision
                FOREIGN KEY (parent_revision_id)
                REFERENCES {_SCHEMA}.database_compatibility_declarations (revision_id),
            CONSTRAINT ck_database_compatibility_generation_positive CHECK (generation >= 1),
            CONSTRAINT ck_database_compatibility_transition_kind
                CHECK (transition_kind IN ('bootstrap', 'expand', 'contract')),
            CONSTRAINT ck_database_compatibility_parent_shape CHECK (
                (generation = 1 AND parent_revision_id IS NULL AND transition_kind = 'bootstrap')
                OR (generation > 1 AND parent_revision_id IS NOT NULL
                    AND transition_kind IN ('expand', 'contract'))
            ),
            CONSTRAINT ck_database_compatibility_declaration_hash
                CHECK (declaration_hash ~ '^[0-9a-f]{{64}}$')
        )
        """,
        f"""
        CREATE TABLE {_SCHEMA}.database_compatibility_capabilities (
            revision_id varchar(32) NOT NULL,
            capability_id varchar(128) NOT NULL,
            descriptor_hash varchar(64) NOT NULL,
            CONSTRAINT pk_database_compatibility_capability
                PRIMARY KEY (revision_id, capability_id),
            CONSTRAINT fk_database_capability_revision
                FOREIGN KEY (revision_id)
                REFERENCES {_SCHEMA}.database_compatibility_declarations (revision_id)
                ON DELETE RESTRICT,
            CONSTRAINT ck_database_capability_descriptor_hash
                CHECK (descriptor_hash ~ '^[0-9a-f]{{64}}$')
        )
        """,
    )


def _create_config_epoch_lifecycle() -> None:
    _execute(
        f"""
        CREATE TABLE {_SCHEMA}.config_epochs (
            epoch_id varchar(64) NOT NULL,
            installation_id bigint NOT NULL,
            repository_id bigint NOT NULL,
            source_format varchar(8) NOT NULL,
            source_bytes bytea NOT NULL,
            normalized_document_bytes bytea NOT NULL,
            compiled_policy_bytes bytea NOT NULL,
            document_schema_id varchar(4096) NOT NULL,
            document_profile_id varchar(4096) NOT NULL,
            semantic_profile_id varchar(4096) NOT NULL,
            compiled_schema_id varchar(4096) NOT NULL,
            producer_resource_profile_id varchar(4096) NOT NULL,
            producer_byte_profile_id varchar(4096) NOT NULL,
            producer_feasibility_profile_id varchar(4096) NOT NULL,
            source_hash varchar(64) NOT NULL,
            document_hash varchar(64) NOT NULL,
            epoch_hash varchar(64) NOT NULL,
            CONSTRAINT pk_config_epochs PRIMARY KEY (epoch_id),
            CONSTRAINT uq_config_epochs_scope_epoch
                UNIQUE (installation_id, repository_id, epoch_id),
            CONSTRAINT ck_config_epochs_epoch_id_hash CHECK (epoch_id ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_config_epochs_installation_id_safe
                CHECK (installation_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}),
            CONSTRAINT ck_config_epochs_repository_id_safe
                CHECK (repository_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}),
            CONSTRAINT ck_config_epochs_source_format
                CHECK (source_format IN ('json', 'yaml-1.2')),
            CONSTRAINT ck_config_epochs_source_bytes_limit
                CHECK (octet_length(source_bytes) <= 2097152),
            CONSTRAINT ck_config_epochs_normalized_bytes_limit
                CHECK (octet_length(normalized_document_bytes) BETWEEN 1 AND 4194304),
            CONSTRAINT ck_config_epochs_compiled_bytes_limit
                CHECK (octet_length(compiled_policy_bytes) BETWEEN 1 AND 4194304),
            CONSTRAINT ck_config_epochs_document_schema_id_limit
                CHECK (octet_length(document_schema_id) BETWEEN 1 AND 4096),
            CONSTRAINT ck_config_epochs_document_profile_id_limit
                CHECK (octet_length(document_profile_id) BETWEEN 1 AND 4096),
            CONSTRAINT ck_config_epochs_semantic_profile_id_limit
                CHECK (octet_length(semantic_profile_id) BETWEEN 1 AND 4096),
            CONSTRAINT ck_config_epochs_compiled_schema_id_limit
                CHECK (octet_length(compiled_schema_id) BETWEEN 1 AND 4096),
            CONSTRAINT ck_config_epochs_producer_resource_profile_id_limit
                CHECK (octet_length(producer_resource_profile_id) BETWEEN 1 AND 4096),
            CONSTRAINT ck_config_epochs_producer_byte_profile_id_limit
                CHECK (octet_length(producer_byte_profile_id) BETWEEN 1 AND 4096),
            CONSTRAINT ck_config_epochs_producer_feasibility_profile_id_limit
                CHECK (octet_length(producer_feasibility_profile_id) BETWEEN 1 AND 4096),
            CONSTRAINT ck_config_epochs_hashes CHECK (
                source_hash ~ '^[0-9a-f]{{64}}$'
                AND document_hash ~ '^[0-9a-f]{{64}}$'
                AND epoch_hash ~ '^[0-9a-f]{{64}}$'
            )
        )
        """,
        f"""
        CREATE TABLE {_SCHEMA}.active_config_epochs (
            installation_id bigint NOT NULL,
            repository_id bigint NOT NULL,
            epoch_id varchar(64) NOT NULL,
            revision bigint NOT NULL,
            CONSTRAINT pk_active_config_epochs PRIMARY KEY (installation_id, repository_id),
            CONSTRAINT fk_active_config_epochs_epoch
                FOREIGN KEY (installation_id, repository_id, epoch_id)
                REFERENCES {_SCHEMA}.config_epochs (installation_id, repository_id, epoch_id)
                ON DELETE RESTRICT,
            CONSTRAINT ck_active_config_epochs_installation_id_safe
                CHECK (installation_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}),
            CONSTRAINT ck_active_config_epochs_repository_id_safe
                CHECK (repository_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}),
            CONSTRAINT ck_active_config_epochs_epoch_id_hash
                CHECK (epoch_id ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_active_config_epochs_revision_safe
                CHECK (revision BETWEEN 1 AND {_SAFE_INTEGER_MAX})
        )
        """,
        f"""
        CREATE TABLE {_SCHEMA}.config_epoch_activations (
            installation_id bigint NOT NULL,
            repository_id bigint NOT NULL,
            operation_id varchar(256) NOT NULL,
            expected_revision bigint,
            previous_epoch_id varchar(64),
            previous_revision bigint,
            target_epoch_id varchar(64) NOT NULL,
            result_revision bigint NOT NULL,
            audit_event_id varchar(38) NOT NULL,
            audit_input_hash bytea NOT NULL,
            CONSTRAINT pk_config_epoch_activations
                PRIMARY KEY (installation_id, repository_id, operation_id),
            CONSTRAINT uq_config_epoch_activations_scope_revision
                UNIQUE (installation_id, repository_id, result_revision),
            CONSTRAINT uq_config_epoch_activations_audit_event UNIQUE (audit_event_id),
            CONSTRAINT fk_config_epoch_activations_target_epoch
                FOREIGN KEY (installation_id, repository_id, target_epoch_id)
                REFERENCES {_SCHEMA}.config_epochs (installation_id, repository_id, epoch_id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_config_epoch_activations_previous_epoch
                FOREIGN KEY (installation_id, repository_id, previous_epoch_id)
                REFERENCES {_SCHEMA}.config_epochs (installation_id, repository_id, epoch_id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_config_epoch_activations_audit_event
                FOREIGN KEY (audit_event_id) REFERENCES {_SCHEMA}.audit_events (audit_event_id)
                ON DELETE RESTRICT,
            CONSTRAINT ck_config_epoch_activations_installation_id_safe
                CHECK (installation_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}),
            CONSTRAINT ck_config_epoch_activations_repository_id_safe
                CHECK (repository_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}),
            CONSTRAINT ck_config_epoch_activations_operation_id_limit
                CHECK (octet_length(operation_id) BETWEEN 1 AND 256),
            CONSTRAINT ck_config_epoch_activations_epoch_hashes CHECK (
                target_epoch_id ~ '^[0-9a-f]{{64}}$'
                AND (previous_epoch_id IS NULL OR previous_epoch_id ~ '^[0-9a-f]{{64}}$')
            ),
            CONSTRAINT ck_config_epoch_activations_audit_identity CHECK (
                audit_event_id ~ '^audit_[0-9a-f]{{32}}$'
                AND octet_length(audit_input_hash) = 32
            ),
            CONSTRAINT ck_config_epoch_activations_revision_successor CHECK (
                (expected_revision IS NULL AND previous_epoch_id IS NULL
                    AND previous_revision IS NULL AND result_revision = 1)
                OR (expected_revision >= 1 AND previous_epoch_id IS NOT NULL
                    AND previous_revision = expected_revision
                    AND result_revision = previous_revision + 1)
            ),
            CONSTRAINT ck_config_epoch_activations_revisions_safe CHECK (
                result_revision >= 1 AND result_revision <= {_SAFE_INTEGER_MAX}
                AND (expected_revision IS NULL OR expected_revision <= {_SAFE_INTEGER_MAX})
                AND (previous_revision IS NULL OR previous_revision <= {_SAFE_INTEGER_MAX})
            )
        )
        """,
    )


def _create_proposal_review_registration() -> None:
    _execute(
        f"""
        CREATE TABLE {_SCHEMA}.repository_attestation_transactions (
            transaction_digest bytea NOT NULL,
            session_handle_digest bytea NOT NULL,
            installation_id bigint NOT NULL,
            repository_id bigint NOT NULL,
            operation_id varchar(256) NOT NULL,
            proposal_manifest_id varchar(41) NOT NULL,
            provider_revision varchar(40) NOT NULL,
            proposal_digest varchar(64) NOT NULL,
            expected_active_epoch_id varchar(64),
            expected_active_revision bigint,
            initiating_actor varchar(82) NOT NULL,
            authority_profile_digest varchar(64) NOT NULL,
            issued_at timestamptz NOT NULL,
            expires_at timestamptz NOT NULL,
            CONSTRAINT pk_repository_attestation_transactions
                PRIMARY KEY (transaction_digest),
            CONSTRAINT uq_repository_attestation_transactions_operation
                UNIQUE (installation_id, repository_id, operation_id),
            CONSTRAINT fk_repository_attestation_transactions_session
                FOREIGN KEY (session_handle_digest)
                REFERENCES {_SCHEMA}.control_plane_sessions (handle_digest)
                ON DELETE CASCADE,
            CONSTRAINT fk_repository_attestation_transactions_active_epoch
                FOREIGN KEY (installation_id, repository_id, expected_active_epoch_id)
                REFERENCES {_SCHEMA}.config_epochs (installation_id, repository_id, epoch_id)
                ON DELETE RESTRICT,
            CONSTRAINT ck_repository_attestation_transactions_digests CHECK (
                octet_length(transaction_digest) = 32
                AND octet_length(session_handle_digest) = 32
            ),
            CONSTRAINT ck_repository_attestation_transactions_scope CHECK (
                installation_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}
                AND repository_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}
            ),
            CONSTRAINT ck_repository_attestation_transactions_operation CHECK (
                octet_length(operation_id) BETWEEN 1 AND 256
                AND proposal_manifest_id ~ '^proposal:[0-9a-f]{{32}}$'
            ),
            CONSTRAINT ck_repository_attestation_transactions_proposal CHECK (
                provider_revision ~ '^[0-9a-f]{{40}}$'
                AND proposal_digest ~ '^[0-9a-f]{{64}}$'
            ),
            CONSTRAINT ck_repository_attestation_transactions_active CHECK (
                (expected_active_epoch_id IS NULL AND expected_active_revision IS NULL)
                OR (expected_active_epoch_id ~ '^[0-9a-f]{{64}}$'
                    AND expected_active_revision BETWEEN 1 AND {_SAFE_INTEGER_MAX})
            ),
            CONSTRAINT ck_repository_attestation_transactions_authority CHECK (
                initiating_actor ~ '^keycloak-human:v1:[0-9a-f]{{64}}$'
                AND authority_profile_digest ~ '^[0-9a-f]{{64}}$'
            ),
            CONSTRAINT ck_repository_attestation_transactions_time CHECK (
                isfinite(issued_at)
                AND isfinite(expires_at)
                AND expires_at > issued_at
                AND expires_at <= issued_at + INTERVAL '300 seconds'
            )
        )
        """,
        f"""
        CREATE INDEX ix_repository_attestation_transactions_expiry
        ON {_SCHEMA}.repository_attestation_transactions (expires_at, transaction_digest)
        """,
        f"""
        CREATE INDEX ix_repository_attestation_transactions_session
        ON {_SCHEMA}.repository_attestation_transactions (session_handle_digest, expires_at)
        """,
        f"""
        CREATE TABLE {_SCHEMA}.workflow_proposal_reviews (
            installation_id bigint NOT NULL,
            repository_id bigint NOT NULL,
            operation_id varchar(256) NOT NULL,
            proposal_manifest_id varchar(41) NOT NULL,
            provider_revision varchar(40) NOT NULL,
            inventory_digest varchar(64) NOT NULL,
            proposal_digest varchar(64) NOT NULL,
            base_epoch_id varchar(64),
            base_revision bigint,
            target_epoch_id varchar(64) NOT NULL,
            semantic_diff_version varchar(64) NOT NULL,
            semantic_diff_canonical_json bytea NOT NULL,
            semantic_diff_hash varchar(64) NOT NULL,
            changed_pointer_count smallint NOT NULL,
            attestation_transaction_digest bytea NOT NULL,
            session_handle_digest bytea NOT NULL,
            initiating_actor varchar(82) NOT NULL,
            reviewer_user_id bigint NOT NULL,
            reviewer_login varchar(39) NOT NULL,
            reviewer_permission varchar(8) NOT NULL,
            attestation_issued_at timestamptz NOT NULL,
            permission_observed_at timestamptz NOT NULL,
            receipt_expires_at timestamptz NOT NULL,
            authority_profile_digest varchar(64) NOT NULL,
            audit_event_id varchar(38) NOT NULL,
            audit_input_hash bytea NOT NULL,
            CONSTRAINT pk_workflow_proposal_reviews
                PRIMARY KEY (installation_id, repository_id, operation_id),
            CONSTRAINT uq_workflow_proposal_reviews_scope_manifest
                UNIQUE (installation_id, repository_id, proposal_manifest_id),
            CONSTRAINT uq_workflow_proposal_reviews_audit_event UNIQUE (audit_event_id),
            CONSTRAINT uq_workflow_proposal_reviews_attestation_transaction
                UNIQUE (attestation_transaction_digest),
            CONSTRAINT fk_workflow_proposal_reviews_base_epoch
                FOREIGN KEY (installation_id, repository_id, base_epoch_id)
                REFERENCES {_SCHEMA}.config_epochs (installation_id, repository_id, epoch_id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_workflow_proposal_reviews_target_epoch
                FOREIGN KEY (installation_id, repository_id, target_epoch_id)
                REFERENCES {_SCHEMA}.config_epochs (installation_id, repository_id, epoch_id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_workflow_proposal_reviews_audit_event
                FOREIGN KEY (audit_event_id)
                REFERENCES {_SCHEMA}.audit_events (audit_event_id) ON DELETE RESTRICT,
            CONSTRAINT ck_workflow_proposal_reviews_installation_id_safe
                CHECK (installation_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}),
            CONSTRAINT ck_workflow_proposal_reviews_repository_id_safe
                CHECK (repository_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}),
            CONSTRAINT ck_workflow_proposal_reviews_operation_id_limit
                CHECK (octet_length(operation_id) BETWEEN 1 AND 256),
            CONSTRAINT ck_workflow_proposal_reviews_manifest_id
                CHECK (proposal_manifest_id ~ '^proposal:[0-9a-f]{{32}}$'),
            CONSTRAINT ck_workflow_proposal_reviews_provider_identity CHECK (
                provider_revision ~ '^[0-9a-f]{{40}}$'
                AND inventory_digest ~ '^[0-9a-f]{{64}}$'
                AND proposal_digest ~ '^[0-9a-f]{{64}}$'
            ),
            CONSTRAINT ck_workflow_proposal_reviews_base_shape CHECK (
                (base_epoch_id IS NULL AND base_revision IS NULL)
                OR (base_epoch_id ~ '^[0-9a-f]{{64}}$'
                    AND base_revision BETWEEN 1 AND {_SAFE_INTEGER_MAX})
            ),
            CONSTRAINT ck_workflow_proposal_reviews_target_epoch
                CHECK (target_epoch_id ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_workflow_proposal_reviews_diff_version
                CHECK (semantic_diff_version = 'policy-semantic-diff/v1'),
            CONSTRAINT ck_workflow_proposal_reviews_diff_evidence CHECK (
                octet_length(semantic_diff_canonical_json) BETWEEN 1 AND 65536
                AND semantic_diff_hash ~ '^[0-9a-f]{{64}}$'
                AND changed_pointer_count BETWEEN 0 AND 1024
            ),
            CONSTRAINT ck_workflow_proposal_reviews_audit_identity CHECK (
                audit_event_id ~ '^audit_[0-9a-f]{{32}}$'
                AND octet_length(audit_input_hash) = 32
            ),
            CONSTRAINT ck_workflow_proposal_reviews_attestation_identity CHECK (
                octet_length(attestation_transaction_digest) = 32
                AND octet_length(session_handle_digest) = 32
                AND initiating_actor ~ '^keycloak-human:v1:[0-9a-f]{{64}}$'
                AND reviewer_user_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}
                AND reviewer_login ~ '^[A-Za-z0-9](?:[A-Za-z0-9-]{{0,37}}[A-Za-z0-9])?$'
                AND reviewer_permission ~ '^(admin|maintain)$'
            ),
            CONSTRAINT ck_workflow_proposal_reviews_attestation_time CHECK (
                isfinite(attestation_issued_at)
                AND isfinite(permission_observed_at)
                AND isfinite(receipt_expires_at)
                AND permission_observed_at >= attestation_issued_at
                AND receipt_expires_at > permission_observed_at
                AND receipt_expires_at <= attestation_issued_at + INTERVAL '300 seconds'
                AND authority_profile_digest ~ '^[0-9a-f]{{64}}$'
            )
        )
        """,
    )


def _create_control_plane_identity_state() -> None:
    _execute(
        f"""
        CREATE TABLE {_SCHEMA}.control_plane_sessions (
            handle_digest bytea NOT NULL,
            issuer varchar(512) NOT NULL,
            subject varchar(512) NOT NULL,
            keycloak_sid varchar(512) NOT NULL,
            actor_id varchar(82) NOT NULL,
            roles smallint NOT NULL,
            preferred_username varchar(256),
            display_name varchar(512),
            profile_digest varchar(64) NOT NULL,
            issued_at timestamptz NOT NULL,
            expires_at timestamptz NOT NULL,
            CONSTRAINT pk_control_plane_sessions PRIMARY KEY (handle_digest),
            CONSTRAINT ck_control_plane_sessions_handle_digest
                CHECK (octet_length(handle_digest) = 32),
            CONSTRAINT ck_control_plane_sessions_issuer
                CHECK (octet_length(issuer) BETWEEN 1 AND 512),
            CONSTRAINT ck_control_plane_sessions_subject
                CHECK (octet_length(subject) BETWEEN 1 AND 512),
            CONSTRAINT ck_control_plane_sessions_keycloak_sid
                CHECK (octet_length(keycloak_sid) BETWEEN 1 AND 512),
            CONSTRAINT ck_control_plane_sessions_actor_id
                CHECK (actor_id ~ '^keycloak-human:v1:[0-9a-f]{{64}}$'),
            CONSTRAINT ck_control_plane_sessions_roles CHECK (roles BETWEEN 0 AND 31),
            CONSTRAINT ck_control_plane_sessions_preferred_username CHECK (
                preferred_username IS NULL
                OR octet_length(preferred_username) BETWEEN 1 AND 256
            ),
            CONSTRAINT ck_control_plane_sessions_display_name CHECK (
                display_name IS NULL OR octet_length(display_name) BETWEEN 1 AND 512
            ),
            CONSTRAINT ck_control_plane_sessions_profile_digest
                CHECK (profile_digest ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_control_plane_sessions_time_order CHECK (
                isfinite(issued_at)
                AND isfinite(expires_at)
                AND expires_at > issued_at
                AND expires_at <= issued_at + INTERVAL '900 seconds'
            )
        )
        """,
        f"""
        CREATE INDEX ix_control_plane_sessions_expires_at
        ON {_SCHEMA}.control_plane_sessions (expires_at, handle_digest)
        """,
        f"""
        CREATE INDEX ix_control_plane_sessions_identity_issued
        ON {_SCHEMA}.control_plane_sessions (
            issuer,
            subject,
            issued_at DESC,
            handle_digest DESC
        )
        """,
        f"""
        CREATE INDEX ix_control_plane_sessions_issuer_sid
        ON {_SCHEMA}.control_plane_sessions (issuer, keycloak_sid)
        """,
        f"""
        CREATE TABLE {_SCHEMA}.control_plane_logout_replays (
            issuer varchar(512) NOT NULL,
            jti varchar(512) NOT NULL,
            retain_until timestamptz NOT NULL,
            CONSTRAINT pk_control_plane_logout_replays PRIMARY KEY (issuer, jti),
            CONSTRAINT ck_control_plane_logout_replays_issuer
                CHECK (octet_length(issuer) BETWEEN 1 AND 512),
            CONSTRAINT ck_control_plane_logout_replays_jti
                CHECK (octet_length(jti) BETWEEN 1 AND 512),
            CONSTRAINT ck_control_plane_logout_replays_retain_until
                CHECK (isfinite(retain_until))
        )
        """,
        f"""
        CREATE INDEX ix_control_plane_logout_replays_retain_until
        ON {_SCHEMA}.control_plane_logout_replays (retain_until, issuer, jti)
        """,
    )


def _create_runtime_state() -> None:
    _execute(
        f"""
        CREATE TABLE {_SCHEMA}.webhook_deliveries (
            delivery_id varchar(256) NOT NULL,
            body_sha256 varchar(64) NOT NULL,
            CONSTRAINT pk_webhook_deliveries PRIMARY KEY (delivery_id),
            CONSTRAINT uq_webhook_deliveries_body_sha256 UNIQUE (body_sha256),
            CONSTRAINT ck_webhook_deliveries_delivery_id_byte_limit
                CHECK (octet_length(delivery_id) BETWEEN 1 AND 256),
            CONSTRAINT ck_webhook_deliveries_body_sha256
                CHECK (body_sha256 ~ '^[0-9a-f]{{64}}$')
        )
        """,
        f"""
        CREATE TABLE {_SCHEMA}.production_admission_authorities (
            authority_id varchar(53) NOT NULL,
            key_id varchar(128) NOT NULL,
            public_key_spki_der bytea NOT NULL,
            issued_at timestamptz NOT NULL,
            expires_at timestamptz NOT NULL,
            envelope_canonical_json bytea NOT NULL,
            CONSTRAINT pk_production_admission_authorities PRIMARY KEY (authority_id),
            CONSTRAINT ck_production_admission_authorities_identity
                CHECK (authority_id ~ '^production_admission_[0-9a-f]{{32}}$'),
            CONSTRAINT ck_production_admission_authorities_key_id_shape
                CHECK (key_id ~ '^[A-Za-z0-9._-]{{1,128}}$'),
            CONSTRAINT ck_production_admission_authorities_public_key_ed25519 CHECK (
                octet_length(public_key_spki_der) = 44
                AND substring(public_key_spki_der FROM 1 FOR 12)
                    = decode('302a300506032b6570032100', 'hex')
            ),
            CONSTRAINT ck_production_admission_authorities_envelope_byte_limit
                CHECK (octet_length(envelope_canonical_json) BETWEEN 1 AND 262144),
            CONSTRAINT ck_production_admission_authorities_lifetime
                CHECK (
                    expires_at > issued_at
                    AND expires_at <= issued_at + INTERVAL '7 days'
                )
        )
        """,
        f"""
        CREATE TABLE {_SCHEMA}.production_admission_scope_bindings (
            authority_id varchar(53) NOT NULL,
            installation_id bigint NOT NULL,
            repository_id bigint NOT NULL,
            admission_subject_digest varchar(64) NOT NULL,
            config_epoch_id varchar(64) NOT NULL,
            target_registry_hash varchar(64) NOT NULL,
            CONSTRAINT pk_production_admission_scope_bindings
                PRIMARY KEY (authority_id, installation_id, repository_id),
            CONSTRAINT fk_production_admission_scope_bindings_authority
                FOREIGN KEY (authority_id)
                REFERENCES {_SCHEMA}.production_admission_authorities (authority_id)
                ON DELETE RESTRICT,
            CONSTRAINT ck_production_admission_scope_bindings_scope_safe CHECK (
                installation_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}
                AND repository_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}
            ),
            CONSTRAINT ck_production_admission_scope_bindings_digests CHECK (
                admission_subject_digest ~ '^[0-9a-f]{{64}}$'
                AND config_epoch_id ~ '^[0-9a-f]{{64}}$'
                AND target_registry_hash ~ '^[0-9a-f]{{64}}$'
            )
        )
        """,
        f"""
        CREATE TABLE {_SCHEMA}.issued_plan_envelopes (
            idempotency_key varchar(128) NOT NULL,
            record_id varchar(64) NOT NULL,
            request_hash varchar(64) NOT NULL,
            installation_id bigint NOT NULL,
            repository_id bigint NOT NULL,
            production_admission_authority_id varchar(53),
            issued_at timestamptz NOT NULL,
            envelope_canonical_json bytea NOT NULL,
            CONSTRAINT pk_issued_plan_envelopes PRIMARY KEY (idempotency_key),
            CONSTRAINT uq_issued_plan_envelopes_record_id UNIQUE (record_id),
            CONSTRAINT fk_issued_plan_envelopes_admission_scope
                FOREIGN KEY (
                    production_admission_authority_id,
                    installation_id,
                    repository_id
                )
                REFERENCES {_SCHEMA}.production_admission_scope_bindings (
                    authority_id,
                    installation_id,
                    repository_id
                )
                ON DELETE RESTRICT,
            CONSTRAINT ck_issued_plan_envelopes_idempotency_key_byte_limit
                CHECK (octet_length(idempotency_key) BETWEEN 1 AND 128),
            CONSTRAINT ck_issued_plan_envelopes_record_id_byte_limit
                CHECK (octet_length(record_id) BETWEEN 1 AND 64),
            CONSTRAINT ck_issued_plan_envelopes_record_id_shape
                CHECK (record_id ~ '^issued_plan_[0-9a-f]{{32}}$'),
            CONSTRAINT ck_issued_plan_envelopes_request_hash
                CHECK (request_hash ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_issued_plan_envelopes_scope_safe CHECK (
                installation_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}
                AND repository_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}
            ),
            CONSTRAINT ck_issued_plan_envelopes_authority_identity CHECK (
                production_admission_authority_id IS NULL
                OR production_admission_authority_id
                    ~ '^production_admission_[0-9a-f]{{32}}$'
            ),
            CONSTRAINT ck_issued_plan_envelopes_envelope_byte_limit
                CHECK (octet_length(envelope_canonical_json) BETWEEN 1 AND 1048576)
        )
        """,
        f"""
        CREATE INDEX ix_issued_plan_envelopes_scope_issued
        ON {_SCHEMA}.issued_plan_envelopes (
            installation_id,
            repository_id,
            issued_at,
            record_id
        )
        """,
    )


def _create_shadow_reconciliation_state() -> None:
    _execute(
        f"""
        CREATE TABLE {_SCHEMA}.shadow_evidence (
            profile_id varchar(256) NOT NULL,
            repository varchar(1024) NOT NULL,
            event varchar(1024) NOT NULL,
            surface varchar(1024) NOT NULL,
            record_canonical_json bytea NOT NULL,
            semantic_hash varchar(64) NOT NULL,
            CONSTRAINT pk_shadow_evidence
                PRIMARY KEY (profile_id, repository, event, surface),
            CONSTRAINT ck_shadow_evidence_key_byte_limits CHECK (
                octet_length(profile_id) BETWEEN 1 AND 256
                AND octet_length(repository) BETWEEN 1 AND 1024
                AND octet_length(event) BETWEEN 1 AND 1024
                AND octet_length(surface) BETWEEN 1 AND 1024
            ),
            CONSTRAINT ck_shadow_evidence_record_byte_limit
                CHECK (octet_length(record_canonical_json) BETWEEN 1 AND 1048576),
            CONSTRAINT ck_shadow_evidence_semantic_hash
                CHECK (semantic_hash ~ '^[0-9a-f]{{64}}$')
        )
        """,
        f"""
        CREATE TABLE {_SCHEMA}.reconciliation_subjects (
            subject_id varchar(64) NOT NULL,
            installation_id bigint NOT NULL,
            repository_id bigint NOT NULL,
            subject_canonical_json bytea NOT NULL,
            contract_canonical_json bytea NOT NULL,
            identity_hash varchar(64) NOT NULL,
            contract_hash varchar(64) NOT NULL,
            revision bigint NOT NULL,
            created_at timestamptz NOT NULL,
            deadline_at timestamptz NOT NULL,
            next_attempt_at timestamptz NOT NULL,
            attempt_count bigint NOT NULL,
            max_attempts bigint NOT NULL,
            backoff_seconds bigint NOT NULL,
            max_backoff_seconds bigint NOT NULL,
            claim_generation bigint NOT NULL,
            lease_token varchar(64),
            lease_acquired_at timestamptz,
            lease_expires_at timestamptz,
            CONSTRAINT pk_reconciliation_subjects PRIMARY KEY (subject_id),
            CONSTRAINT ck_reconciliation_subjects_hashes CHECK (
                subject_id ~ '^[0-9a-f]{{64}}$'
                AND identity_hash = subject_id
                AND contract_hash ~ '^[0-9a-f]{{64}}$'
            ),
            CONSTRAINT ck_reconciliation_subjects_scope_safe CHECK (
                installation_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}
                AND repository_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}
            ),
            CONSTRAINT ck_reconciliation_subjects_canonical_byte_limits CHECK (
                octet_length(subject_canonical_json) BETWEEN 1 AND 16384
                AND octet_length(contract_canonical_json) BETWEEN 1 AND 1048576
            ),
            CONSTRAINT ck_reconciliation_subjects_revision_safe
                CHECK (revision BETWEEN 0 AND {_SAFE_INTEGER_MAX}),
            CONSTRAINT ck_reconciliation_subjects_convergence_lifetime CHECK (
                deadline_at > created_at
                AND next_attempt_at >= created_at
                AND next_attempt_at <= deadline_at
                AND deadline_at <= created_at + INTERVAL '86400 seconds'
            ),
            CONSTRAINT ck_reconciliation_subjects_convergence_bounds CHECK (
                attempt_count >= 0
                AND attempt_count <= max_attempts
                AND max_attempts BETWEEN 1 AND 1000
                AND backoff_seconds BETWEEN 1 AND max_backoff_seconds
                AND max_backoff_seconds BETWEEN 1 AND 3600
                AND claim_generation >= attempt_count
                AND claim_generation <= {_SAFE_INTEGER_MAX}
            ),
            CONSTRAINT ck_reconciliation_subjects_lease_shape CHECK (
                (
                    lease_token IS NULL
                    AND lease_acquired_at IS NULL
                    AND lease_expires_at IS NULL
                )
                OR (
                    lease_token IS NOT NULL
                    AND lease_token ~ '^[0-9a-f]{{64}}$'
                    AND lease_acquired_at IS NOT NULL
                    AND lease_expires_at IS NOT NULL
                    AND lease_acquired_at >= created_at
                    AND lease_expires_at > lease_acquired_at
                    AND lease_expires_at <= lease_acquired_at + INTERVAL '3600 seconds'
                )
            )
        )
        """,
        f"""
        CREATE INDEX ix_reconciliation_subjects_due
        ON {_SCHEMA}.reconciliation_subjects (next_attempt_at, subject_id)
        """,
        f"""
        CREATE INDEX ix_reconciliation_subjects_scope_created
        ON {_SCHEMA}.reconciliation_subjects (
            installation_id,
            repository_id,
            created_at,
            subject_id
        )
        """,
        f"""
        CREATE TABLE {_SCHEMA}.reconciliation_observations (
            subject_id varchar(64) NOT NULL,
            observation_id varchar(1024) NOT NULL,
            revision bigint NOT NULL,
            observation_canonical_json bytea NOT NULL,
            semantic_hash varchar(64) NOT NULL,
            CONSTRAINT pk_reconciliation_observations PRIMARY KEY (subject_id, observation_id),
            CONSTRAINT uq_reconciliation_observations_subject_revision
                UNIQUE (subject_id, revision),
            CONSTRAINT fk_reconciliation_observations_subject
                FOREIGN KEY (subject_id)
                REFERENCES {_SCHEMA}.reconciliation_subjects (subject_id)
                ON DELETE RESTRICT,
            CONSTRAINT ck_reconciliation_observations_identity_limits CHECK (
                subject_id ~ '^[0-9a-f]{{64}}$'
                AND octet_length(observation_id) BETWEEN 1 AND 1024
            ),
            CONSTRAINT ck_reconciliation_observations_revision_safe
                CHECK (revision BETWEEN 1 AND {_SAFE_INTEGER_MAX}),
            CONSTRAINT ck_reconciliation_observations_canonical_byte_limit
                CHECK (octet_length(observation_canonical_json) BETWEEN 1 AND 65536),
            CONSTRAINT ck_reconciliation_observations_semantic_hash
                CHECK (semantic_hash ~ '^[0-9a-f]{{64}}$')
        )
        """,
        f"""
        CREATE TABLE {_SCHEMA}.reconciliation_results (
            subject_id varchar(64) NOT NULL,
            revision bigint NOT NULL,
            result_canonical_json bytea NOT NULL,
            semantic_hash varchar(64) NOT NULL,
            CONSTRAINT pk_reconciliation_results PRIMARY KEY (subject_id, revision),
            CONSTRAINT fk_reconciliation_results_subject
                FOREIGN KEY (subject_id)
                REFERENCES {_SCHEMA}.reconciliation_subjects (subject_id)
                ON DELETE RESTRICT,
            CONSTRAINT ck_reconciliation_results_subject_hash
                CHECK (subject_id ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_reconciliation_results_revision_safe
                CHECK (revision BETWEEN 0 AND {_SAFE_INTEGER_MAX}),
            CONSTRAINT ck_reconciliation_results_canonical_byte_limit
                CHECK (octet_length(result_canonical_json) BETWEEN 1 AND 1048576),
            CONSTRAINT ck_reconciliation_results_semantic_hash
                CHECK (semantic_hash ~ '^[0-9a-f]{{64}}$')
        )
        """,
    )


def _create_operator_override_state() -> None:
    _execute(
        f"""
        CREATE TABLE {_SCHEMA}.operator_overrides (
            override_id varchar(41) NOT NULL,
            operation_id varchar(512) NOT NULL,
            installation_id bigint NOT NULL,
            repository_id bigint NOT NULL,
            kind varchar(32) NOT NULL,
            target_subject_id varchar(512),
            expires_at timestamptz,
            applied_at timestamptz NOT NULL,
            record_canonical_json bytea NOT NULL,
            semantic_hash varchar(64) NOT NULL,
            audit_event_id varchar(38) NOT NULL,
            audit_input_hash bytea NOT NULL,
            CONSTRAINT pk_operator_overrides PRIMARY KEY (override_id),
            CONSTRAINT uq_operator_overrides_operation
                UNIQUE (installation_id, repository_id, operation_id),
            CONSTRAINT uq_operator_overrides_audit_event UNIQUE (audit_event_id),
            CONSTRAINT fk_operator_overrides_audit_event
                FOREIGN KEY (audit_event_id) REFERENCES {_SCHEMA}.audit_events (audit_event_id)
                ON DELETE RESTRICT,
            CONSTRAINT ck_operator_overrides_identity CHECK (
                override_id ~ '^override_[0-9a-f]{{32}}$'
                AND octet_length(operation_id) BETWEEN 1 AND 512
            ),
            CONSTRAINT ck_operator_overrides_scope_safe CHECK (
                installation_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}
                AND repository_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}
            ),
            CONSTRAINT ck_operator_overrides_kind_target CHECK (
                (kind = 'force_full_ci' AND target_subject_id IS NOT NULL
                    AND octet_length(target_subject_id) BETWEEN 1 AND 512
                    AND expires_at IS NOT NULL)
                OR (kind = 'disable_omission' AND target_subject_id IS NULL
                    AND expires_at IS NULL)
                OR (kind = 'enable_omission'
                    AND target_subject_id ~ '^override_[0-9a-f]{{32}}$'
                    AND expires_at IS NULL)
            ),
            CONSTRAINT ck_operator_overrides_lifetime
                CHECK (expires_at IS NULL OR expires_at > applied_at),
            CONSTRAINT ck_operator_overrides_record_byte_limit
                CHECK (octet_length(record_canonical_json) BETWEEN 1 AND 8192),
            CONSTRAINT ck_operator_overrides_semantic_hash
                CHECK (semantic_hash ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_operator_overrides_audit_identity CHECK (
                audit_event_id ~ '^audit_[0-9a-f]{{32}}$'
                AND octet_length(audit_input_hash) = 32
            )
        )
        """,
        f"""
        CREATE INDEX ix_operator_overrides_resolution
        ON {_SCHEMA}.operator_overrides (
            installation_id,
            repository_id,
            kind,
            target_subject_id,
            applied_at DESC,
            override_id DESC
        )
        """,
    )


def _create_governance_baseline_state() -> None:
    _execute(
        f"""
        CREATE TABLE {_SCHEMA}.governance_baselines (
            installation_id bigint NOT NULL,
            repository_id bigint NOT NULL,
            version bigint NOT NULL,
            baseline_id varchar(84) NOT NULL,
            operation_id varchar(256) NOT NULL,
            state_digest varchar(64) NOT NULL,
            state_canonical_json bytea NOT NULL,
            observed_at timestamptz NOT NULL,
            approved_at timestamptz NOT NULL,
            actor varchar(256) NOT NULL,
            reason varchar(1024) NOT NULL,
            supersedes_baseline_id varchar(84),
            supersedes_version bigint,
            supersedes_state_digest varchar(64),
            audit_event_id varchar(38) NOT NULL,
            audit_input_hash bytea NOT NULL,
            CONSTRAINT pk_governance_baselines
                PRIMARY KEY (installation_id, repository_id, version),
            CONSTRAINT uq_governance_baselines_baseline_id UNIQUE (baseline_id),
            CONSTRAINT uq_governance_baselines_scope_operation
                UNIQUE (installation_id, repository_id, operation_id),
            CONSTRAINT uq_governance_baselines_scope_version_identity
                UNIQUE (installation_id, repository_id, version, baseline_id),
            CONSTRAINT uq_governance_baselines_audit_event UNIQUE (audit_event_id),
            CONSTRAINT fk_governance_baselines_predecessor
                FOREIGN KEY (
                    installation_id,
                    repository_id,
                    supersedes_version,
                    supersedes_baseline_id
                )
                REFERENCES {_SCHEMA}.governance_baselines (
                    installation_id,
                    repository_id,
                    version,
                    baseline_id
                ) ON DELETE RESTRICT,
            CONSTRAINT fk_governance_baselines_audit_event
                FOREIGN KEY (audit_event_id)
                REFERENCES {_SCHEMA}.audit_events (audit_event_id) ON DELETE RESTRICT,
            CONSTRAINT ck_governance_baselines_installation_id_safe
                CHECK (installation_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}),
            CONSTRAINT ck_governance_baselines_repository_id_safe
                CHECK (repository_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}),
            CONSTRAINT ck_governance_baselines_chain_shape CHECK (
                (
                    version = 1
                    AND supersedes_baseline_id IS NULL
                    AND supersedes_version IS NULL
                    AND supersedes_state_digest IS NULL
                )
                OR (
                    version BETWEEN 2 AND {_SAFE_INTEGER_MAX}
                    AND supersedes_baseline_id
                        ~ '^governance-baseline:[0-9a-f]{{64}}$'
                    AND supersedes_version = version - 1
                    AND supersedes_state_digest ~ '^[0-9a-f]{{64}}$'
                )
            ),
            CONSTRAINT ck_governance_baselines_identity CHECK (
                baseline_id ~ '^governance-baseline:[0-9a-f]{{64}}$'
                AND state_digest ~ '^[0-9a-f]{{64}}$'
            ),
            CONSTRAINT ck_governance_baselines_operation_id_limit
                CHECK (octet_length(operation_id) BETWEEN 1 AND 256),
            CONSTRAINT ck_governance_baselines_state_bytes CHECK (
                octet_length(state_canonical_json)
                BETWEEN 1 AND {MAX_GOVERNANCE_STATE_BYTES}
            ),
            CONSTRAINT ck_governance_baselines_time_order
                CHECK (observed_at <= approved_at),
            CONSTRAINT ck_governance_baselines_approval_text CHECK (
                octet_length(actor) BETWEEN 1 AND 256
                AND octet_length(reason) BETWEEN 1 AND 1024
                AND reason = btrim(reason)
            ),
            CONSTRAINT ck_governance_baselines_audit_identity CHECK (
                audit_event_id ~ '^audit_[0-9a-f]{{32}}$'
                AND octet_length(audit_input_hash) = 32
            )
        )
        """,
        f"CREATE FUNCTION {_SCHEMA}.reject_governance_baseline_mutation() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "RAISE EXCEPTION 'governance baseline history is immutable' "
        "USING ERRCODE = '42501'; END; $$",
        f"""
        CREATE TRIGGER tr_governance_baselines_immutable
        BEFORE UPDATE OR DELETE ON {_SCHEMA}.governance_baselines
        FOR EACH ROW EXECUTE FUNCTION {_SCHEMA}.reject_governance_baseline_mutation()
        """,
        f"""
        CREATE TABLE {_SCHEMA}.governance_baseline_operations (
            installation_id bigint NOT NULL,
            repository_id bigint NOT NULL,
            operation_id varchar(256) NOT NULL,
            command_canonical_json bytea NOT NULL,
            result_kind varchar(9) NOT NULL,
            result_baseline_id varchar(84) NOT NULL,
            result_version bigint NOT NULL,
            recorded_at timestamptz NOT NULL,
            CONSTRAINT pk_governance_baseline_operations
                PRIMARY KEY (installation_id, repository_id, operation_id),
            CONSTRAINT fk_governance_baseline_operations_result
                FOREIGN KEY (
                    installation_id,
                    repository_id,
                    result_version,
                    result_baseline_id
                )
                REFERENCES {_SCHEMA}.governance_baselines (
                    installation_id,
                    repository_id,
                    version,
                    baseline_id
                ) ON DELETE RESTRICT,
            CONSTRAINT ck_governance_baseline_operations_installation_id_safe
                CHECK (installation_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}),
            CONSTRAINT ck_governance_baseline_operations_repository_id_safe
                CHECK (repository_id BETWEEN 1 AND {_SAFE_INTEGER_MAX}),
            CONSTRAINT ck_governance_baseline_operations_operation_id_limit
                CHECK (octet_length(operation_id) BETWEEN 1 AND 256),
            CONSTRAINT ck_governance_baseline_operations_command_bytes CHECK (
                octet_length(command_canonical_json)
                BETWEEN 1 AND {MAX_BASELINE_COMMAND_BYTES}
            ),
            CONSTRAINT ck_governance_baseline_operations_result CHECK (
                result_kind IN ('accepted', 'unchanged')
                AND result_baseline_id ~ '^governance-baseline:[0-9a-f]{{64}}$'
                AND result_version BETWEEN 1 AND {_SAFE_INTEGER_MAX}
            )
        )
        """,
        f"""
        CREATE TRIGGER tr_governance_baseline_operations_immutable
        BEFORE UPDATE OR DELETE ON {_SCHEMA}.governance_baseline_operations
        FOR EACH ROW EXECUTE FUNCTION {_SCHEMA}.reject_governance_baseline_mutation()
        """,
    )


def _seed_pristine_audit_head() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            f"INSERT INTO {_SCHEMA}.audit_ledger_head "
            "(head_id, revision, last_sequence, last_event_hash) VALUES (1, 0, NULL, NULL)"
        )
    )


def _publish_baseline_declaration() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            f"INSERT INTO {_SCHEMA}.database_compatibility_declarations "
            "(generation, revision_id, parent_revision_id, transition_kind, lineage_id, "
            "protocol_version, declaration_hash) VALUES "
            "(1, :revision_id, NULL, 'bootstrap', 'ci-coordinator-postgresql/v1', 1, "
            ":declaration_hash)"
        ),
        {"revision_id": revision, "declaration_hash": _DECLARATION_HASH},
    )
    bind.execute(
        sa.text(
            f"INSERT INTO {_SCHEMA}.database_compatibility_capabilities "
            "(revision_id, capability_id, descriptor_hash) "
            "VALUES (:revision_id, :capability_id, :descriptor_hash)"
        ),
        [
            {
                "revision_id": revision,
                "capability_id": capability_id,
                "descriptor_hash": descriptor_hash,
            }
            for capability_id, descriptor_hash in _CAPABILITIES
        ],
    )


def _install_immutability_guards() -> None:
    _execute(
        f"CREATE FUNCTION {_SCHEMA}.reject_compatibility_mutation() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "RAISE EXCEPTION 'database compatibility history is append-only' "
        "USING ERRCODE = '42501'; END; $$",
        f"""
        CREATE TRIGGER tr_database_compatibility_declarations_immutable
        BEFORE UPDATE OR DELETE ON {_SCHEMA}.database_compatibility_declarations
        FOR EACH ROW EXECUTE FUNCTION {_SCHEMA}.reject_compatibility_mutation()
        """,
        f"""
        CREATE TRIGGER tr_database_compatibility_capabilities_immutable
        BEFORE UPDATE OR DELETE ON {_SCHEMA}.database_compatibility_capabilities
        FOR EACH ROW EXECUTE FUNCTION {_SCHEMA}.reject_compatibility_mutation()
        """,
        f"CREATE FUNCTION {_SCHEMA}.reject_config_epoch_mutation() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "RAISE EXCEPTION 'config epoch history is immutable' "
        "USING ERRCODE = '42501'; END; $$",
        f"""
        CREATE TRIGGER tr_config_epochs_immutable
        BEFORE UPDATE OR DELETE ON {_SCHEMA}.config_epochs
        FOR EACH ROW EXECUTE FUNCTION {_SCHEMA}.reject_config_epoch_mutation()
        """,
        f"""
        CREATE TRIGGER tr_config_epoch_activations_immutable
        BEFORE UPDATE OR DELETE ON {_SCHEMA}.config_epoch_activations
        FOR EACH ROW EXECUTE FUNCTION {_SCHEMA}.reject_config_epoch_mutation()
        """,
        f"CREATE FUNCTION {_SCHEMA}.reject_proposal_review_mutation() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "RAISE EXCEPTION 'workflow proposal review history is immutable' "
        "USING ERRCODE = '42501'; END; $$",
        f"""
        CREATE TRIGGER tr_workflow_proposal_reviews_immutable
        BEFORE UPDATE OR DELETE ON {_SCHEMA}.workflow_proposal_reviews
        FOR EACH ROW EXECUTE FUNCTION {_SCHEMA}.reject_proposal_review_mutation()
        """,
        f"CREATE FUNCTION {_SCHEMA}.reject_control_plane_session_mutation() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "RAISE EXCEPTION 'control-plane session identity is immutable' "
        "USING ERRCODE = '42501'; END; $$",
        f"""
        CREATE TRIGGER tr_control_plane_sessions_immutable
        BEFORE UPDATE ON {_SCHEMA}.control_plane_sessions
        FOR EACH ROW EXECUTE FUNCTION {_SCHEMA}.reject_control_plane_session_mutation()
        """,
    )


def _configure_extended_storage() -> None:
    for column in (
        "idempotency_key",
        "subject_id",
        "event_type",
        "actor",
        "payload_canonical_json",
    ):
        op.execute(
            sa.text(
                f"ALTER TABLE {_SCHEMA}.audit_events ALTER COLUMN {column} SET STORAGE EXTENDED"
            )
        )
    op.execute(
        sa.text(
            f"ALTER TABLE {_SCHEMA}.workflow_proposal_reviews "
            "ALTER COLUMN semantic_diff_canonical_json SET STORAGE EXTENDED"
        )
    )
    op.execute(
        sa.text(
            f"ALTER TABLE {_SCHEMA}.governance_baselines "
            "ALTER COLUMN state_canonical_json SET STORAGE EXTENDED"
        )
    )
    op.execute(
        sa.text(
            f"ALTER TABLE {_SCHEMA}.governance_baseline_operations "
            "ALTER COLUMN command_canonical_json SET STORAGE EXTENDED"
        )
    )


def _revoke_public_access() -> None:
    # Array types derive privileges from their element type and reject direct ACL changes.
    _execute(
        f"REVOKE ALL ON SCHEMA {_SCHEMA} FROM PUBLIC",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA {_SCHEMA} FROM PUBLIC",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {_SCHEMA} FROM PUBLIC",
        f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {_SCHEMA} FROM PUBLIC",
        """
        DO $$
        DECLARE type_name text;
        BEGIN
            FOR type_name IN
                SELECT typname FROM pg_type
                WHERE typnamespace = 'ci_coordinator'::regnamespace AND typelem = 0
            LOOP
                EXECUTE format(
                    'REVOKE ALL ON TYPE ci_coordinator.%I FROM PUBLIC',
                    type_name
                );
            END LOOP;
        END;
        $$
        """,
    )


def _attest_baseline_catalog_and_privileges() -> None:
    bind = op.get_bind()
    declared_capabilities = tuple(capability_id for capability_id, _digest in _CAPABILITIES)
    catalog_capabilities = tuple(capability_id for capability_id, _tables in _CAPABILITY_TABLES)
    if declared_capabilities != catalog_capabilities:
        raise RuntimeError("baseline catalog attestation does not cover every declaration")

    actual_tables = tuple(
        row[0]
        for row in bind.execute(
            sa.text(
                "SELECT relation.relname FROM pg_class AS relation "
                "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "WHERE namespace.nspname = :schema AND relation.relkind = 'r' "
                "ORDER BY relation.relname"
            ),
            {"schema": _SCHEMA},
        )
    )
    if actual_tables != tuple(sorted(_ALL_TABLES)):
        raise RuntimeError("baseline catalog attestation failed: capability tables")

    for label, statement, expected_count, expected_digest in _CATALOG_PROJECTIONS:
        facts = tuple(tuple(row) for row in bind.execute(sa.text(statement), {"schema": _SCHEMA}))
        serialized = json.dumps(facts, ensure_ascii=True, separators=(",", ":"))
        digest = sha256(serialized.encode("ascii")).hexdigest()
        if len(facts) != expected_count or digest != expected_digest:
            raise RuntimeError(
                "baseline catalog attestation failed: "
                f"{label} expected {expected_count}/{expected_digest}, "
                f"observed {len(facts)}/{digest}"
            )

    if not proposal_review_registration_schema_matches_contract_sync(bind):
        raise RuntimeError("baseline proposal-review schema attestation failed")
    repository_attestation_mismatches = repository_attestation_schema_mismatches_sync(bind)
    if repository_attestation_mismatches:
        raise RuntimeError(
            "baseline repository-attestation schema attestation failed: "
            + ",".join(repository_attestation_mismatches)
        )
    if not control_plane_identity_schema_matches_contract_sync(bind):
        raise RuntimeError("baseline control-plane identity schema attestation failed")
    if not governance_baseline_schema_matches_contract_sync(bind):
        raise RuntimeError("baseline governance schema attestation failed")
    if not webhook_body_identity_schema_matches_contract_sync(bind):
        raise RuntimeError("baseline webhook body identity schema attestation failed")

    if bind.scalar(sa.text(_PUBLIC_ACCESS_ATTESTATION), {"schema": _SCHEMA}) is not True:
        raise RuntimeError("baseline privilege attestation failed: PUBLIC object access")
    if bind.scalar(sa.text(_SCHEMA_DEFAULT_ACL_ATTESTATION), {"schema": _SCHEMA}) is not True:
        raise RuntimeError("baseline privilege attestation failed: schema-local default ACL")


_PUBLIC_ACCESS_ATTESTATION: Final = """
SELECT EXISTS (
    SELECT 1 FROM pg_namespace AS namespace
    JOIN pg_roles AS owner ON owner.oid = namespace.nspowner
    WHERE namespace.nspname = :schema AND owner.rolname = current_user
) AND NOT EXISTS (
    SELECT 1 FROM pg_namespace AS namespace
    CROSS JOIN LATERAL aclexplode(
        COALESCE(namespace.nspacl, acldefault('n', namespace.nspowner))
    ) AS privilege
    WHERE namespace.nspname = :schema AND privilege.grantee = 0
) AND NOT EXISTS (
    SELECT 1 FROM pg_class AS relation
    JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
    CROSS JOIN LATERAL aclexplode(
        COALESCE(
            relation.relacl,
            acldefault(
                (CASE WHEN relation.relkind = 'S' THEN 's' ELSE 'r' END)::"char",
                relation.relowner
            )
        )
    ) AS privilege
    WHERE namespace.nspname = :schema
      AND relation.relkind IN ('r', 'S', 'v', 'm', 'p', 'f')
      AND privilege.grantee = 0
) AND NOT EXISTS (
    SELECT 1 FROM pg_proc AS routine
    JOIN pg_namespace AS namespace ON namespace.oid = routine.pronamespace
    CROSS JOIN LATERAL aclexplode(
        COALESCE(routine.proacl, acldefault('f', routine.proowner))
    ) AS privilege
    WHERE namespace.nspname = :schema
      AND (privilege.grantee = 0 OR routine.prosecdef)
) AND NOT EXISTS (
    SELECT 1 FROM pg_type AS type_metadata
    JOIN pg_namespace AS namespace ON namespace.oid = type_metadata.typnamespace
    CROSS JOIN LATERAL aclexplode(
        COALESCE(type_metadata.typacl, acldefault('T', type_metadata.typowner))
    ) AS privilege
    WHERE namespace.nspname = :schema
      AND type_metadata.typelem = 0
      AND privilege.grantee = 0
)
"""


_SCHEMA_DEFAULT_ACL_ATTESTATION: Final = """
SELECT NOT EXISTS (
    SELECT 1 FROM pg_default_acl AS default_acl
    JOIN pg_namespace AS namespace ON namespace.oid = default_acl.defaclnamespace
    CROSS JOIN LATERAL aclexplode(default_acl.defaclacl) AS privilege
    WHERE default_acl.defaclrole = (SELECT oid FROM pg_roles WHERE rolname = current_user)
      AND namespace.nspname = :schema
      AND privilege.grantee = 0
)
"""


def _lock_baseline_tables() -> None:
    relations = ", ".join(f"{_SCHEMA}.{table}" for table in _ALL_TABLES)
    op.get_bind().execute(sa.text(f"LOCK TABLE {relations} IN ACCESS EXCLUSIVE MODE"))


def _assert_prepublication_state() -> None:
    bind = op.get_bind()
    state = bind.execute(
        sa.text(
            f"SELECT "
            f"(SELECT count(*) FROM {_SCHEMA}.database_compatibility_declarations), "
            f"(SELECT count(*) FROM {_SCHEMA}.database_compatibility_capabilities), "
            f"(SELECT count(*) FROM {_SCHEMA}.audit_ledger_head), "
            f"(SELECT count(*) FROM {_SCHEMA}.audit_ledger_head "
            " WHERE head_id = 1 AND revision = 0 "
            " AND last_sequence IS NULL AND last_event_hash IS NULL)"
        )
    ).one()
    retained = tuple(
        table
        for table in _RETAINED_DATA_TABLES
        if bind.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {_SCHEMA}.{table})")) is True
    )
    if tuple(state) != (0, 0, 1, 1) or retained:
        raise RuntimeError("baseline prepublication state attestation failed")


def _assert_exact_published_baseline() -> None:
    bind = op.get_bind()
    declarations = tuple(
        tuple(row)
        for row in bind.execute(
            sa.text(
                f"SELECT generation, revision_id, parent_revision_id, transition_kind, "
                f"lineage_id, protocol_version, declaration_hash FROM {_SCHEMA}."
                "database_compatibility_declarations ORDER BY generation"
            )
        )
    )
    expected_declarations = (
        (
            1,
            revision,
            None,
            "bootstrap",
            "ci-coordinator-postgresql/v1",
            1,
            _DECLARATION_HASH,
        ),
    )
    capabilities = tuple(
        tuple(row)
        for row in bind.execute(
            sa.text(
                f"SELECT revision_id, capability_id, descriptor_hash FROM {_SCHEMA}."
                "database_compatibility_capabilities ORDER BY revision_id, capability_id"
            )
        )
    )
    expected_capabilities = tuple(
        (revision, capability_id, descriptor_hash)
        for capability_id, descriptor_hash in _CAPABILITIES
    )
    if declarations != expected_declarations or capabilities != expected_capabilities:
        raise RuntimeError("refusing to downgrade a non-exact baseline declaration")


def _drop_baseline_schema() -> None:
    _execute(
        f"DROP TRIGGER tr_control_plane_sessions_immutable ON {_SCHEMA}.control_plane_sessions",
        f"DROP TRIGGER tr_governance_baseline_operations_immutable "
        f"ON {_SCHEMA}.governance_baseline_operations",
        f"DROP TRIGGER tr_governance_baselines_immutable ON {_SCHEMA}.governance_baselines",
        f"DROP TRIGGER tr_workflow_proposal_reviews_immutable "
        f"ON {_SCHEMA}.workflow_proposal_reviews",
        f"DROP TRIGGER tr_config_epoch_activations_immutable ON {_SCHEMA}.config_epoch_activations",
        f"DROP TRIGGER tr_config_epochs_immutable ON {_SCHEMA}.config_epochs",
        f"DROP TRIGGER tr_database_compatibility_capabilities_immutable "
        f"ON {_SCHEMA}.database_compatibility_capabilities",
        f"DROP TRIGGER tr_database_compatibility_declarations_immutable "
        f"ON {_SCHEMA}.database_compatibility_declarations",
        f"DROP FUNCTION {_SCHEMA}.reject_proposal_review_mutation()",
        f"DROP FUNCTION {_SCHEMA}.reject_control_plane_session_mutation()",
        f"DROP FUNCTION {_SCHEMA}.reject_governance_baseline_mutation()",
        f"DROP FUNCTION {_SCHEMA}.reject_config_epoch_mutation()",
        f"DROP FUNCTION {_SCHEMA}.reject_compatibility_mutation()",
        f"DROP INDEX {_SCHEMA}.ix_repository_attestation_transactions_session",
        f"DROP INDEX {_SCHEMA}.ix_repository_attestation_transactions_expiry",
        f"DROP TABLE {_SCHEMA}.repository_attestation_transactions",
        f"DROP INDEX {_SCHEMA}.ix_control_plane_logout_replays_retain_until",
        f"DROP TABLE {_SCHEMA}.control_plane_logout_replays",
        f"DROP INDEX {_SCHEMA}.ix_control_plane_sessions_issuer_sid",
        f"DROP INDEX {_SCHEMA}.ix_control_plane_sessions_identity_issued",
        f"DROP INDEX {_SCHEMA}.ix_control_plane_sessions_expires_at",
        f"DROP TABLE {_SCHEMA}.control_plane_sessions",
        f"DROP TABLE {_SCHEMA}.workflow_proposal_reviews",
        f"DROP TABLE {_SCHEMA}.governance_baseline_operations",
        f"DROP TABLE {_SCHEMA}.governance_baselines",
        f"DROP INDEX {_SCHEMA}.ix_operator_overrides_resolution",
        f"DROP TABLE {_SCHEMA}.operator_overrides",
        f"DROP TABLE {_SCHEMA}.reconciliation_results",
        f"DROP TABLE {_SCHEMA}.reconciliation_observations",
        f"DROP INDEX {_SCHEMA}.ix_reconciliation_subjects_scope_created",
        f"DROP INDEX {_SCHEMA}.ix_reconciliation_subjects_due",
        f"DROP TABLE {_SCHEMA}.reconciliation_subjects",
        f"DROP TABLE {_SCHEMA}.shadow_evidence",
        f"DROP INDEX {_SCHEMA}.ix_issued_plan_envelopes_scope_issued",
        f"DROP TABLE {_SCHEMA}.issued_plan_envelopes",
        f"DROP TABLE {_SCHEMA}.production_admission_scope_bindings",
        f"DROP TABLE {_SCHEMA}.production_admission_authorities",
        f"DROP TABLE {_SCHEMA}.webhook_deliveries",
        f"DROP TABLE {_SCHEMA}.config_epoch_activations",
        f"DROP TABLE {_SCHEMA}.active_config_epochs",
        f"DROP TABLE {_SCHEMA}.config_epochs",
        f"DROP TABLE {_SCHEMA}.database_compatibility_capabilities",
        f"DROP TABLE {_SCHEMA}.database_compatibility_declarations",
        f"DROP TABLE {_SCHEMA}.audit_ledger_head",
        f"DROP INDEX {_SCHEMA}.ix_audit_events_scope_sequence",
        f"DROP TABLE {_SCHEMA}.audit_events",
        f"DROP SCHEMA {_SCHEMA}",
    )


def _assert_no_retained_product_data() -> None:
    bind = op.get_bind()
    retained = tuple(
        table
        for table in _RETAINED_DATA_TABLES
        if bind.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {_SCHEMA}.{table})")) is True
    )
    pristine_head = bind.scalar(
        sa.text(
            f"SELECT count(*) = 1 FROM {_SCHEMA}.audit_ledger_head "
            "WHERE head_id = 1 AND revision = 0 "
            "AND last_sequence IS NULL AND last_event_hash IS NULL"
        )
    )
    if retained or pristine_head is not True:
        raise RuntimeError("refusing to remove retained CI Coordinator data")


def _execute(*statements: str) -> None:
    for statement in statements:
        op.execute(sa.text(statement))
