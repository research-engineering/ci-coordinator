"use strict";

const fs = require("node:fs");
const { gunzipSync } = require("node:zlib");

const CHUNK_COUNT = 7;
const MAX_CHUNK_CHARACTERS = 65_536;
const MAX_COMPRESSED_BYTES = 344_064;
const MAX_PLAN_BYTES = 1_048_576;
const FALLBACK_REASON = /^[a-z][a-z0-9_]{0,127}$/;
const BASE64URL = /^[A-Za-z0-9_-]+$/;
let temporaryCounter = 0;

class FallbackError extends Error {
  constructor(reason) {
    super(reason);
    this.reason = reason;
  }
}

function planPath() {
  const value = process.env.PLAN_PATH;
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > 4_096 ||
    value.includes("\0")
  ) {
    throw new Error("plan path is invalid");
  }
  return value;
}

function writePrivate(content) {
  const target = planPath();
  const temporary = `${target}.tmp-${process.pid}-${temporaryCounter}`;
  temporaryCounter += 1;
  let created = false;
  try {
    const descriptor = fs.openSync(
      temporary,
      fs.constants.O_WRONLY |
        fs.constants.O_CREAT |
        fs.constants.O_EXCL |
        fs.constants.O_NOFOLLOW,
      0o600,
    );
    created = true;
    try {
      fs.writeFileSync(descriptor, content);
    } finally {
      fs.closeSync(descriptor);
    }
    fs.renameSync(temporary, target);
    created = false;
  } finally {
    if (created) {
      try {
        fs.unlinkSync(temporary);
      } catch {
        // The original write error remains authoritative.
      }
    }
  }
}

function fallbackReason() {
  const value = process.env.CI_COORDINATOR_PLAN_REASON;
  return typeof value === "string" && FALLBACK_REASON.test(value)
    ? value
    : "plan_request_unavailable";
}

function writeFallback(reason) {
  const admittedReason = FALLBACK_REASON.test(reason)
    ? reason
    : "plan_request_unavailable";
  writePrivate(
    `${JSON.stringify({
      schemaVersion: "dynamic-ci-plan/v0",
      fallback: true,
      reason: admittedReason,
    })}\n`,
  );
}

function encodedTransport() {
  const chunks = Array.from(
    { length: CHUNK_COUNT },
    (_, index) => process.env[`CI_COORDINATOR_PLAN_CHUNK_${index}`] || "",
  );
  const firstEmpty = chunks.findIndex((value) => value.length === 0);
  const presentCount = firstEmpty === -1 ? CHUNK_COUNT : firstEmpty;
  if (presentCount === 0) {
    throw new FallbackError(fallbackReason());
  }
  if (chunks.slice(presentCount).some((value) => value.length !== 0)) {
    throw new FallbackError("plan_transport_chunks_invalid");
  }
  const present = chunks.slice(0, presentCount);
  if (
    present.some(
      (value) =>
        value.length > MAX_CHUNK_CHARACTERS || !BASE64URL.test(value),
    )
  ) {
    throw new FallbackError("plan_transport_chunks_invalid");
  }
  const encoded = present.join("");
  if (fallbackReason() !== "signed_plan_available") {
    throw new FallbackError("plan_transport_state_invalid");
  }
  return encoded;
}

function decodePlan() {
  const encoded = encodedTransport();
  const compressed = Buffer.from(encoded, "base64url");
  if (
    compressed.length === 0 ||
    compressed.length > MAX_COMPRESSED_BYTES ||
    compressed.toString("base64url") !== encoded
  ) {
    throw new FallbackError("plan_transport_encoding_invalid");
  }
  let content;
  try {
    content = gunzipSync(compressed, { maxOutputLength: MAX_PLAN_BYTES });
  } catch {
    throw new FallbackError("plan_transport_compression_invalid");
  }
  if (content.length === 0 || content.length > MAX_PLAN_BYTES) {
    throw new FallbackError("plan_transport_size_invalid");
  }
  let value;
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(content);
    value = JSON.parse(text);
  } catch {
    throw new FallbackError("coordinator_plan_response_invalid");
  }
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new FallbackError("coordinator_plan_response_invalid");
  }
  return content;
}

function main() {
  try {
    writePrivate(decodePlan());
  } catch (error) {
    try {
      writeFallback(
        error instanceof FallbackError ? error.reason : "plan_request_unavailable",
      );
    } catch {
      process.exitCode = 1;
    }
  }
}

module.exports = Object.freeze({ main });
