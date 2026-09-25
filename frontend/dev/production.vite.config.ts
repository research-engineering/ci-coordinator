import { defineConfig, mergeConfig } from "vite";
import applicationConfig from "../vite.config.ts";
import { productionBoundary } from "./productionBoundary.ts";

export default defineConfig(async (environment) => {
  const application = await (typeof applicationConfig === "function"
    ? applicationConfig(environment)
    : applicationConfig);
  return mergeConfig(application, { base: "./", plugins: [productionBoundary()] });
});
