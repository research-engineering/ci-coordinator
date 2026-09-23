import { open, readFile, rename } from "node:fs/promises";
import { join } from "node:path";
import { expect, type Page, type Request as PlaywrightRequest, test } from "@playwright/test";

const mutationPath = "/api/v2/economics/budget-policies";
const sessionPath = "/api/v1/auth/session";

function requireValue(condition: boolean, code: string): asserts condition {
  if (!condition) throw new Error(code);
}

async function publishEvidence(path: string, value: string): Promise<void> {
  const temporaryPath = `${path}.tmp`;
  const output = await open(temporaryPath, "wx", 0o600);
  try {
    await output.writeFile(value, "utf8");
    await output.chmod(0o644);
  } finally {
    await output.close();
  }
  await rename(temporaryPath, path);
}

function forbiddenMutation(
  command: Record<string, unknown>,
  kind: "csrf" | "anonymous" | "canary",
) {
  const policyKey = command["policyKey"];
  const operationId = command["operationId"];
  requireValue(typeof policyKey === "string" && typeof operationId === "string", "commit-response");
  return {
    ...command,
    policyKey: `${policyKey}-${kind}`,
    operationId: `${kind}-${operationId}`,
    expectedRevision: 0,
  };
}

async function post(page: Page, body: object, csrf?: string) {
  return page.evaluate(
    async ({ body, csrf, mutationPath }) => {
      const response = await fetch(mutationPath, {
        method: "POST",
        credentials: "same-origin",
        headers: { "content-type": "application/json", ...(csrf ? { "x-csrf-token": csrf } : {}) },
        body: JSON.stringify(body),
      });
      return { status: response.status, body: (await response.json()) as Record<string, unknown> };
    },
    { body, csrf, mutationPath },
  );
}

test("real administrator budget mutation has one persisted audited effect", async ({
  page,
  context,
}) => {
  const fixturePath = process.env["BUSINESS_FIXTURE_FILE"];
  const evidence = process.env["BUSINESS_EVIDENCE_DIR"];
  requireValue(!!fixturePath && !!evidence, "fixture-unavailable");
  const fixture = JSON.parse(await readFile(fixturePath, "utf8")) as Record<string, string>;
  const username = fixture["username"];
  const password = fixture["password"];
  const actorId = fixture["actorId"];
  const policyKey = fixture["policyKey"];
  const logCanary = fixture["logCanary"];
  requireValue(
    !!username && !!password && !!actorId && !!policyKey && !!logCanary,
    "fixture-invalid",
  );
  const consoleMessages: string[] = [];
  page.on("console", (message) => {
    consoleMessages.push(message.text());
    requireValue(consoleMessages.join("\n").length <= 1_048_576, "console-output-limit");
  });
  let mutationRequests = 0;
  let policyWriteRequest: PlaywrightRequest | undefined;
  let policyWriteFinished = false;
  let policyWriteFailed = false;
  let policyWriteAborted = false;
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === mutationPath)
      mutationRequests += 1;
    if (
      policyWriteRequest === undefined &&
      request.method() === "POST" &&
      new URL(request.url()).origin === "https://coordinator.test" &&
      new URL(request.url()).pathname === mutationPath
    ) {
      policyWriteRequest = request;
    }
  });
  page.on("requestfinished", (request) => {
    if (request === policyWriteRequest) policyWriteFinished = true;
  });
  page.on("requestfailed", (request) => {
    if (request !== policyWriteRequest) return;
    policyWriteFailed = true;
    policyWriteAborted = request.failure()?.errorText === "net::ERR_ABORTED";
  });
  let command: Record<string, unknown> | undefined;
  let stageIndex = 0;
  let browserPhase = "authenticate";
  let policyReadStatus: number | null = null;
  let policyWriteStatus: number | null = null;
  let policyWriteMedia: "json" | "html" | "other" | null = null;
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.origin !== "https://coordinator.test") return;
    if (url.pathname === "/api/v2/economics/repositories/1/1/budget-policies")
      policyReadStatus = response.status();
    if (url.pathname === mutationPath && response.request() === policyWriteRequest) {
      policyWriteStatus = response.status();
      const media = response.headers()["content-type"]?.split(";", 1)[0]?.trim().toLowerCase();
      policyWriteMedia =
        media === "application/json" || media?.endsWith("+json")
          ? "json"
          : media === "text/html"
            ? "html"
            : "other";
    }
  });
  async function checkpoint(stage: string) {
    const name = `${String(stageIndex).padStart(2, "0")}-${stage}`;
    const path = join(evidence as string, `${name}.json`);
    await publishEvidence(path, JSON.stringify({ stage, actorId, command }));
    await expect
      .poll(
        async () => {
          try {
            return await readFile(join(evidence as string, `${name}.ack`), "utf8");
          } catch {
            return "pending";
          }
        },
        { message: `independent SQL checkpoint ${stage}`, timeout: 30_000 },
      )
      .toBe("accepted");
    stageIndex += 1;
  }
  try {
    await page.goto("/workbench");
    await expect(page).toHaveURL(/^https:\/\/auth\.example\.test\/realms\/coordinator\//);
    await page.locator("#username").fill(username);
    await page.locator("#password").fill(password);
    await page.locator("#kc-login").click();
    await expect(page).toHaveURL("https://coordinator.test/workbench");
    const session = await page.evaluate(async (path) => {
      const response = await fetch(path, { credentials: "same-origin" });
      return {
        status: response.status,
        body: (await response.json()) as {
          user: { actorId: string };
          roles: string[];
          csrfToken: string;
        },
      };
    }, sessionPath);
    requireValue(
      session.status === 200 && session.body.user.actorId === actorId,
      "session-identity",
    );
    requireValue(
      ["read", "audit", "configure"].every((role) => session.body.roles.includes(role)),
      "session-roles",
    );
    const cookies = await context.cookies("https://coordinator.test");
    requireValue(
      cookies.some(
        (cookie) =>
          cookie.name.includes("session") &&
          cookie.httpOnly &&
          cookie.secure &&
          cookie.sameSite === "Lax",
      ),
      "secure-session-cookie",
    );
    browserPhase = "authenticated-checkpoint";
    await checkpoint("authenticated");
    const scope =
      "/workbench?installationId=1&repositoryId=1&limit=10&view=economics&economicsTab=budgets";
    browserPhase = "navigate-budgets";
    await page.goto(scope);
    browserPhase = "open-editor";
    await page.getByRole("button", { name: "New policy", exact: true }).click();
    browserPhase = "fill-editor";
    await page.getByLabel("Policy key", { exact: true }).fill(policyKey);
    await page.getByLabel("Sample key", { exact: true }).fill("connected-sample");
    await page.getByLabel("Producer SHA-256", { exact: true }).fill("a".repeat(64));
    await page.getByLabel("Maximum (microseconds)", { exact: true }).fill("123456789");
    const pending = page
      .waitForResponse(
        (response) =>
          response.request() === policyWriteRequest &&
          response.request().method() === "POST" &&
          new URL(response.url()).origin === "https://coordinator.test" &&
          new URL(response.url()).pathname === mutationPath,
      )
      .then(async (response) => {
        browserPhase = "commit-response";
        const submitted = JSON.parse(response.request().postData() ?? "null") as Record<
          string,
          unknown
        >;
        return {
          submitted,
          status: response.status(),
          committed: (await response.json()) as Record<string, unknown>,
        };
      });
    browserPhase = "submit-policy";
    const [observed] = await Promise.all([
      pending,
      page.getByRole("button", { name: "Save policy", exact: true }).click(),
    ]);
    command = observed.submitted;
    requireValue(
      observed.status === 200 && observed.committed["outcome"] === "committed",
      "commit-response",
    );
    browserPhase = "committed-checkpoint";
    await checkpoint("committed");
    browserPhase = "reload-policy";
    await page.reload();
    await expect(page.getByText(policyKey, { exact: true })).toBeVisible();
    await expect(page.getByText(/revision 1$/)).toBeVisible();
    await page.getByRole("button", { name: `Edit ${policyKey}`, exact: true }).click();
    await expect(page.getByLabel("Maximum (microseconds)", { exact: true })).toHaveValue(
      "123456789",
    );
    await page.getByRole("button", { name: "Close editor", exact: true }).click();
    requireValue(mutationRequests === 1, "reload-replayed-command");
    await checkpoint("reloaded");
    browserPhase = "replay";
    const replay = await post(page, command, session.body.csrfToken);
    requireValue(replay.status === 200 && replay.body["outcome"] === "replayed", "replay-response");
    await checkpoint("replayed");
    browserPhase = "conflicts";
    const conflict = await post(
      page,
      { ...command, policyKey: `${policyKey}-changed` },
      session.body.csrfToken,
    );
    requireValue(
      conflict.status === 409 && conflict.body["outcome"] === "operation_conflict",
      "operation-conflict",
    );
    await checkpoint("operation-conflict");
    const stale = await post(
      page,
      { ...command, operationId: crypto.randomUUID() },
      session.body.csrfToken,
    );
    requireValue(
      stale.status === 409 && stale.body["outcome"] === "revision_conflict",
      "revision-conflict",
    );
    await checkpoint("revision-conflict");
    browserPhase = "csrf";
    requireValue(
      (await post(page, forbiddenMutation(command, "csrf"))).status === 403,
      "csrf-admission",
    );
    await checkpoint("csrf-rejected");
    browserPhase = "logout";
    const logout = await page.evaluate(async (csrf) => {
      const response = await fetch("/api/v1/auth/keycloak/logout", {
        method: "POST",
        credentials: "same-origin",
        headers: { "content-type": "application/json", "x-csrf-token": csrf },
        body: "{}",
      });
      return response.status;
    }, session.body.csrfToken);
    requireValue(logout === 200, "logout-response");
    browserPhase = "anonymous";
    requireValue(
      (await post(page, forbiddenMutation(command, "anonymous"), session.body.csrfToken)).status ===
        401,
      "anonymous-admission",
    );
    await checkpoint("unauthenticated");
    browserPhase = "canary";
    const rejected = await page.evaluate(
      async ({ mutationPath, logCanary, command }) => {
        const response = await fetch(mutationPath, {
          method: "POST",
          credentials: "omit",
          headers: { "content-type": "application/json", authorization: `Bearer ${logCanary}` },
          body: JSON.stringify(command),
        });
        return response.status;
      },
      { mutationPath, logCanary, command: forbiddenMutation(command, "canary") },
    );
    requireValue(rejected === 401, "canary-admission");
    await checkpoint("canary-rejected");
    browserPhase = "complete";
  } finally {
    await publishEvidence(
      join(evidence, "browser-progress.json"),
      JSON.stringify({
        browserPhase,
        policyReadStatus,
        policyWriteStatus,
        policyWriteMedia,
        policyWriteFinished,
        policyWriteFailed,
        policyWriteAborted,
      }),
    );
    await publishEvidence(
      join(evidence, "browser-console.log"),
      consoleMessages.join("\n") || "console-empty",
    );
  }
});
