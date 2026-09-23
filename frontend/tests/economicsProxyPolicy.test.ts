import { expect, test } from "vitest";
import {
  OPERATOR_API_PROXY_CONTEXT,
  proxyRequestIsAdmitted,
} from "../src/api/development/proxyPolicy";

const digest = "a".repeat(64);
const sha = "b".repeat(40);
const scope = "/api/v2/economics/repositories/1/2";
const routes = [
  ["POST", "/api/v2/economics/source-discovery"],
  ["POST", "/api/v2/economics/sources"],
  ["POST", "/api/v2/economics/budget-policies"],
  ["POST", "/api/v2/economics/observation"],
  ["POST", "/api/v2/economics/history"],
  ["GET", `${scope}/history`],
  ["GET", `${scope}/observation`],
  ["GET", `${scope}/observation/gaps`],
  ["GET", `${scope}/observation/gaps?limit=50&afterCursor=1.2.3.${digest}`],
  ["GET", `${scope}/observation/workflows`],
  ["GET", `${scope}/observation/workflows?pageNumber=20`],
  ["GET", `${scope}/budget-policies`],
  ["GET", `${scope}/budget-signals`],
  [
    "GET",
    `${scope}/budget-signals?limit=20&afterCursor=${digest}&policyKey=backend-cpu&revision=2&outcome=breached`,
  ],
  ["GET", `${scope}/sources`],
  ["GET", `${scope}/sources?limit=1&afterCursor=4201.2`],
  ["GET", `${scope}/sources/${digest}/reports?limit=100&afterCursor=${digest}`],
  ["GET", `${scope}/sources/${digest}/reports`],
  ["GET", `${scope}/reports/${digest}`],
  ["GET", `${scope}/reports/${digest}/budget?counter=cpu_user&maximumUs=0`],
  ["GET", `${scope}/report-comparisons?baselineReportId=${digest}&treatmentReportId=${digest}`],
  ["GET", `${scope}/attempts/42/1/measurements?headSha=${sha}`],
  ["GET", "/api/v1/economics/repositories/1/2/attempts"],
  [
    "GET",
    `/api/v1/economics/repositories/1/2/attempts?limit=20&afterCursor=2026-09-08T00:00:00.000000Z.${digest}`,
  ],
  ["GET", `/api/v1/economics/repositories/1/2/attempts/42/1/jobs?headSha=${sha}`],
  [
    "GET",
    `/api/v1/economics/repositories/1/2/attempts/42/1/jobs?headSha=${sha}&afterJobId=7&limit=10`,
  ],
] as const;
test.each(routes)("admits only the configured operation %s %s", (method, path) => {
  expect(new RegExp(OPERATOR_API_PROXY_CONTEXT).test(path)).toBe(true);
  expect(proxyRequestIsAdmitted(method, path)).toBe(true);
});
test.each(
  routes.flatMap(([method, path]) => [
    ["DELETE", path],
    [method === "GET" ? "POST" : "GET", path],
    [method, `${path}${path.includes("?") ? "&" : "?"}unknown=1`],
    [method, path.replace("/economics/", "/economics/extra/")],
    [method, `https://foreign.invalid${path}`],
  ]),
)("rejects adjacent or widened operation %s %s", (method, path) => {
  expect(proxyRequestIsAdmitted(method, path)).toBe(false);
});
test.each([
  "/api/v2/economics/reports",
  `${scope}/reports/${digest}?unused=1`,
  `${scope}/unknown`,
  `${scope}/budget-signals?revision=1`,
  `${scope}/budget-signals?policyKey=bad/key`,
  `${scope}/budget-signals?policyKey=${"a".repeat(129)}`,
  `${scope}/budget-signals?outcome=unknown`,
  `${scope}/budget-signals?limit=1&limit=2`,
  `${scope}/budget-signals?afterCursor=x`,
  `${scope}/budget-signals?policyKey=x&revision=0`,
  `${scope}/budget-policies?limit=20`,
  `${scope}/observation?limit=20`,
  `${scope}/history?rescan=true`,
  "/api/v1/economics/repositories/1/2/history",
  `${scope}/observation/gaps?limit=51`,
  `${scope}/observation/gaps?afterCursor=invalid`,
  `${scope}/observation/gaps?afterCursor=${digest}`,
  `${scope}/observation/gaps?afterCursor=1.3.3.${digest}`,
  `${scope}/observation/gaps?afterCursor=2.2.3.${digest}`,
  `${scope}/observation/gaps?afterCursor=1.2.9007199254740992.${digest}`,
  `${scope}/observation/gaps?limit=20&limit=20`,
  `${scope}/observation/workflows?pageNumber=21`,
  `${scope}/observation/workflows?pageNumber=0`,
  `${scope}/observation/workflows?pageNumber=01`,
  `${scope}/observation/workflows?pageNumber=1&pageNumber=2`,
  `${scope}/observation/workflows?limit=20`,
  "/api/v1/economics/repositories/1/2/observation",
  "/api/v1/economics/repositories/1/2/observation/gaps",
  "/api/v1/economics/repositories/1/2/observation/workflows",
  `${scope}/sources?limit=0`,
  `${scope}/sources?limit=101`,
  `${scope}/sources?limit=01`,
  `${scope}/sources?limit=1&limit=2`,
  `${scope}/sources?afterCursor=1.1&afterCursor=2.2`,
  `${scope}/sources?afterCursor=9007199254740992.1`,
  `${scope}/sources?afterCursor=1.9007199254740992`,
  `${scope}/sources?afterCursor=x`,
  `${scope}/sources?afterCursor=0.1`,
  `${scope}/sources/${digest}/reports?afterCursor=x`,
  `${scope}/attempts/1/1/jobs?headSha=${sha}`,
  `${scope}/attempts/9007199254740992/1/measurements?headSha=${sha}`,
  `${scope}/attempts/1/9007199254740992/measurements?headSha=${sha}`,
  `${scope}/attempts/1/1/measurements?headSha=${sha}&limit=10`,
  `${scope}/attempts/1/1/measurements?headSha=x`,
  `${scope}/attempts/1/1/measurements`,
  `/api/v1/economics/repositories/1/2/attempts/1/1/measurements?headSha=${sha}`,
  `/api/v1/economics/repositories/1/2/attempts/1/1/jobs?headSha=${sha}&afterJobId=0`,
  `/api/v1/economics/repositories/1/2/attempts/1/1/jobs?headSha=${sha}&limit=101`,
  `/api/v1/economics/repositories/1/2/unknown`,
  `/api/v1/economics/repositories/1/2/attempts?afterCursor=x`,
  `${scope}/report-comparisons?baselineReportId=${digest}`,
  `${scope}/report-comparisons?treatmentReportId=${digest}`,
  `${scope}/reports/${digest}/budget?counter=cpu_user`,
  `${scope}/reports/${digest}/budget?counter=invalid&maximumUs=1`,
  `${scope}/reports/${digest}/budget?counter=elapsed&maximumUs=9007199254740992`,
  `${scope}/reports/${digest}/budget?counter=cpu_system&maximumUs=-1`,
  `${scope}/reports/${digest}/budget?counter=elapsed&maximumUs=1.1`,
  "/api/v2/economics/repositories/9007199254740992/2/sources",
  "/api/v2/economics/repositories/1/9007199254740992/sources",
  "/api/v2/economics/repositories/0/2/sources",
  "/api/v2/economics/repositories/1/2",
])("rejects malformed economics admission %s", (path) => {
  expect(proxyRequestIsAdmitted("GET", path)).toBe(false);
});
