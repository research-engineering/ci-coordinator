from typing import Final

type ColumnFact = tuple[str, str, str, bool, str | None]

PRODUCTION_COLUMNS: Final[tuple[ColumnFact, ...]] = (
    ("production_evidence_bundles", "bundle_digest", "character varying(64)", False, None),
    ("production_evidence_bundles", "content_sha256", "character varying(64)", False, None),
    ("production_evidence_bundles", "canonical_json", "bytea", False, None),
    ("production_evidence_bundles", "byte_count", "bigint", False, None),
    ("production_evidence_bundles", "retained_at", "timestamp with time zone", False, None),
    ("production_scope_states", "installation_id", "bigint", False, None),
    ("production_scope_states", "repository_id", "bigint", False, None),
    ("production_scope_states", "revision", "bigint", False, None),
    ("production_scope_states", "generation", "bigint", False, None),
    ("production_scope_states", "revoked_through_generation", "bigint", False, None),
    ("production_scope_states", "active_authority_id", "character varying(53)", True, None),
    ("production_scope_states", "staged_authority_id", "character varying(53)", True, None),
    ("production_scope_states", "latch_override_id", "character varying(41)", True, None),
    ("production_scope_states", "latch_applied_at", "timestamp with time zone", True, None),
    ("production_staged_grants", "installation_id", "bigint", False, None),
    ("production_staged_grants", "repository_id", "bigint", False, None),
    ("production_staged_grants", "authority_id", "character varying(53)", False, None),
    ("production_staged_grants", "generation", "bigint", False, None),
    ("production_staged_grants", "bundle_digest", "character varying(64)", False, None),
    ("production_staged_grants", "lookup_canonical_json", "bytea", False, None),
    ("production_staged_grants", "staged_at", "timestamp with time zone", False, None),
)
PRODUCTION_CONSTRAINTS: Final = {
    ("production_evidence_bundles", "production_evidence_bundles_pkey"): (
        "p",
        "PRIMARY KEY (bundle_digest)",
    ),
    ("production_evidence_bundles", "ck_production_evidence_bundles_identity"): (
        "c",
        "CHECK (bundle_digest::text ~ '^[0-9a-f]{64}$'::text AND "
        "content_sha256::text = encode(sha256(canonical_json), 'hex'::text))",
    ),
    ("production_evidence_bundles", "ck_production_evidence_bundles_bytes"): (
        "c",
        "CHECK (byte_count >= 1 AND byte_count <= 8388608 AND "
        "byte_count = octet_length(canonical_json))",
    ),
    ("production_staged_grants", "pk_production_staged_grants"): (
        "p",
        "PRIMARY KEY (installation_id, repository_id, authority_id)",
    ),
    ("production_staged_grants", "uq_production_staged_grants_generation"): (
        "u",
        "UNIQUE (installation_id, repository_id, authority_id, generation)",
    ),
    ("production_staged_grants", "fk_production_staged_grants_scope"): (
        "f",
        "FOREIGN KEY (authority_id, installation_id, repository_id) REFERENCES "
        "ci_coordinator.production_admission_scope_bindings(authority_id, "
        "installation_id, repository_id) ON DELETE RESTRICT",
    ),
    ("production_staged_grants", "fk_production_staged_grants_bundle"): (
        "f",
        "FOREIGN KEY (bundle_digest) REFERENCES "
        "ci_coordinator.production_evidence_bundles(bundle_digest) ON DELETE RESTRICT",
    ),
    ("production_staged_grants", "ck_production_staged_grants_generation"): (
        "c",
        "CHECK (generation >= 1 AND generation <= '9007199254740991'::bigint)",
    ),
    ("production_staged_grants", "ck_production_staged_grants_lookup_bytes"): (
        "c",
        "CHECK (octet_length(lookup_canonical_json) >= 1 AND "
        "octet_length(lookup_canonical_json) <= 262144)",
    ),
    ("production_scope_states", "pk_production_scope_states"): (
        "p",
        "PRIMARY KEY (installation_id, repository_id)",
    ),
    ("production_scope_states", "fk_production_scope_states_active"): (
        "f",
        "FOREIGN KEY (installation_id, repository_id, active_authority_id, generation) "
        "REFERENCES ci_coordinator.production_staged_grants(installation_id, repository_id, "
        "authority_id, generation) ON DELETE RESTRICT",
    ),
    ("production_scope_states", "fk_production_scope_states_staged"): (
        "f",
        "FOREIGN KEY (installation_id, repository_id, staged_authority_id) "
        "REFERENCES ci_coordinator.production_staged_grants(installation_id, "
        "repository_id, authority_id) ON DELETE RESTRICT",
    ),
    ("production_scope_states", "fk_production_scope_states_latch"): (
        "f",
        "FOREIGN KEY (latch_override_id) REFERENCES "
        "ci_coordinator.operator_overrides(override_id) ON DELETE RESTRICT",
    ),
    ("production_scope_states", "ck_production_scope_states_counters"): (
        "c",
        "CHECK (installation_id >= 1 AND installation_id <= '9007199254740991'::bigint "
        "AND repository_id >= 1 AND repository_id <= '9007199254740991'::bigint AND "
        "revision >= 1 AND revision <= '9007199254740991'::bigint AND generation >= 0 "
        "AND generation <= '9007199254740991'::bigint AND revoked_through_generation >= 0 "
        "AND revoked_through_generation <= generation)",
    ),
    ("production_scope_states", "ck_production_scope_states_active"): (
        "c",
        "CHECK (generation = 0 AND active_authority_id IS NULL OR "
        "generation > 0 AND active_authority_id IS NOT NULL)",
    ),
    ("production_scope_states", "ck_production_scope_states_latch"): (
        "c",
        "CHECK (latch_override_id IS NULL AND latch_applied_at IS NULL OR "
        "latch_override_id IS NOT NULL AND latch_applied_at IS NOT NULL AND "
        "revoked_through_generation = generation)",
    ),
}

EXPIRY_COLUMNS: Final[tuple[ColumnFact, ...]] = (
    ("issued_plan_envelopes", "expires_at", "timestamp with time zone", False, None),
)
ORIGIN_COLUMNS: Final[tuple[ColumnFact, ...]] = (
    (
        "reconciliation_subjects",
        "execution_origin",
        "character varying(8)",
        False,
        "'legacy'::character varying",
    ),
    ("reconciliation_subjects", "production_generation", "bigint", True, None),
    ("reconciliation_subjects", "production_authority_id", "character varying(53)", True, None),
)
EXPIRY_CONSTRAINTS: Final = {
    ("issued_plan_envelopes", "ck_issued_plan_envelopes_exact_expiry"): (
        "c",
        "CHECK ((isfinite(expires_at) AND expires_at > issued_at AND "
        "jsonb_typeof(convert_from(envelope_canonical_json, 'UTF8'::name)::jsonb -> "
        "'expiresAt'::text) = 'string'::text AND "
        "(convert_from(envelope_canonical_json, 'UTF8'::name)::jsonb ->> 'expiresAt'::text) "
        "~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
        "(\\.[0-9]{6})?[+-][0-9]{2}:[0-9]{2}(:[0-9]{2}(\\.[0-9]{6})?)?$'::text AND "
        "expires_at = ((convert_from(envelope_canonical_json, 'UTF8'::name)::jsonb ->> "
        "'expiresAt'::text)::timestamp with time zone)) IS TRUE)",
    ),
}
ORIGIN_CONSTRAINTS: Final = {
    ("reconciliation_subjects", "ck_reconciliation_subjects_production_origin"): (
        "c",
        "CHECK ((execution_origin::text = ANY (ARRAY['legacy'::character varying, "
        "'full_ci'::character varying]::text[])) AND production_generation IS NULL AND "
        "production_authority_id IS NULL OR execution_origin::text = 'selected'::text AND "
        "production_generation IS NOT NULL AND production_generation >= 1 AND "
        "production_generation <= '9007199254740991'::bigint AND "
        "production_authority_id IS NOT NULL)",
    ),
    ("reconciliation_subjects", "fk_reconciliation_subjects_production_generation"): (
        "f",
        "FOREIGN KEY (installation_id, repository_id, production_authority_id, "
        "production_generation) REFERENCES ci_coordinator.production_staged_grants("
        "installation_id, repository_id, authority_id, generation) ON DELETE RESTRICT",
    ),
}

EXPIRY_INDEX: Final = (
    "issued_plan_envelopes",
    "ix_issued_plan_envelopes_selected_expiry",
    False,
    False,
    True,
    True,
    True,
    "CREATE INDEX ix_issued_plan_envelopes_selected_expiry ON "
    "ci_coordinator.issued_plan_envelopes USING btree (installation_id, repository_id, "
    "expires_at) WHERE (production_admission_authority_id IS NOT NULL)",
)
ORIGIN_INDEX: Final = (
    "reconciliation_subjects",
    "ix_reconciliation_subjects_production_drain",
    False,
    False,
    True,
    True,
    True,
    "CREATE INDEX ix_reconciliation_subjects_production_drain ON "
    "ci_coordinator.reconciliation_subjects USING btree (installation_id, repository_id, "
    "subject_id) WHERE ((execution_origin)::text <> 'full_ci'::text)",
)
