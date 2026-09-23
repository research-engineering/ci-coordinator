import { test } from "@playwright/test";
import { scenarioName } from "./scenarioNames";
import { startDemoServer } from "./server";
import { openDemoSession } from "./session";

test("interactive synthetic session (not an automated witness)", async ({ browser }) => {
  const selected = scenarioName(process.env["CI_COORDINATOR_DEMO_SCENARIO"] ?? "populated");
  const server = await startDemoServer();
  try {
    const record = process.env["CI_COORDINATOR_DEMO_MODE"] === "record";
    const session = await openDemoSession(browser, server, selected, {}, record);
    try {
      console.log(
        "Synthetic session ready. Select or reset scenarios in the browser; Close session finishes. This is not CI qualification.",
      );
      if (record) {
        console.log(
          "Use Record in Playwright Inspector. Keep recorded actions inside this fixture session; a standalone URL has no routes.",
        );
      }
      await session.finished;
      if (session.errors.length)
        throw new Error("Synthetic session failed closed", { cause: session.errors[0] });
    } finally {
      await session.close();
    }
  } finally {
    await server.close();
  }
});
