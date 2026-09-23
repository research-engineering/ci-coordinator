import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import {
  readConfigStatus,
  registerSource,
  rollbackConfig,
  validateSource,
} from "../src/api/configLifecycle/client";
import {
  type ConfigError,
  MAX_REASON_BYTES,
  MAX_SOURCE_BYTES,
  rollbackCommandSchema,
  sourceSchema,
} from "../src/api/configLifecycle/schema";
import { observeResponses } from "../src/api/shared/responseObservation";
import {
  configEpoch,
  configJson,
  configStatus,
  configValidation,
  rollbackCommand,
  configurationScope as scope,
  configurationSource as source,
  validatedSource,
} from "./configurationFixture";

const csrf = "c".repeat(43);
beforeEach(() => vi.stubGlobal("crypto", webcrypto));
afterEach(() => vi.unstubAllGlobals());

test.each(["json", "yaml-1.2"] as const)(
  "validation binds exact %s source, scope, identity and transport",
  async (format) => {
    const exact = `${source}\r\n`;
    const fetch = vi.fn(async (request: Request) => {
      expect(new URL(request.url).pathname).toBe("/api/v1/config/validations");
      expect(request.method).toBe("POST");
      expect(request.credentials).toBe("same-origin");
      expect(request.cache).toBe("no-store");
      expect(request.redirect).toBe("error");
      expect(request.headers.get("x-csrf-token")).toBe(csrf);
      expect(await request.json()).toEqual({
        schemaVersion: "ci-config-epoch-validation/v1",
        source: exact,
        sourceFormat: format,
      });
      return configJson(configValidation(exact, format));
    });
    vi.stubGlobal("fetch", fetch);
    const result = await validateSource(scope, exact, format, csrf);
    expect(result).toEqual({
      kind: "ready",
      value: {
        scope,
        source: exact,
        sourceFormat: format,
        validation: configValidation(exact, format),
      },
    });
  },
);

test.each([
  { installationId: 2 },
  { repositoryId: 2 },
  { sourceHash: "a".repeat(64) },
  { epochId: "b".repeat(64) },
  { documentHash: "c".repeat(64) },
  { epochHash: "d".repeat(64) },
  { repositoryId: Number.MAX_SAFE_INTEGER + 1 },
  { sourceHash: "A".repeat(64) },
  { documentSchemaId: "\u00e9".repeat(2049) },
  { documentProfileId: "" },
  { semanticProfileId: "\ud800" },
  { compiledSchemaId: "\u00e9".repeat(2049) },
  { extra: true },
])("rejects validation operand drift %j", async (delta) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => configJson({ ...configValidation(), ...delta })),
  );
  expect((await validateSource(scope, source, "json", csrf)).kind).toBe("invalid-response");
});

test("validation cannot be reused for a changed byte or declared format", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => configJson(configValidation())),
  );
  expect((await validateSource(scope, `${source}\n`, "json", csrf)).kind).toBe("invalid-response");
  expect((await validateSource(scope, source, "yaml-1.2", csrf)).kind).toBe("invalid-response");
});

test("source and reason limits count UTF-8 bytes and reject lone surrogates", async () => {
  expect(
    sourceSchema.safeParse({ source: "\u00e9".repeat(MAX_SOURCE_BYTES / 2), sourceFormat: "json" })
      .success,
  ).toBe(true);
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  for (const invalid of ["", "\u00e9".repeat(MAX_SOURCE_BYTES / 2 + 1), "\ud800"]) {
    expect((await validateSource(scope, invalid, "json", csrf)).kind).toBe("invalid-request");
  }
  const body = rollbackCommand().body;
  expect(
    rollbackCommandSchema.safeParse({ ...body, reason: "\u00e9".repeat(MAX_REASON_BYTES / 2) })
      .success,
  ).toBe(true);
  for (const reason of [" ", "\u00e9".repeat(MAX_REASON_BYTES / 2 + 1), "a\0b", "\ud800"]) {
    expect(rollbackCommandSchema.safeParse({ ...body, reason }).success).toBe(false);
  }
  expect(fetch).not.toHaveBeenCalled();
});

test("registration transmits captured bytes and exact operation; created and replay are distinct", async () => {
  const draft = validatedSource(`${source}\r\n`);
  const command = { kind: "registration" as const, draft, operationId: "register-one" };
  const bodies: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      expect(new URL(request.url).pathname).toBe("/api/v1/config/epochs");
      bodies.push(await request.json());
      return configJson(
        {
          schemaVersion: "ci-config-epoch-registration-result/v1",
          ok: true,
          epochId: draft.validation.epochId,
          duplicate: bodies.length > 1,
        },
        bodies.length === 1 ? 201 : 200,
      );
    }),
  );
  expect((await registerSource(command, csrf)).kind).toBe("ready");
  expect((await registerSource(command, csrf)).kind).toBe("ready");
  expect(bodies[0]).toEqual({
    schemaVersion: "ci-config-epoch-registration/v1",
    source: draft.source,
    sourceFormat: "json",
    operationId: "register-one",
  });
  expect(bodies[1]).toEqual(bodies[0]);
});

test("changed registration commands and missing CSRF never reach fetch", async () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  const command = { kind: "registration" as const, draft: validatedSource(), operationId: "one" };
  for (const draft of [
    { ...command.draft, source: `${source}\n` },
    { ...command.draft, sourceFormat: "yaml-1.2" as const },
    { ...command.draft, scope: { ...scope, repositoryId: 2 } },
  ]) {
    expect((await registerSource({ ...command, draft }, csrf)).kind).toBe("invalid-request");
  }
  expect((await registerSource({ ...command, operationId: "\u00e9".repeat(129) }, csrf)).kind).toBe(
    "invalid-request",
  );
  expect((await registerSource(command, "")).kind).not.toBe("ready");
  expect(fetch).not.toHaveBeenCalled();
});

test.each([
  { epochId: "a".repeat(64), duplicate: false, status: 201 },
  { epochId: configEpoch().epochId, duplicate: true, status: 201 },
  { epochId: configEpoch().epochId, duplicate: false, status: 200 },
])("rejects registration success drift %j", async ({ status, ...fields }) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      configJson(
        { schemaVersion: "ci-config-epoch-registration-result/v1", ok: true, ...fields },
        status,
      ),
    ),
  );
  expect(
    (
      await registerSource(
        { kind: "registration", draft: validatedSource(), operationId: "one" },
        csrf,
      )
    ).kind,
  ).toBe("invalid-response");
});

test("status uses the actual afterEpochId alias and no other cursor spelling", async () => {
  const page = configStatus();
  const last = page.epochs.at(-1);
  const first = page.epochs[0];
  if (!last || !first) throw new Error("Fixture epochs missing");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      expect(new URL(request.url).pathname).toBe("/api/v1/config/repositories/1/1/status");
      expect([...new URL(request.url).searchParams]).toEqual([
        ["limit", "1"],
        ["afterEpochId", first.epochId],
      ]);
      return configJson({ ...page, epochs: [last], nextCursor: last.epochId });
    }),
  );
  expect((await readConfigStatus(scope, first.epochId, undefined, 1)).kind).toBe("ready");
});

test.each([
  { repositoryId: 2 },
  { installationId: 2 },
  { epochs: [...configStatus().epochs].reverse() },
  { epochs: [configEpoch(), configEpoch()] },
  { nextCursor: "f".repeat(64) },
  { epochs: [], nextCursor: configEpoch().epochId },
  { active: { epochId: "wrong", revision: 1 } },
  { epochs: [{ ...configEpoch(), sourceByteCount: MAX_SOURCE_BYTES + 1 }] },
  { active: { epochId: configEpoch().epochId, revision: 0 } },
])("rejects raw status drift %j", async (delta) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => configJson({ ...configStatus(), ...delta })),
  );
  expect((await readConfigStatus(scope, null)).kind).toBe("invalid-response");
});

test("status cannot exceed requested page bound or return rows before the requested cursor", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => configJson(configStatus())),
  );
  expect((await readConfigStatus(scope, null, undefined, 1)).kind).toBe("invalid-response");
  expect((await readConfigStatus(scope, "f".repeat(64))).kind).toBe("invalid-response");
});

test.each([0, 101, 1.5, Number.MAX_SAFE_INTEGER + 1])(
  "invalid status limit %s performs no fetch",
  async (limit) => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    expect((await readConfigStatus(scope, null, undefined, limit)).kind).toBe("invalid-request");
    expect(fetch).not.toHaveBeenCalled();
  },
);

test.each([false, true])(
  "rollback result binds target and exact successor revision, replay=%s",
  async (duplicate) => {
    const command = rollbackCommand();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        expect(new URL(request.url).pathname).toBe("/api/v1/config/rollbacks");
        expect(await request.json()).toEqual(command.body);
        return configJson({
          schemaVersion: "ci-config-epoch-activation-result/v1",
          ok: true,
          epochId: command.body.targetEpochId,
          revision: 5,
          duplicate,
        });
      }),
    );
    expect((await rollbackConfig(command, csrf)).kind).toBe("ready");
  },
);

test.each([{ revision: 4 }, { revision: 6 }, { epochId: "f".repeat(64) }])(
  "rollback rejects outcome drift %j",
  async (delta) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        configJson({
          schemaVersion: "ci-config-epoch-activation-result/v1",
          ok: true,
          epochId: rollbackCommand().body.targetEpochId,
          revision: 5,
          duplicate: true,
          ...delta,
        }),
      ),
    );
    expect((await rollbackConfig(rollbackCommand(), csrf)).kind).toBe("invalid-response");
  },
);

test.each([
  [401, "unauthenticated"],
  [403, "forbidden"],
  [409, "revision_conflict"],
  [409, "coverage_reducing"],
  [409, "coverage_unproven"],
  [503, "unavailable"],
] as const)("typed failure %s/%s remains observable", async (status, error) => {
  const observed = vi.fn();
  const stop = observeResponses(observed);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => configJson({ ok: false, error, diagnostics: [] }, status)),
  );
  expect((await rollbackConfig(rollbackCommand(), csrf)).kind).toBe(error);
  expect(observed).toHaveBeenCalledWith(expect.any(Request), status);
  stop();
});

test("typed diagnostics are preserved, while mismatched error status is rejected", async () => {
  const error: ConfigError = {
    ok: false,
    error: "invalid_config",
    diagnostics: [
      {
        code: "source.invalid",
        phase: "source",
        ruleId: "source.bytes",
        instancePointer: "/repository",
      },
    ],
  };
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(configJson(error, 422))
    .mockResolvedValueOnce(configJson(error, 200));
  vi.stubGlobal("fetch", fetch);
  expect(await validateSource(scope, source, "json", csrf)).toEqual({
    kind: "invalid_config",
    diagnostics: error.diagnostics,
  });
  expect((await validateSource(scope, source, "json", csrf)).kind).toBe("invalid-response");
});

test("missing no-store and malformed media cannot be accepted", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(Response.json(configStatus()))
    .mockResolvedValueOnce(
      new Response("<b>provider</b>", {
        headers: { "content-type": "text/html", "cache-control": "no-store" },
      }),
    );
  vi.stubGlobal("fetch", fetch);
  expect((await readConfigStatus(scope, null)).kind).toBe("invalid-response");
  expect((await readConfigStatus(scope, null)).kind).toBe("invalid-response");
});
