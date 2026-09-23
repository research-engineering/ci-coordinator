import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";

export const FRONTEND_ROOT = fileURLToPath(new URL("..", import.meta.url));
export const SYNTHETIC_CSP = [
  "default-src 'none'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self'",
  "worker-src 'none'",
  "frame-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
].join("; ");

export function assetPath(path: string): boolean {
  if (/[\\%]/.test(path) || path.includes("//")) return false;
  return (
    path === "/@vite/client" ||
    path === "/@vite/env" ||
    path === "/@react-refresh" ||
    ["/src/", "/node_modules/", "/@fs/", "/@id/"].some((prefix) => path.startsWith(prefix))
  );
}

export async function startDemoServer() {
  const cache = await mkdtemp(join(tmpdir(), "ci-coordinator-ui-demo-"));
  const server = await createServer({
    configFile: false,
    envFile: false,
    envPrefix: [],
    root: FRONTEND_ROOT,
    publicDir: false,
    cacheDir: cache,
    plugins: [
      react(),
      {
        name: "synthetic-session-boundary",
        configureServer(vite) {
          vite.httpServer?.on("connect", (_request, socket) => {
            // APIRequestContext uses CONNECT even for HTTP destinations. Send a
            // complete denial before closing; a pending tunnel is not a denial.
            socket.end(
              "HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
              () => socket.destroy(),
            );
          });
          vite.middlewares.use((request, result, next) => {
            const raw = request.url ?? "";
            const path = raw.split("?", 1)[0] ?? "";
            // Absolute-form and CONNECT traffic must never turn this server into a forwarding proxy.
            if (
              !raw.startsWith("/") ||
              raw.startsWith("//") ||
              !["GET", "HEAD"].includes(request.method ?? "") ||
              !(path === "/" || path === "/workbench" || assetPath(path))
            ) {
              const body = "Synthetic session: request blocked";
              result.writeHead(403, {
                "content-type": "text/plain",
                "content-length": Buffer.byteLength(body),
                "cache-control": "no-store",
                connection: "close",
              });
              result.end(body);
              return;
            }
            result.setHeader("Content-Security-Policy", SYNTHETIC_CSP);
            result.setHeader("Cache-Control", "no-store");
            next();
          });
        },
      },
    ],
    server: {
      host: "127.0.0.1",
      port: 0,
      strictPort: false,
      hmr: false,
      fs: {
        strict: true,
        allow: [
          FRONTEND_ROOT,
          cache,
          fileURLToPath(new URL("../../node_modules", import.meta.url)),
        ],
      },
    },
  }).catch(async (error: unknown) => {
    await rm(cache, { recursive: true, force: true });
    throw error;
  });
  try {
    await server.listen();
    const address = server.httpServer?.address();
    if (!address || typeof address === "string" || address.address !== "127.0.0.1")
      throw new Error("Synthetic Vite server did not bind its owned loopback endpoint");
    return {
      origin: `http://127.0.0.1:${address.port}`,
      async close() {
        try {
          await server.close();
        } finally {
          await rm(cache, { recursive: true, force: true });
        }
      },
    };
  } catch (error) {
    try {
      await server.close();
    } finally {
      await rm(cache, { recursive: true, force: true });
    }
    throw error;
  }
}

export type DemoServer = Awaited<ReturnType<typeof startDemoServer>>;
