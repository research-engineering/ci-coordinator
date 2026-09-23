const SCOPE =
  /^\/api\/v([12])\/economics\/repositories\/([1-9][0-9]{0,15})\/([1-9][0-9]{0,15})(\/.*)$/;
const DIGEST = /^[0-9a-f]{64}$/;
const SHA = /^[0-9a-f]{40}$/;
const SOURCE_CURSOR = /^[1-9][0-9]{0,15}\.[1-9][0-9]{0,15}$/;
const OBSERVATION_CURSOR = /^[1-9][0-9]{0,15}\.[1-9][0-9]{0,15}\.[1-9][0-9]{0,15}\.[0-9a-f]{64}$/;
const ATTEMPT_CURSOR = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\.[0-9a-f]{64}$/;

export function economicsProxyRequestIsAdmitted(method: string | undefined, url: URL): boolean {
  if (
    url.pathname === "/api/v2/economics/source-discovery" ||
    url.pathname === "/api/v2/economics/sources" ||
    url.pathname === "/api/v2/economics/budget-policies" ||
    url.pathname === "/api/v2/economics/observation" ||
    url.pathname === "/api/v2/economics/history" ||
    url.pathname === "/api/v2/economics/history/gaps/retry" ||
    url.pathname === "/api/v2/economics/history/retention/preview" ||
    url.pathname === "/api/v2/economics/history/retention/apply"
  ) {
    return method === "POST" && url.search === "";
  }
  const match = SCOPE.exec(url.pathname);
  if (
    match?.[1] === "2" &&
    match[4] === "/history/analytics/settings" &&
    safeId(match[2]) &&
    safeId(match[3])
  )
    return method === "PUT"
      ? url.search === ""
      : method === "GET" &&
          url.search.length <= 256 &&
          queryKeys(url.searchParams, ["generation"]) &&
          safeId(url.searchParams.get("generation"));
  if (method !== "GET" || !match || !safeId(match[2]) || !safeId(match[3])) return false;
  const [, version, , , path] = match;
  if (!path) return false;
  const params = url.searchParams;
  if (version === "1" && path === "/attempts") return pageQuery(params, ATTEMPT_CURSOR);
  if (version === "2" && path === "/sources") {
    const cursor = params.get("afterCursor");
    return pageQuery(params, SOURCE_CURSOR) && (cursor === null || cursor.split(".").every(safeId));
  }
  if (version === "2" && /^\/sources\/[0-9a-f]{64}\/reports$/.test(path))
    return pageQuery(params, DIGEST);
  const attempt = /^\/attempts\/([1-9][0-9]{0,15})\/([1-9][0-9]{0,15})\/(jobs|measurements)$/.exec(
    path,
  );
  if (attempt) {
    const jobs = version === "1" && attempt[3] === "jobs";
    const measurements = version === "2" && attempt[3] === "measurements";
    return (
      safeId(attempt[1]) &&
      safeId(attempt[2]) &&
      (jobs || measurements) &&
      queryKeys(params, jobs ? ["headSha", "afterJobId", "limit"] : ["headSha"]) &&
      SHA.test(params.get("headSha") ?? "") &&
      (!jobs ||
        (optionalLimit(params) && (!params.has("afterJobId") || safeId(params.get("afterJobId")))))
    );
  }
  if (version !== "2") return false;
  if (path.startsWith("/history/")) return archiveProxyQueryIsAdmitted(path, params);
  if (path === "/observation" || path === "/history") return url.search === "";
  if (path === "/observation/gaps") {
    const cursor = params.get("afterCursor");
    const parts = cursor?.split(".");
    return (
      pageQuery(params, OBSERVATION_CURSOR, 50) &&
      (parts === undefined ||
        (parts.slice(0, 3).every(safeId) && parts[0] === match[2] && parts[1] === match[3]))
    );
  }
  if (path === "/observation/workflows") {
    const page = params.get("pageNumber");
    return (
      queryKeys(params, ["pageNumber"]) && (page === null || (safeId(page) && Number(page) <= 20))
    );
  }
  if (path === "/budget-policies") return url.search === "";
  if (path === "/budget-signals") {
    const policyKey = params.get("policyKey");
    return (
      queryKeys(params, ["limit", "afterCursor", "policyKey", "revision", "outcome"]) &&
      optionalLimit(params) &&
      (!params.has("afterCursor") || DIGEST.test(params.get("afterCursor") ?? "")) &&
      (policyKey === null || /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(policyKey)) &&
      (!params.has("revision") || (policyKey !== null && safeId(params.get("revision")))) &&
      (!params.has("outcome") ||
        ["breached", "within_budget", "insufficient_evidence"].includes(
          params.get("outcome") ?? "",
        ))
    );
  }
  if (/^\/reports\/[0-9a-f]{64}$/.test(path)) return url.search === "";
  if (path === "/report-comparisons")
    return (
      queryKeys(params, ["baselineReportId", "treatmentReportId"]) &&
      DIGEST.test(params.get("baselineReportId") ?? "") &&
      DIGEST.test(params.get("treatmentReportId") ?? "")
    );
  if (/^\/reports\/[0-9a-f]{64}\/budget$/.test(path)) {
    const value = params.get("maximumUs") ?? "";
    return (
      queryKeys(params, ["counter", "maximumUs"]) &&
      ["cpu_user", "cpu_system", "elapsed"].includes(params.get("counter") ?? "") &&
      /^(0|[1-9][0-9]{0,15})$/.test(value) &&
      Number.isSafeInteger(Number(value))
    );
  }
  return false;
}

function pageQuery(params: URLSearchParams, cursorPattern: RegExp, maximum = 100): boolean {
  return (
    queryKeys(params, ["limit", "afterCursor"]) &&
    optionalLimit(params, maximum) &&
    (!params.has("afterCursor") || cursorPattern.test(params.get("afterCursor") ?? ""))
  );
}
function optionalLimit(params: URLSearchParams, maximum = 100): boolean {
  const value = params.get("limit");
  return value === null || (safeId(value) && Number(value) <= maximum);
}
function safeId(value: string | null | undefined): boolean {
  return (
    typeof value === "string" &&
    /^[1-9][0-9]{0,15}$/.test(value) &&
    Number.isSafeInteger(Number(value))
  );
}
function queryKeys(params: URLSearchParams, allowed: readonly string[]): boolean {
  const keys = [...params.keys()];
  return keys.length === new Set(keys).size && keys.every((key) => allowed.includes(key));
}

import { archiveProxyQueryIsAdmitted } from "./archiveProxyPolicy.ts";
