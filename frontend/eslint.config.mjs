import { defineConfig, globalIgnores } from "eslint/config";
import tseslint from "typescript-eslint";

const transportOwner = "src/api/shared/boundedFetch.ts";
const networkGlobals = ["fetch", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon"];
const transportMessage = "Use the admitted boundedFetch transport owner.";
const networkRules = (allowFetch) => {
  const restricted = networkGlobals.filter((name) => !allowFetch || name !== "fetch");
  return {
    "no-restricted-globals": [
      "error",
      ...[...restricted, "window", "self"].map((name) => ({ name, message: transportMessage })),
    ],
    "no-restricted-properties": [
      "error",
      ...restricted.map((property) => ({ property, message: transportMessage })),
      ...["window", "self", "globalThis", "top", "parent", "frames"].map((property) => ({
        object: "globalThis",
        property,
        message: "Do not alias the browser global object.",
      })),
    ],
    "no-restricted-syntax": [
      "error",
      {
        selector: "MemberExpression[object.name='globalThis'][computed=true]",
        message: "Use a static named global member so transport ownership remains reviewable.",
      },
      {
        selector:
          "Identifier[name='globalThis']:not(MemberExpression > Identifier.object):not(MemberExpression > Identifier.property):not(Property > Identifier.key):not(TSTypeQuery > Identifier.exprName)",
        message: "Do not pass, destructure, cast or alias the browser global object.",
      },
    ],
  };
};

export default defineConfig(
  globalIgnores(["src/api/generated.ts"]),
  {
    files: ["src/**/*.{ts,tsx,mts,cts}", "tests/**/*.{ts,tsx}", "dev/**/*.ts"],
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
        onUnsupportedTypeScriptVersion: "error",
      },
    },
    plugins: { "@typescript-eslint": tseslint.plugin },
    rules: {
      "@typescript-eslint/no-floating-promises": ["error", { checkThenables: true }],
    },
  },
  {
    files: ["src/**/*.{ts,tsx,mts,cts}"],
    ignores: [transportOwner],
    rules: networkRules(false),
  },
  {
    files: [transportOwner],
    rules: networkRules(true),
  },
);
