import type { Browser, BrowserContextOptions, Page } from "@playwright/test";
import { installDemoControls } from "./controls";
import { installSyntheticNetwork } from "./network";
import { DEMO_TIME, type ScenarioName, scenarioName } from "./scenarioNames";
import { createScenario } from "./scenarios";
import type { DemoServer } from "./server";

type DisplayOptions = Pick<
  BrowserContextOptions,
  "viewport" | "isMobile" | "hasTouch" | "deviceScaleFactor"
>;

export async function confirmPageClosure(page: Page): Promise<void> {
  for (const runBeforeUnload of [true, false]) {
    try {
      const confirmed = page.waitForEvent("close", { timeout: 1_000 });
      await Promise.race([confirmed, page.close({ runBeforeUnload }).then(() => confirmed)]);
      return;
    } catch (error) {
      if (page.isClosed()) return;
      if (!runBeforeUnload) throw error;
    }
  }
}

export async function openDemoSession(
  browser: Browser,
  server: DemoServer,
  initial: ScenarioName,
  display: DisplayOptions = {},
  record = false,
) {
  const finished = Promise.withResolvers<void>();
  const errors: unknown[] = [];
  let closed = false;
  let current: Awaited<ReturnType<typeof openContext>>;
  let transition = Promise.resolve();

  function fail(error: unknown) {
    errors.push(error);
    finished.resolve();
  }

  async function openContext(name: ScenarioName) {
    const scenario = createScenario(name);
    const context = await browser.newContext({
      ...display,
      locale: "en-GB",
      timezoneId: "UTC",
      serviceWorkers: "block",
      acceptDownloads: false,
      storageState: { cookies: [], origins: [] },
      // Unroutable traffic reaches a loopback server that rejects forwarding, including CONNECT.
      proxy: { server: server.origin, bypass: "<-loopback>" },
    });
    try {
      const closingPages = new WeakSet<Page>();
      const closeForeignPage = (page: Page) => {
        if (page.isClosed() || page.url() === "about:blank") return;
        if (new URL(page.url()).origin === server.origin || closingPages.has(page)) return;
        closingPages.add(page);
        void confirmPageClosure(page).catch(async (error: unknown) => {
          fail(error);
          await context.close().catch(fail);
        });
      };
      // A popup's initial URL can arrive in its initializer without a navigation event.
      context.on("page", closeForeignPage);
      context.on("framenavigated", (frame) => {
        if (frame === frame.page().mainFrame()) closeForeignPage(frame.page());
      });
      const network = await installSyntheticNetwork(context, server.origin, scenario, fail);
      await context.exposeBinding("__ciDemoControl", async ({ page, frame }, value: unknown) => {
        if (
          current?.page !== page ||
          frame !== page.mainFrame() ||
          value === null ||
          typeof value !== "object"
        )
          return;
        const action: unknown = Reflect.get(value, "action");
        const selected: unknown = Reflect.get(value, "scenario");
        if (action === "provider-delivered" && name === "provider-failure-retry")
          await scenario.providerFailureDelivered;
        else if (action === "release") scenario.releaseStale();
        else if (action === "close") finished.resolve();
        else if (action === "reset" || action === "select") {
          if (typeof selected !== "string") throw new Error("Invalid synthetic scenario selection");
          void reset(action === "reset" ? scenario.name : scenarioName(selected)).catch(fail);
        }
      });
      await installDemoControls(context, name);
      if (name === "provider-failure-retry") {
        await context.addInitScript(() => {
          const originalFetch = globalThis.fetch;
          globalThis.fetch = async (input, init) => {
            const result = await originalFetch(input, init);
            if (result.status !== 503) return result;
            const url = new URL(result.url);
            if (
              url.origin === location.origin &&
              url.pathname === "/api/v1/workbench/installations" &&
              url.searchParams.size === 2 &&
              url.searchParams.get("page") === "1" &&
              url.searchParams.get("perPage") === "30"
            ) {
              // The UI must not enable Retry before the host observes body completion.
              // An aborted StrictMode read fails here and cannot acknowledge delivery.
              await result.clone().arrayBuffer();
              const control = Reflect.get(window, "__ciDemoControl") as (command: {
                action: string;
              }) => Promise<void>;
              await control({ action: "provider-delivered" });
            }
            return result;
          };
        });
      }
      if (name === "stale-navigation") {
        await context.addInitScript(() => {
          const originalFetch = globalThis.fetch;
          globalThis.fetch = async (input, init) => {
            const url =
              input instanceof Request ? new URL(input.url) : new URL(input, location.href);
            if (url.pathname !== "/api/v2/economics/repositories/1/1/sources")
              return originalFetch(input, init);
            // This witness deliberately delivers a late response even after the client cancels.
            const result = await originalFetch(new Request(input, { ...init, signal: null }));
            // Keep body consumption owned by the witness, not the abandoned UI consumer.
            await result.clone().arrayBuffer();
            return result;
          };
        });
      }
      const page = await context.newPage();
      await page.clock.setFixedTime(new Date(DEMO_TIME));
      return { context, page, scenario, network };
    } catch (error) {
      scenario.dispose();
      await context.close();
      throw error;
    }
  }

  async function show(next: Awaited<ReturnType<typeof openContext>>) {
    await next.page.goto(new URL(next.scenario.startPath, server.origin).href);
    next.page.once("close", () => {
      if (current === next) finished.resolve();
    });
    if (record) {
      void next.page.pause().catch((error: unknown) => {
        if (!next.page.isClosed() && !closed) fail(error);
      });
    }
  }

  function reset(name: ScenarioName = current.scenario.name): Promise<void> {
    transition = transition.then(async () => {
      if (closed) return;
      const previous = current;
      const next = await openContext(name);
      current = next;
      try {
        await show(next);
      } finally {
        previous.scenario.dispose();
        await previous.context.close();
      }
    });
    return transition;
  }

  current = await openContext(initial);
  try {
    await show(current);
  } catch (error) {
    current.scenario.dispose();
    await current.context.close();
    throw error;
  }

  return {
    get page(): Page {
      return current.page;
    },
    get context() {
      return current.context;
    },
    get scenario() {
      return current.scenario;
    },
    get blocked() {
      return current.network.blocked;
    },
    errors,
    finished: finished.promise,
    reset,
    async close() {
      closed = true;
      finished.resolve();
      try {
        await transition;
      } finally {
        current.scenario.dispose();
        await current.context.close();
      }
    },
  };
}
