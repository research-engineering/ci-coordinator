const BASE_URL_ENV = "CI_COORDINATOR_PLAYWRIGHT_BASE_URL";

export default function validateProductionTransport() {
  const origin = process.env[BASE_URL_ENV];
  if (
    !origin ||
    !/^http:\/\/127\.0\.0\.1:[1-9]\d{0,4}$/.test(origin) ||
    Number(new URL(origin).port) > 65_535
  )
    throw new Error("ASGI startup did not expose an ephemeral loopback endpoint");
  return () => {
    delete process.env[BASE_URL_ENV];
  };
}
