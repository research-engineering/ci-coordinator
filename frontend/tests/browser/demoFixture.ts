import { test as base, expect } from "@playwright/test";
import type { ScenarioName } from "../../dev/scenarioNames";
import { type DemoServer, startDemoServer } from "../../dev/server";
import { openDemoSession } from "../../dev/session";

export const test = base.extend<
  {
    demoScenario: ScenarioName;
    demo: Awaited<ReturnType<typeof openDemoSession>>;
  },
  { demoServer: DemoServer }
>({
  demoScenario: ["populated", { option: true }],
  demoServer: [
    async ({ browserName }, use) => {
      expect(browserName).toBe("chromium");
      const server = await startDemoServer();
      try {
        await use(server);
      } finally {
        await server.close();
      }
    },
    { scope: "worker" },
  ],
  demo: async (
    { browser, demoServer, demoScenario, viewport, isMobile, hasTouch, deviceScaleFactor },
    use,
  ) => {
    const session = await openDemoSession(browser, demoServer, demoScenario, {
      viewport,
      isMobile,
      hasTouch,
      ...(deviceScaleFactor === undefined ? {} : { deviceScaleFactor }),
    });
    try {
      await use(session);
    } finally {
      await session.close();
      expect(session.errors).toEqual([]);
    }
  },
});
