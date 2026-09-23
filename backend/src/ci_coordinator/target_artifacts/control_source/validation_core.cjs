"use strict";

// Shared primitives for the generated target control bundle.

const { createHash, createPublicKey } = require("node:crypto");
const fs = require("node:fs");
const { TextDecoder } = require("node:util");

const MAX_CLOCK_SKEW_SECONDS = 300;
const MAX_ENVELOPE_BYTES = 1_048_576;
const MAX_GITHUB_OUTPUT_UTF16_BYTES = 786_432;
const MAX_PARALLEL = 256;
const MAX_PROFILES = 64;
const MAX_SERVICE_PROFILE_IDS = 16;
const MAX_SHARDS = 256;
const MAX_TESTS = 4_096;
const MAX_TESTS_PER_SHARD = 10_000;
const MAX_TTL_SECONDS = 300;
const MAX_TRUST_ROOT_BYTES = 16_384;
const MAX_WORKFLOWS = 32;
const DEFAULT_TRUST_ROOT_PATH = ".ci-coordinator/plan-trust-root.v1.json";
const IDENTIFIER = /^[a-z][a-z0-9._-]{0,63}$/;
const JOB_ID = /^[A-Za-z_][A-Za-z0-9_-]{0,127}$/;
const UTF8_DECODER = new TextDecoder("utf-8", { fatal: true });
const ENVELOPE_KEYS = new Set([
  "algorithm",
  "expiresAt",
  "issuedAt",
  "keyId",
  "payload",
  "schemaVersion",
  "signature",
]);
const PAYLOAD_KEYS = new Set([
  "authenticatedRun",
  "execution",
  "fallbackReason",
  "planId",
  "productionAdmissionReceiptId",
  "repository",
  "request",
  "schemaVersion",
  "verifiedPlanId",
  "verifierVersion",
]);

function finish(
  planValid,
  fallback,
  reason,
  matrices = {},
  maxParallelByJob = {},
  selectedJobs = [],
) {
  let output = outputRecord(
    planValid,
    fallback,
    reason,
    matrices,
    maxParallelByJob,
    selectedJobs,
  );
  if (Buffer.byteLength(output, "utf16le") > MAX_GITHUB_OUTPUT_UTF16_BYTES) {
    output = outputRecord(false, true, "provider_output_budget_exceeded", {}, {}, []);
  }
  fs.appendFileSync(process.env.GITHUB_OUTPUT, `${output}\n`);
}

function outputRecord(planValid, fallback, reason, matrices, maxParallelByJob, selectedJobs) {
  return [
    `plan_valid=${planValid ? "true" : "false"}`,
    `fallback=${fallback ? "true" : "false"}`,
    `reason=${oneLine(reason)}`,
    `matrices=${JSON.stringify(matrices)}`,
    `max_parallel_by_job=${JSON.stringify(maxParallelByJob)}`,
    `selected_jobs=${JSON.stringify(selectedJobs)}`,
  ].join("\n");
}

function oneLine(value) {
  return String(value ?? "unknown")
    .replace(/[^\x20-\x7E]/g, "_")
    .slice(0, 200);
}

function failClosed(reason) {
  finish(false, true, reason);
}

function hasExactKeys(value, expected) {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.keys(value).length === expected.size &&
    Object.keys(value).every((key) => expected.has(key))
  );
}

function canonicalStrings(
  value,
  { allowEmpty = false, maximum = 4096, predicate = () => true } = {},
) {
  if (!Array.isArray(value) || value.length > maximum || (!allowEmpty && value.length === 0)) {
    return false;
  }
  if (
    value.some(
      (item) => typeof item !== "string" || item.length === 0 || !predicate(item),
    )
  ) {
    return false;
  }
  const sorted = [...new Set(value)].sort(compareUtf16CodeUnits);
  return sorted.length === value.length && sorted.every((item, index) => item === value[index]);
}

function compareUnicodeCodePoints(left, right) {
  const leftCodePoints = Array.from(left);
  const rightCodePoints = Array.from(right);
  const length = Math.min(leftCodePoints.length, rightCodePoints.length);
  for (let index = 0; index < length; index += 1) {
    const difference = leftCodePoints[index].codePointAt(0) - rightCodePoints[index].codePointAt(0);
    if (difference !== 0) {
      return difference;
    }
  }
  return leftCodePoints.length - rightCodePoints.length;
}

function compareUtf16CodeUnits(left, right) {
  const length = Math.min(left.length, right.length);
  for (let index = 0; index < length; index += 1) {
    const difference = left.charCodeAt(index) - right.charCodeAt(index);
    if (difference !== 0) {
      return difference;
    }
  }
  return left.length - right.length;
}

function stableStringify(value) {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableStringify(item)).join(",")}]`;
  }
  return `{${Object.entries(value)
    .sort(([left], [right]) => compareUnicodeCodePoints(left, right))
    .map(([key, item]) => `${JSON.stringify(key)}:${stableStringify(item)}`)
    .join(",")}}`;
}

function digest(value) {
  return createHash("sha256").update(value).digest("hex");
}

class ValidationError extends Error {}

function reject(code) {
  throw new ValidationError(code);
}

function readJson(path, maximumBytes) {
  const bytes = readBoundedRegularFile(path, maximumBytes);
  let text;
  try {
    text = UTF8_DECODER.decode(bytes);
  } catch {
    reject("json_utf8_invalid");
  }
  if (!Buffer.from(text, "utf8").equals(bytes)) {
    reject("json_utf8_invalid");
  }
  try {
    return { bytes, text, value: JSON.parse(text) };
  } catch {
    reject("json_parse_invalid");
  }
}

function readBoundedRegularFile(path, maximumBytes) {
  if (typeof path !== "string" || path.length === 0) {
    reject("json_path_invalid");
  }
  let descriptor;
  try {
    descriptor = fs.openSync(
      path,
      fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW ?? 0),
    );
    const before = fs.fstatSync(descriptor, { bigint: true });
    if (!before.isFile() || before.size < 1n || before.size > BigInt(maximumBytes)) {
      reject("json_size_invalid");
    }
    const bytes = Buffer.alloc(Number(before.size));
    let offset = 0;
    while (offset < bytes.length) {
      const count = fs.readSync(descriptor, bytes, offset, bytes.length - offset, null);
      if (count === 0) {
        reject("json_file_changed");
      }
      offset += count;
    }
    const probe = Buffer.allocUnsafe(1);
    if (fs.readSync(descriptor, probe, 0, 1, null) !== 0) {
      reject("json_file_changed");
    }
    const after = fs.fstatSync(descriptor, { bigint: true });
    if (
      !after.isFile() ||
      after.dev !== before.dev ||
      after.ino !== before.ino ||
      after.size !== before.size ||
      after.mtimeNs !== before.mtimeNs ||
      after.ctimeNs !== before.ctimeNs
    ) {
      reject("json_file_changed");
    }
    return bytes;
  } catch (error) {
    if (error instanceof ValidationError) {
      throw error;
    }
    reject("json_read_failed");
  } finally {
    if (descriptor !== undefined) {
      fs.closeSync(descriptor);
    }
  }
}

function readPlanTrustRoot() {
  try {
    const path = process.env.CI_COORDINATOR_PLAN_TRUST_ROOT_PATH || DEFAULT_TRUST_ROOT_PATH;
    const document = readJson(path, MAX_TRUST_ROOT_BYTES);
    const root = document.value;
    if (
      !hasExactKeys(
        root,
        new Set(["algorithm", "keyId", "publicKeySpkiBase64", "schemaVersion"]),
      ) ||
      root.schemaVersion !== "ci-coordinator-plan-trust-root/v1" ||
      root.algorithm !== "Ed25519" ||
      typeof root.keyId !== "string" ||
      !/^[A-Za-z0-9._-]{1,128}$/.test(root.keyId) ||
      typeof root.publicKeySpkiBase64 !== "string" ||
      root.publicKeySpkiBase64.length > 4096 ||
      !/^[A-Za-z0-9+/]+={0,2}$/.test(root.publicKeySpkiBase64) ||
      root.publicKeySpkiBase64.length % 4 !== 0 ||
      document.text !== `${stableStringify(root)}\n`
    ) {
      reject("plan_trust_root_invalid");
    }
    const der = Buffer.from(root.publicKeySpkiBase64, "base64");
    if (der.toString("base64") !== root.publicKeySpkiBase64) {
      reject("plan_trust_root_invalid");
    }
    const publicKey = createPublicKey({ key: der, format: "der", type: "spki" });
    if (publicKey.type !== "public" || publicKey.asymmetricKeyType !== "ed25519") {
      reject("plan_trust_root_invalid");
    }
    return { keyId: root.keyId, publicKey };
  } catch {
    reject("plan_trust_root_invalid");
  }
}

module.exports = Object.freeze({
  ENVELOPE_KEYS,
  IDENTIFIER,
  JOB_ID,
  MAX_CLOCK_SKEW_SECONDS,
  MAX_ENVELOPE_BYTES,
  MAX_PARALLEL,
  MAX_PROFILES,
  MAX_SERVICE_PROFILE_IDS,
  MAX_SHARDS,
  MAX_TESTS,
  MAX_TESTS_PER_SHARD,
  MAX_TTL_SECONDS,
  MAX_WORKFLOWS,
  PAYLOAD_KEYS,
  canonicalStrings,
  compareUtf16CodeUnits,
  digest,
  failClosed,
  finish,
  hasExactKeys,
  readJson,
  readPlanTrustRoot,
  reject,
  stableStringify,
});
