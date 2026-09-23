"use strict";

const {
  MAX_ENVELOPE_BYTES,
  failClosed,
  finish,
  readJson,
  readPlanTrustRoot,
} = require("./validation_core.cjs");
const { verifyEnvelope } = require("./validation_envelope.cjs");
const {
  loadRegistry,
  loadStaticJobIds,
  loadWorkflowPath,
  validatePayload,
} = require("./validation_execution.cjs");

function main() {
  try {
    const response = readJson(process.env.PLAN_PATH, MAX_ENVELOPE_BYTES).value;
    if (response?.schemaVersion === "dynamic-ci-plan/v0" && response.fallback === true) {
      finish(false, true, response.reason ?? "unsigned_fallback_response");
      return;
    }
    const trustRoot = readPlanTrustRoot();
    const envelopeError = verifyEnvelope(response, trustRoot.publicKey, trustRoot.keyId);
    if (envelopeError) {
      failClosed(envelopeError);
      return;
    }
    const registry = loadRegistry(loadStaticJobIds(), loadWorkflowPath());
    const result = validatePayload(response.payload, registry);
    if (result.reason && !result.fallback) {
      failClosed(result.reason);
      return;
    }
    if (result.fallback) {
      finish(true, true, result.reason);
      return;
    }
    finish(
      true,
      false,
      "signed_plan_valid",
      result.matrices,
      result.maxParallelByJob,
      result.selectedJobs,
    );
  } catch (error) {
    failClosed(error instanceof Error ? error.message : "plan_validation_failed");
  }
}

module.exports = Object.freeze({ main });
