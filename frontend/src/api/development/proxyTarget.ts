const DEFAULT_PROXY_TARGET = "http://127.0.0.1:3000";

export function admitProxyTarget(value: string | undefined): string {
  const raw = value ?? DEFAULT_PROXY_TARGET;
  let target: URL;
  try {
    target = new URL(raw);
  } catch {
    throw new Error("operator proxy target is invalid");
  }

  const isComposeBackend = target.hostname === "backend" && target.port === "3000";
  const isLoopback =
    (target.hostname === "127.0.0.1" || target.hostname === "[::1]") && target.port !== "";
  if (
    target.protocol !== "http:" ||
    (!isComposeBackend && !isLoopback) ||
    target.username !== "" ||
    target.password !== "" ||
    target.pathname !== "/" ||
    target.search !== "" ||
    target.hash !== ""
  ) {
    throw new Error("operator proxy target is outside the admitted local boundary");
  }
  return target.origin;
}
