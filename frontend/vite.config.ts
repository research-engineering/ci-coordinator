import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";
import {
  OPERATOR_API_PROXY_CONTEXT,
  proxyRequestIsAdmitted,
} from "./src/api/development/proxyPolicy.ts";
import { admitProxyTarget } from "./src/api/development/proxyTarget.ts";

export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, process.cwd(), "CI_COORDINATOR_");
  const target = admitProxyTarget(environment["CI_COORDINATOR_DEV_API_URL"]);

  return {
    build: {
      sourcemap: false,
      target: "es2024",
    },
    plugins: [react()],
    server: {
      watch: {
        awaitWriteFinish: true,
      },
      proxy: {
        [OPERATOR_API_PROXY_CONTEXT]: {
          bypass(request) {
            return proxyRequestIsAdmitted(request.method, request.url) ? undefined : false;
          },
          changeOrigin: false,
          target,
        },
      },
    },
  };
});
