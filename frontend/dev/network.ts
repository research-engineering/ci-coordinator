import type { BrowserContext, Request } from "@playwright/test";
import type { createScenario } from "./scenarios";
import { assetPath, SYNTHETIC_CSP } from "./server";

export async function installSyntheticNetwork(
  context: BrowserContext,
  origin: string,
  scenario: ReturnType<typeof createScenario>,
  onError: (error: unknown) => void,
) {
  const blocked: string[] = [];
  const scenarioRequests = new WeakMap<Request, string>();
  function record(reason: string) {
    if (blocked.length < 256) blocked.push(reason);
  }
  context.on("requestfinished", (request) => {
    const path = scenarioRequests.get(request);
    if (path === undefined) return;
    scenarioRequests.delete(request);
    try {
      // Confirm only after the body completes. response() would issue a channel
      // call that can race page closure; existingResponse() reads the local cache.
      const result = request.existingResponse();
      if (!result) throw new Error("Synthetic session: completed scenario request has no response");
      scenario.confirmDelivery(path, result.status());
    } catch (error) {
      onError(error);
    }
  });
  context.on("requestfailed", (request) => scenarioRequests.delete(request));
  await context.routeWebSocket(/.*/, (socket) => {
    record("websocket");
    return socket.close().catch(onError);
  });
  await context.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const reject = async (reason: string) => {
      record(reason);
      await route.abort("blockedbyclient");
    };
    try {
      if (url.origin !== origin || url.username || url.password)
        return await reject("foreign-origin");
      if (request.method() === "GET" && url.pathname === "/favicon.ico" && !url.search) {
        await route.fulfill({ status: 204 });
        return;
      }
      if (url.pathname.startsWith("/api/")) {
        let body: unknown;
        try {
          body = request.postData() === null ? undefined : request.postDataJSON();
        } catch {
          return await reject("invalid-json");
        }
        const result = await scenario.handle({
          url,
          method: request.method(),
          body,
          csrf: request.headers()["x-csrf-token"],
        });
        if (!result) return await reject("unhandled-api");
        scenarioRequests.set(request, url.pathname);
        await route.fulfill({ ...result, headers: { "cache-control": "no-store" } });
        return;
      }
      const type = request.resourceType();
      const document =
        type === "document" &&
        request.frame().parentFrame() === null &&
        (url.pathname === "/workbench" || url.pathname === "/");
      const asset =
        ["script", "stylesheet", "image", "font"].includes(type) && assetPath(url.pathname);
      if (request.method() !== "GET" || !(document || asset))
        return await reject("unhandled-resource");
      // Fetch only the admitted Vite URL without browser cookies; reject redirects before fulfilling.
      const assetResponse = await fetch(url, {
        redirect: "manual",
        signal: AbortSignal.timeout(15_000),
      });
      if (assetResponse.status < 200 || assetResponse.status >= 300)
        return await reject("asset-status");
      const bytes = Buffer.from(await assetResponse.arrayBuffer());
      if (bytes.length > 8 * 1024 * 1024) return await reject("asset-size");
      await route.fulfill({
        status: assetResponse.status,
        body: bytes,
        headers: {
          "content-type": assetResponse.headers.get("content-type") ?? "application/octet-stream",
          "content-security-policy": SYNTHETIC_CSP,
          "cache-control": "no-store",
        },
      });
    } catch (error) {
      if (context.pages().every((page) => page.isClosed())) return;
      await reject("harness-error");
      onError(error);
    }
  });
  return { blocked };
}
