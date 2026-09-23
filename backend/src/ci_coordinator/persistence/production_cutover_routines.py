"""Versioned SQL guards owned by the production-generation-cutover capability."""

from typing import Final

PRODUCTION_EVIDENCE_INSERT_BODY: Final = """
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

PRODUCTION_STAGE_INSERT_BODY: Final = """
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

PRODUCTION_STATE_TRANSITION_BODY: Final = """
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
