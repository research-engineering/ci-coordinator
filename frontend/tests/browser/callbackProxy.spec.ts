import { once } from "node:events";
import { mkdtemp, rm } from "node:fs/promises";
import { createServer as createRecorder } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";

// This exercises Vite forwarding, not a provider login or backend issuer admission.
test("actual development config forwards distinct callback shapes without rewriting their queries", async ({
  request,
}) => {
  const root = await mkdtemp(join(tmpdir(), "ci-callback-forwarding-"));
  const seen: { method: string | undefined; url: string | undefined }[] = [];
  const recorder = createRecorder((incoming, response) => {
    seen.push({ method: incoming.method, url: incoming.url });
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({ marker: "loopback-recorder", path: incoming.url }));
  });
  const previous = process.env["CI_COORDINATOR_DEV_API_URL"];
  let vite: ViteDevServer | undefined;
  try {
    recorder.listen(0, "127.0.0.1");
    await once(recorder, "listening");
    const target = recorder.address();
    if (!target || typeof target === "string") throw new Error("Recorder has no loopback address");
    process.env["CI_COORDINATOR_DEV_API_URL"] = `http://127.0.0.1:${target.port}`;
    const configFile = fileURLToPath(new URL("../../vite.config.ts", import.meta.url));
    vite = await createServer({
      configFile,
      configLoader: "native",
      root,
      logLevel: "silent",
      server: { host: "127.0.0.1", port: 0 },
    });
    expect(vite.config.configFile).toBe(configFile);
    await vite.listen();
    const address = vite.httpServer?.address();
    if (!address || typeof address === "string") throw new Error("Vite has no loopback address");
    const origin = `http://127.0.0.1:${address.port}`;
    const keycloak = "/api/v1/auth/keycloak/callback?";
    const github = "/api/v1/repository-attestations/github/callback?";
    const state = "s".repeat(43);
    const valid = [
      keycloak +
        new URLSearchParams({
          code: "provider",
          state,
          iss: "https://identity.example/realms/operators",
        }),
      keycloak +
        new URLSearchParams({
          code: "\u{1f642}".repeat(1024),
          state,
          iss: "i".repeat(2048),
          session_state: "x".repeat(512),
        }),
      github + new URLSearchParams({ code: "g".repeat(512), state }),
    ];
    for (const path of valid) {
      const response = await request.get(origin + path);
      expect(response.status()).toBe(200);
      expect(await response.json()).toEqual({ marker: "loopback-recorder", path });
      expect(seen.at(-1)).toEqual({ method: "GET", url: path });
    }
    expect(seen).toHaveLength(3);
    const invalid = [
      keycloak + new URLSearchParams({ code: "provider", state }),
      keycloak + new URLSearchParams({ code: "x".repeat(1025), state, iss: "issuer" }),
      `${valid[0]}&%69ss=duplicate`,
      `${valid[0]}&session_state=one&session_state=two`,
      github + new URLSearchParams({ code: "provider", state, iss: "issuer" }),
      github + new URLSearchParams({ code: "x".repeat(513), state }),
    ];
    for (const path of invalid) expect((await request.get(origin + path)).status()).toBe(404);
    expect((await request.post(origin + valid[0])).status()).toBe(404);
    expect(seen).toHaveLength(3);
  } finally {
    if (previous === undefined) delete process.env["CI_COORDINATOR_DEV_API_URL"];
    else process.env["CI_COORDINATOR_DEV_API_URL"] = previous;
    try {
      await vite?.close();
    } finally {
      try {
        if (recorder.listening)
          await new Promise<void>((resolve, reject) =>
            recorder.close((error) => (error ? reject(error) : resolve())),
          );
      } finally {
        await rm(root, { recursive: true, force: true });
      }
    }
  }
});
