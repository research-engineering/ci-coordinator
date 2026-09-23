import { controlPlaneSessionFixture as deterministicSession } from "../dev/fixtures";
import type { ControlPlaneSession } from "../src/api/controlPlaneIdentity/schema";

export {
  configActivationFixture,
  governanceObservationFixture,
  installationCatalogFixture,
  installationFixture,
  repositoryFixture,
  repositoryPageFixture,
  workbenchFixture,
  workflowDiscoveryFixture,
} from "../dev/fixtures";

export function controlPlaneSessionFixture(
  overrides: Partial<ControlPlaneSession> = {},
): ControlPlaneSession {
  return deterministicSession({
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    ...overrides,
  });
}
