import { boundedFetch, combinedSignal } from "../shared/boundedFetch";
import { caughtFailure } from "../shared/responseFailure";
import {
  type InstallationCatalog,
  installationCatalogSchema,
  type ProviderInventoryError,
  providerInventoryErrorSchema,
  type RepositoryPage,
  repositoryPageSchema,
} from "./schema";

const REQUEST_TIMEOUT_MS = 15_000;
export const PROVIDER_REPOSITORY_PAGE_SIZE = 100;
export const PROVIDER_INSTALLATION_PAGE_SIZE = 30;

type InventoryFailure =
  | { readonly kind: "unauthenticated" }
  | { readonly kind: "forbidden" }
  | { readonly kind: "unavailable" }
  | { readonly kind: "rate-limited"; readonly retryAfterSeconds?: number }
  | { readonly kind: "not-found" }
  | { readonly kind: "suspended" }
  | { readonly kind: "unsupported-account" }
  | { readonly kind: "invalid-response" }
  | { readonly kind: "network-failure" };

export type InstallationCatalogResult =
  | { readonly kind: "ready"; readonly catalog: InstallationCatalog }
  | InventoryFailure;

export type RepositoryCatalogResult =
  | { readonly kind: "ready"; readonly page: RepositoryPage }
  | InventoryFailure;

export async function fetchInstallationCatalog(
  signal?: AbortSignal,
  timeoutMs = REQUEST_TIMEOUT_MS,
  page = 1,
): Promise<InstallationCatalogResult> {
  if (!Number.isInteger(page) || page < 1 || page > 10_000) {
    return { kind: "invalid-response" };
  }
  const query = new URLSearchParams({
    page: String(page),
    perPage: String(PROVIDER_INSTALLATION_PAGE_SIZE),
  });
  try {
    const response = await boundedFetch(
      new Request(new URL(`/api/v1/workbench/installations?${query}`, globalThis.location.origin), {
        credentials: "same-origin",
        headers: { accept: "application/json" },
        signal: combinedSignal(signal, timeoutMs),
      }),
    );
    if (response.status !== 200) return await inventoryFailure(response);
    const catalog = installationCatalogSchema.parse(await response.json());
    if (catalog.page !== page || catalog.perPage !== PROVIDER_INSTALLATION_PAGE_SIZE) {
      return { kind: "invalid-response" };
    }
    return { kind: "ready", catalog };
  } catch (error) {
    return caughtFailure(error, signal);
  }
}

export async function fetchRepositoryCatalogPage(
  installationId: number,
  page: number,
  signal?: AbortSignal,
  timeoutMs = REQUEST_TIMEOUT_MS,
): Promise<RepositoryCatalogResult> {
  if (
    !Number.isSafeInteger(installationId) ||
    installationId < 1 ||
    !Number.isInteger(page) ||
    page < 1 ||
    page > 10_000
  ) {
    return { kind: "invalid-response" };
  }
  const path = `/api/v1/workbench/installations/${installationId}/repositories`;
  const query = new URLSearchParams({
    page: String(page),
    perPage: String(PROVIDER_REPOSITORY_PAGE_SIZE),
  });
  try {
    const response = await boundedFetch(
      new Request(new URL(`${path}?${query}`, globalThis.location.origin), {
        credentials: "same-origin",
        headers: { accept: "application/json" },
        signal: combinedSignal(signal, timeoutMs),
      }),
    );
    if (response.status !== 200) return await inventoryFailure(response);
    const result = repositoryPageSchema.parse(await response.json());
    if (
      result.installation.installationId !== installationId ||
      result.page !== page ||
      result.perPage !== PROVIDER_REPOSITORY_PAGE_SIZE
    ) {
      return { kind: "invalid-response" };
    }
    return { kind: "ready", page: result };
  } catch (error) {
    return caughtFailure(error, signal);
  }
}

async function inventoryFailure(response: Response): Promise<InventoryFailure> {
  let error: ProviderInventoryError;
  try {
    error = providerInventoryErrorSchema.parse(await response.json());
  } catch {
    return { kind: "invalid-response" };
  }
  switch (error.error) {
    case "unauthenticated":
      return response.status === 401 ? { kind: "unauthenticated" } : { kind: "invalid-response" };
    case "forbidden":
      return response.status === 403 ? { kind: "forbidden" } : { kind: "invalid-response" };
    case "rate_limited":
      return response.status === 429
        ? {
            kind: "rate-limited",
            ...(error.retryAfterSeconds === null
              ? {}
              : { retryAfterSeconds: error.retryAfterSeconds }),
          }
        : { kind: "invalid-response" };
    case "not_found":
      return response.status === 404 ? { kind: "not-found" } : { kind: "invalid-response" };
    case "suspended":
      return response.status === 409 ? { kind: "suspended" } : { kind: "invalid-response" };
    case "unsupported_account_type":
      return response.status === 409
        ? { kind: "unsupported-account" }
        : { kind: "invalid-response" };
    case "unavailable":
    case "malformed_provider_response":
    case "provider_binding_mismatch":
      return response.status === 503 ? { kind: "unavailable" } : { kind: "invalid-response" };
  }
}
