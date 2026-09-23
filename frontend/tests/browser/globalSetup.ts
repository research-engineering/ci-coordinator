import { fileURLToPath } from "node:url";
import { preview } from "vite";

const BASE_URL_ENV = "CI_COORDINATOR_PLAYWRIGHT_BASE_URL";

export default async function startPreview() {
  const server = await preview({
    preview: { host: "127.0.0.1", port: 0, strictPort: false },
    root: fileURLToPath(new URL("../..", import.meta.url)),
  });
  const address = server.httpServer.address();
  if (address === null || typeof address === "string") {
    await server.close();
    throw new Error("Vite Preview did not expose an IP endpoint");
  }
  process.env[BASE_URL_ENV] = `http://127.0.0.1:${address.port}`;
  return async () => server.close();
}
