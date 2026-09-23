export function configLifecycleProxyRequestIsAdmitted(
  method: string | undefined,
  url: URL,
): boolean {
  if (
    ["/api/v1/config/validations", "/api/v1/config/epochs", "/api/v1/config/rollbacks"].includes(
      url.pathname,
    )
  )
    return method === "POST" && url.search === "";
  const match =
    /^\/api\/v1\/config\/repositories\/([1-9][0-9]*)\/([1-9][0-9]*)\/(status|epochs\/([0-9a-f]{64})\/source)$/.exec(
      url.pathname,
    );
  if (
    !match ||
    method !== "GET" ||
    ![match[1], match[2]].every((value) => Number.isSafeInteger(Number(value)))
  )
    return false;
  if (match[3] !== "status") return url.search === "";
  const params = url.searchParams;
  if (
    [...params.keys()].some((key) => key !== "limit" && key !== "afterEpochId") ||
    params.getAll("limit").length > 1 ||
    params.getAll("afterEpochId").length > 1
  )
    return false;
  const limit = params.get("limit") ?? "50";
  const after = params.get("afterEpochId");
  return (
    /^[1-9][0-9]{0,2}$/.test(limit) &&
    Number(limit) <= 100 &&
    (after === null || /^[0-9a-f]{64}$/.test(after))
  );
}
