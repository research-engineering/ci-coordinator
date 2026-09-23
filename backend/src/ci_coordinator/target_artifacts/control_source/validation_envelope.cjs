"use strict";

const { verify } = require("node:crypto");
const {
  ENVELOPE_KEYS,
  MAX_CLOCK_SKEW_SECONDS,
  MAX_TTL_SECONDS,
  PAYLOAD_KEYS,
  hasExactKeys,
  stableStringify,
} = require("./validation_core.cjs");

function verifyEnvelope(envelope, publicKey, expectedKeyId) {
  if (!hasExactKeys(envelope, ENVELOPE_KEYS)) {
    return "signed_plan_envelope_shape_invalid";
  }
  if (
    envelope.schemaVersion !== "dynamic-ci-signed-plan-envelope/v1" ||
    envelope.algorithm !== "Ed25519"
  ) {
    return "signed_plan_envelope_schema_unsupported";
  }
  if (envelope.keyId !== expectedKeyId) {
    return "signed_plan_key_id_mismatch";
  }
  if (!hasExactKeys(envelope.payload, PAYLOAD_KEYS)) {
    return "signed_plan_payload_shape_invalid";
  }
  if (envelope.payload.schemaVersion !== "dynamic-ci-signed-plan-payload/v2") {
    return "signed_plan_payload_schema_unsupported";
  }
  if (typeof envelope.signature !== "string" || envelope.signature.length === 0) {
    return "signed_plan_signature_missing";
  }
  if (!/^[A-Za-z0-9_-]{86}$/.test(envelope.signature)) {
    return "signed_plan_signature_invalid";
  }
  const signature = Buffer.from(envelope.signature, "base64url");
  if (signature.length !== 64 || signature.toString("base64url") !== envelope.signature) {
    return "signed_plan_signature_invalid";
  }

  const now = Date.now();
  const issuedAt = Date.parse(envelope.issuedAt);
  const expiresAt = Date.parse(envelope.expiresAt);
  if (!Number.isFinite(issuedAt) || !Number.isFinite(expiresAt)) {
    return "signed_plan_time_invalid";
  }
  if (issuedAt > now + MAX_CLOCK_SKEW_SECONDS * 1000) {
    return "signed_plan_issued_in_future";
  }
  if (expiresAt <= now) {
    return "signed_plan_expired";
  }
  if (expiresAt <= issuedAt || expiresAt - issuedAt > MAX_TTL_SECONDS * 1000) {
    return "signed_plan_ttl_invalid";
  }

  const unsigned = {
    schemaVersion: envelope.schemaVersion,
    keyId: envelope.keyId,
    algorithm: envelope.algorithm,
    issuedAt: envelope.issuedAt,
    expiresAt: envelope.expiresAt,
    payload: envelope.payload,
  };
  try {
    const valid = verify(
      null,
      Buffer.from(stableStringify(unsigned), "utf8"),
      publicKey,
      signature,
    );
    return valid ? null : "signed_plan_signature_invalid";
  } catch {
    return "signed_plan_signature_invalid";
  }
}

function optionalInteger(value) {
  if (!value || !/^\d+$/.test(value)) {
    return null;
  }
  const parsed = Number.parseInt(value, 10);
  return Number.isSafeInteger(parsed) ? parsed : null;
}

function validateBinding(payload) {
  const request = payload.request;
  const repository = payload.repository;
  if (
    !hasExactKeys(repository, new Set(["installationId", "owner", "repository", "repositoryId"])) ||
    !hasExactKeys(
      request,
      new Set([
        "baseSha",
        "eventName",
        "executionSha",
        "headSha",
        "installationId",
        "mergeGroupHeadRef",
        "owner",
        "pullRequestNumber",
        "ref",
        "repository",
        "repositoryId",
        "requestId",
        "runAttempt",
        "schemaVersion",
        "workflowRunId",
      ]),
    )
  ) {
    return "request_binding_shape_invalid";
  }
  const installationId = optionalInteger(process.env.CI_INSTALLATION_ID);
  const repositoryId = optionalInteger(process.env.CI_REPOSITORY_ID);
  const workflowRunId = optionalInteger(process.env.CI_RUN_ID);
  const runAttempt = optionalInteger(process.env.CI_RUN_ATTEMPT);
  const pullRequestNumber = optionalInteger(process.env.CI_PULL_REQUEST_NUMBER);
  const mergeGroupHeadRef = process.env.CI_MERGE_GROUP_HEAD_REF || null;
  const expected = [
    [repository.repositoryId, repositoryId, "repository_id_mismatch"],
    [repository.owner, process.env.CI_OWNER, "repository_owner_mismatch"],
    [repository.repository, process.env.CI_REPO, "repository_name_mismatch"],
    [request.schemaVersion, "dynamic-ci-plan-request/v2", "request_schema_mismatch"],
    [request.requestId, `${repositoryId}:${workflowRunId}:${runAttempt}`, "request_id_mismatch"],
    [request.installationId, installationId, "request_installation_id_mismatch"],
    [request.repositoryId, repositoryId, "request_repository_id_mismatch"],
    [request.owner, process.env.CI_OWNER, "request_owner_mismatch"],
    [request.repository, process.env.CI_REPO, "request_repository_mismatch"],
    [request.eventName, process.env.CI_EVENT, "event_name_mismatch"],
    [request.ref, process.env.CI_REF, "ref_mismatch"],
    [request.baseSha, process.env.CI_BASE_SHA, "base_sha_mismatch"],
    [request.headSha, process.env.CI_HEAD_SHA, "head_sha_mismatch"],
    [request.executionSha, process.env.CI_EXECUTION_SHA, "execution_sha_mismatch"],
    [request.workflowRunId, workflowRunId, "workflow_run_id_mismatch"],
    [request.runAttempt, runAttempt, "run_attempt_mismatch"],
    [request.pullRequestNumber, pullRequestNumber, "pull_request_number_mismatch"],
    [request.mergeGroupHeadRef, mergeGroupHeadRef, "merge_group_head_ref_mismatch"],
  ];
  if (installationId !== null) {
    expected.push([
      repository.installationId,
      installationId,
      "repository_installation_id_mismatch",
    ]);
  }
  return expected.find(([actual, wanted]) => actual !== wanted)?.[2] ?? null;
}

function validateAuthenticatedRun(authenticatedRun) {
  const keys = new Set([
    "audience",
    "checkRunId",
    "claimHash",
    "eventName",
    "executionSha",
    "issuer",
    "jobWorkflowRef",
    "jobWorkflowSha",
    "ref",
    "repository",
    "repositoryId",
    "runAttempt",
    "runId",
    "verifiedAt",
    "verifierVersion",
    "workflowRef",
    "workflowSha",
  ]);
  if (!hasExactKeys(authenticatedRun, keys)) {
    return "authenticated_run_shape_invalid";
  }
  const expected = [
    [authenticatedRun.issuer, "https://token.actions.githubusercontent.com", "auth_issuer_mismatch"],
    [authenticatedRun.audience, process.env.CI_COORDINATOR_PLAN_OIDC_AUDIENCE || "ci-coordinator", "auth_audience_mismatch"],
    [authenticatedRun.repository, `${process.env.CI_OWNER}/${process.env.CI_REPO}`, "auth_repository_mismatch"],
    [authenticatedRun.repositoryId, optionalInteger(process.env.CI_REPOSITORY_ID), "auth_repository_id_mismatch"],
    [authenticatedRun.ref, process.env.CI_REF, "auth_ref_mismatch"],
    [authenticatedRun.runId, optionalInteger(process.env.CI_RUN_ID), "auth_run_id_mismatch"],
    [authenticatedRun.runAttempt, optionalInteger(process.env.CI_RUN_ATTEMPT), "auth_run_attempt_mismatch"],
    [authenticatedRun.eventName, process.env.CI_EVENT, "auth_event_mismatch"],
    [authenticatedRun.executionSha, process.env.CI_EXECUTION_SHA, "auth_execution_sha_mismatch"],
    [authenticatedRun.workflowRef, process.env.CI_WORKFLOW_REF || null, "auth_workflow_ref_mismatch"],
    [authenticatedRun.workflowSha, process.env.CI_WORKFLOW_SHA || null, "auth_workflow_sha_mismatch"],
  ];
  return expected.find(([actual, wanted]) => actual !== wanted)?.[2] ?? null;
}

module.exports = Object.freeze({
  validateAuthenticatedRun,
  validateBinding,
  verifyEnvelope,
});
