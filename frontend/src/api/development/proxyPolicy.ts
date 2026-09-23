import { MAX_WORKBENCH_SECTION_ITEMS } from "../workbench/limits.ts";
import { activityProxyRequestIsAdmitted } from "./activityProxyPolicy.ts";
import { configLifecycleProxyRequestIsAdmitted } from "./configLifecycleProxyPolicy.ts";
import { economicsProxyRequestIsAdmitted } from "./economicsProxyPolicy.ts";

export const OPERATOR_API_PROXY_CONTEXT =
  "^/api/(?:v1/(?:activity/|auth/|config/|repository-attestations/|workbench/)|v[12]/economics/)";

const LOCAL_ORIGIN = "http://ci-coordinator.local";
const AUTH_LOGIN_START_PATH = "/api/v1/auth/keycloak/start";
const AUTH_LOGIN_CALLBACK_PATH = "/api/v1/auth/keycloak/callback";
const AUTH_SESSION_PATH = "/api/v1/auth/session";
const AUTH_LOGOUT_PATH = "/api/v1/auth/keycloak/logout";
const AUTH_BACKCHANNEL_LOGOUT_PATH = "/api/v1/auth/keycloak/backchannel-logout";
const REPOSITORY_ATTESTATION_START_PATH = "/api/v1/repository-attestations/github/start";
const REPOSITORY_ATTESTATION_CALLBACK_PATH = "/api/v1/repository-attestations/github/callback";
const CONFIG_ACTIVATIONS_PATH = "/api/v1/config/activations";
const WORKBENCH_PATH = /^\/api\/v1\/workbench\/repositories\/([1-9][0-9]*)\/([1-9][0-9]*)$/;
const WORKFLOW_DISCOVERY_PATH =
  /^\/api\/v1\/workbench\/repositories\/([1-9][0-9]*)\/([1-9][0-9]*)\/workflow-discovery$/;
const GOVERNANCE_OBSERVATION_PATH =
  /^\/api\/v1\/workbench\/repositories\/([1-9][0-9]*)\/([1-9][0-9]*)\/governance-observation$/;
const GOVERNANCE_BASELINE_PATH =
  /^\/api\/v1\/workbench\/repositories\/([1-9][0-9]*)\/([1-9][0-9]*)\/governance-baselines$/;
const GOVERNANCE_COMPARISON_PATH =
  /^\/api\/v1\/workbench\/repositories\/([1-9][0-9]*)\/([1-9][0-9]*)\/governance-comparison$/;
const INSTALLATIONS_PATH = "/api/v1/workbench/installations";
const REPOSITORIES_PATH = /^\/api\/v1\/workbench\/installations\/([1-9][0-9]*)\/repositories$/;

export function proxyRequestIsAdmitted(
  method: string | undefined,
  requestUrl: string | undefined,
): boolean {
  if (!requestUrl) return false;

  let url: URL;
  try {
    url = new URL(requestUrl, LOCAL_ORIGIN);
  } catch {
    return false;
  }
  if (url.origin !== LOCAL_ORIGIN) return false;
  if (url.pathname.startsWith("/api/v1/activity/"))
    return activityProxyRequestIsAdmitted(method, url);
  if (url.pathname.startsWith("/api/v1/config/") && url.pathname !== CONFIG_ACTIVATIONS_PATH)
    return configLifecycleProxyRequestIsAdmitted(method, url);
  if (/^\/api\/v[12]\/economics\//.test(url.pathname))
    return economicsProxyRequestIsAdmitted(method, url);

  if (url.pathname === AUTH_LOGIN_START_PATH || url.pathname === AUTH_SESSION_PATH) {
    return method === "GET" && url.search === "";
  }
  if (
    url.pathname === AUTH_LOGOUT_PATH ||
    url.pathname === AUTH_BACKCHANNEL_LOGOUT_PATH ||
    url.pathname === REPOSITORY_ATTESTATION_START_PATH ||
    url.pathname === CONFIG_ACTIVATIONS_PATH
  ) {
    return method === "POST" && url.search === "";
  }
  if (
    url.pathname === AUTH_LOGIN_CALLBACK_PATH ||
    url.pathname === REPOSITORY_ATTESTATION_CALLBACK_PATH
  ) {
    return method === "GET" && callbackQueryIsAdmitted(url);
  }
  const baseline = GOVERNANCE_BASELINE_PATH.exec(url.pathname);
  if (baseline) {
    return (
      (method === "GET" || method === "POST") &&
      url.search === "" &&
      pathIdentifiersAreSafe(baseline)
    );
  }

  if (method !== "GET") return false;

  if (url.pathname === INSTALLATIONS_PATH) return pageQueryIsAdmitted(url, 30);

  const governance = GOVERNANCE_OBSERVATION_PATH.exec(url.pathname);
  if (governance) {
    return url.search === "" && pathIdentifiersAreSafe(governance);
  }
  const comparison = GOVERNANCE_COMPARISON_PATH.exec(url.pathname);
  if (comparison) {
    return url.search === "" && pathIdentifiersAreSafe(comparison);
  }

  const discovery = WORKFLOW_DISCOVERY_PATH.exec(url.pathname);
  if (discovery) {
    if (
      url.searchParams.getAll("revision").length > 1 ||
      [...url.searchParams.keys()].some((key) => key !== "revision")
    ) {
      return false;
    }
    const installationId = Number(discovery[1]);
    const repositoryId = Number(discovery[2]);
    const revision = url.searchParams.get("revision");
    return (
      Number.isSafeInteger(installationId) &&
      Number.isSafeInteger(repositoryId) &&
      (revision === null || /^[0-9a-f]{40}$/.test(revision))
    );
  }

  const repositories = REPOSITORIES_PATH.exec(url.pathname);
  if (repositories) {
    const installationId = Number(repositories[1]);
    return Number.isSafeInteger(installationId) && pageQueryIsAdmitted(url, 100);
  }

  const match = WORKBENCH_PATH.exec(url.pathname);
  if (!match || url.searchParams.getAll("limit").length > 1) return false;
  if ([...url.searchParams.keys()].some((key) => key !== "limit")) return false;

  const installationId = Number(match[1]);
  const repositoryId = Number(match[2]);
  const limit = admittedPositiveInteger(
    url.searchParams.get("limit") ?? "10",
    MAX_WORKBENCH_SECTION_ITEMS,
  );
  return (
    Number.isSafeInteger(installationId) &&
    Number.isSafeInteger(repositoryId) &&
    limit !== undefined
  );
}

function pageQueryIsAdmitted(url: URL, defaultSize: number): boolean {
  return (
    ![...url.searchParams.keys()].some((key) => key !== "page" && key !== "perPage") &&
    url.searchParams.getAll("page").length <= 1 &&
    url.searchParams.getAll("perPage").length <= 1 &&
    admittedPositiveInteger(url.searchParams.get("page") ?? "1", 10_000) !== undefined &&
    admittedPositiveInteger(url.searchParams.get("perPage") ?? String(defaultSize), 100) !==
      undefined
  );
}

function callbackQueryIsAdmitted(url: URL): boolean {
  if (
    url.searchParams.getAll("code").length !== 1 ||
    url.searchParams.getAll("state").length !== 1 ||
    [...url.searchParams.keys()].some((key) => key !== "code" && key !== "state")
  ) {
    return false;
  }
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");
  return (
    code !== null &&
    code.length > 0 &&
    code.length <= 512 &&
    state !== null &&
    /^[A-Za-z0-9_-]{43}$/.test(state)
  );
}

function pathIdentifiersAreSafe(match: RegExpExecArray): boolean {
  return [match[1], match[2]].every((value) => {
    const candidate = Number(value);
    return Number.isSafeInteger(candidate);
  });
}

function admittedPositiveInteger(value: string, maximum: number): number | undefined {
  if (!/^[1-9][0-9]*$/.test(value) || value.length > 16) return undefined;
  const candidate = Number(value);
  return Number.isSafeInteger(candidate) && candidate <= maximum ? candidate : undefined;
}
