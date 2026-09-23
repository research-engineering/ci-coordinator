const PATH =
  /^\/api\/v1\/activity\/(security|repositories\/([1-9][0-9]*)\/([1-9][0-9]*))(?:\/export)?$/;
const LIMITS: Readonly<Record<string, number>> = {
  since: 27,
  until: 27,
  issuer: 2048,
  actor: 128,
  action: 64,
  cursor: 1024,
  limit: 3,
};

export function activityProxyRequestIsAdmitted(method: string | undefined, url: URL): boolean {
  const path = PATH.exec(url.pathname);
  const parameters = url.searchParams;
  if (
    method !== "GET" ||
    !path ||
    url.search.length > 8193 ||
    (path[2] !== undefined &&
      ![path[2], path[3]].every((value) => Number.isSafeInteger(Number(value)))) ||
    [...parameters.keys()].some(
      (key) => !Object.hasOwn(LIMITS, key) || parameters.getAll(key).length !== 1,
    )
  )
    return false;
  for (const [key, value] of parameters) {
    if (!value.length || Array.from(value).length > (LIMITS[key] ?? 0)) return false;
  }
  const since = parameters.get("since"),
    until = parameters.get("until");
  const limit = parameters.get("limit") ?? "50";
  return (
    since !== null &&
    until !== null &&
    Number.isFinite(Date.parse(since)) &&
    Number.isFinite(Date.parse(until)) &&
    /^(?:[1-9][0-9]?|100)$/.test(limit) &&
    (path[1] === "security" || !parameters.has("issuer"))
  );
}
