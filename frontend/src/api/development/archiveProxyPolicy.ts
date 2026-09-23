const ID = Number.MAX_SAFE_INTEGER;
const ANALYTICS_NUMBERS: Readonly<Record<string, number>> = {
  generation: ID,
  workflowId: ID,
  horizonDays: 30,
  minimumDailySamples: 1000,
  degradationRelativeBps: 100000,
  degradationAbsoluteMs: 86400000,
  persistenceDays: 14,
};
const ARCHIVE_NUMBERS: Readonly<Record<string, number>> = {
  generation: ID,
  limit: 50,
  workflow_id: ID,
  workflow_run_id: ID,
  run_attempt: ID,
};

export function archiveProxyQueryIsAdmitted(path: string, params: URLSearchParams): boolean {
  if ([...params.keys()].length !== new Set(params.keys()).size || !params.has("generation"))
    return false;
  const detail = /^\/history\/attempts\/([1-9][0-9]{0,15})\/([1-9][0-9]{0,15})\/detail$/.exec(path);
  if (detail) {
    const generation = params.get("generation") ?? "";
    return (
      params.size === 1 &&
      Number.isSafeInteger(Number(detail[1])) &&
      Number.isSafeInteger(Number(detail[2])) &&
      /^[1-9][0-9]{0,15}$/.test(generation) &&
      Number.isSafeInteger(Number(generation))
    );
  }
  const analytics = path === "/history/analytics";
  const kind = /^\/history\/archive\/(records|jobs|gaps|detail)$/.exec(path)?.[1];
  if ((!analytics && !kind) || params.toString().length > (analytics ? 8192 : 16384)) return false;
  const numeric = analytics ? ANALYTICS_NUMBERS : ARCHIVE_NUMBERS;
  for (const [key, value] of params) {
    const maximum = Object.hasOwn(numeric, key) ? numeric[key] : undefined;
    if (maximum !== undefined) {
      if (
        !/^[1-9][0-9]{0,15}$/.test(value) ||
        !Number.isSafeInteger(Number(value)) ||
        Number(value) > maximum
      )
        return false;
    } else if (
      analytics
        ? key === "createdFrom" || key === "createdUntil"
        : key === "created_from" || key === "created_through"
    ) {
      if (value.length > 64 || !Number.isFinite(Date.parse(value))) return false;
    } else if (key === (analytics ? "jobName" : "job_name")) {
      if (!value || Array.from(value).length > 512) return false;
    } else if (analytics && key === "purpose") {
      if (!["lint", "typecheck", "test", "build", "deploy", "mixed", "unknown"].includes(value))
        return false;
    } else if (!analytics && key === "cursor") {
      if (!value || value.length > 4096) return false;
    } else return false;
  }
  if (analytics) return params.has("createdFrom") && params.has("createdUntil");
  const exact = params.has("workflow_run_id") && params.has("run_attempt");
  return (
    params.has("workflow_run_id") === params.has("run_attempt") &&
    (!(kind === "jobs" || kind === "detail") || exact) &&
    (kind !== "gaps" || !exact) &&
    (kind === "records" ||
      !["created_from", "created_through", "workflow_id"].some((key) => params.has(key))) &&
    (kind === "records" || kind === "jobs" || !params.has("job_name"))
  );
}
