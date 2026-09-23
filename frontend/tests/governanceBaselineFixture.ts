import type {
  GovernanceBaselineApproval,
  GovernanceBaselineRead,
  GovernanceBaselineRecord,
} from "../src/api/governanceBaseline/schema";
import { governanceObservationFixture } from "./fixture";

export function governanceBaselineRecordFixture(
  overrides: Partial<GovernanceBaselineRecord> = {},
): GovernanceBaselineRecord {
  const observation = governanceObservationFixture();
  return {
    actor: `keycloak-human:v1:${"7".repeat(64)}`,
    approvedAt: "2026-07-26T12:00:01Z",
    auditEventId: `audit_${"b".repeat(32)}`,
    authority: "approved_expected_state",
    observedAt: observation.observedAt,
    operationId: "00000000-0000-4000-8000-000000000000",
    pointer: {
      baselineId: `governance-baseline:${"a".repeat(64)}`,
      stateDigest: observation.stateDigest,
      version: 1,
    },
    reason: "Adopt repository governance",
    state: {
      apiVersion: observation.apiVersion,
      repository: observation.repository,
      rules: observation.rules,
      stateDigest: observation.stateDigest,
    },
    supersedes: null,
    ...overrides,
  };
}

export function governanceBaselineReadFixture(
  overrides: Partial<GovernanceBaselineRead> = {},
): GovernanceBaselineRead {
  return {
    baseline: governanceBaselineRecordFixture(),
    ok: true,
    scope: { installationId: 1, repositoryId: 1 },
    state: "active",
    ...overrides,
  };
}

export function governanceBaselineApprovalFixture(
  overrides: Partial<GovernanceBaselineApproval> = {},
): GovernanceBaselineApproval {
  return {
    baseline: governanceBaselineRecordFixture(),
    ok: true,
    requestOperationId: "00000000-0000-4000-8000-000000000000",
    scope: { installationId: 1, repositoryId: 1 },
    state: "accepted",
    ...overrides,
  };
}
