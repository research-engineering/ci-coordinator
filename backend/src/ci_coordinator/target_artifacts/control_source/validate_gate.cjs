"use strict";

const {
  JOB_ID,
  MAX_PROFILES,
  canonicalStrings,
  compareUtf16CodeUnits,
} = require("./validation_core.cjs");
const {
  dependencyClosure,
  loadRegistry,
  loadWorkflowPath,
} = require("./validation_execution.cjs");

const JOB_RESULTS = new Set(["cancelled", "failure", "skipped", "success"]);
const MAX_GATE_INPUT_BYTES = 131_072;

function parseJsonEnvironment(name, fallback) {
  const text = process.env[name] || fallback;
  if (Buffer.byteLength(text, "utf8") > MAX_GATE_INPUT_BYTES) {
    throw new Error("gate_input_too_large");
  }
  try {
    return JSON.parse(text);
  } catch {
    throw new Error("gate_input_invalid");
  }
}

function loadStaticJobResults() {
  const value = parseJsonEnvironment("CI_STATIC_JOB_RESULTS_JSON", "");
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("static_job_results_invalid");
  }
  const jobIds = Object.keys(value).sort(compareUtf16CodeUnits);
  if (
    !canonicalStrings(jobIds, {
      maximum: MAX_PROFILES,
      predicate: (item) => JOB_ID.test(item),
    }) ||
    jobIds.some((jobId) => !JOB_RESULTS.has(value[jobId]))
  ) {
    throw new Error("static_job_results_invalid");
  }
  return { jobIds, results: value };
}

function loadSelectedJobs() {
  const value = parseJsonEnvironment("CI_SELECTED_JOBS_JSON", "[]");
  if (
    !canonicalStrings(value, {
      allowEmpty: true,
      maximum: MAX_PROFILES,
      predicate: (item) => JOB_ID.test(item),
    })
  ) {
    throw new Error("selected_jobs_invalid");
  }
  return value;
}

function main() {
  try {
    const { jobIds, results } = loadStaticJobResults();
    const workflowPath = loadWorkflowPath();
    const registry = loadRegistry(new Set(jobIds), workflowPath);
    const selectedJobs = loadSelectedJobs();
    const selected = new Set(selectedJobs);
    const selectedWithinRegistry = selectedJobs.every((jobId) => jobIds.includes(jobId));
    const selectedClosure = selectedWithinRegistry
      ? dependencyClosure(selectedJobs, registry.workflow.executionJobs)
      : [];
    const selectedIsClosed =
      selectedWithinRegistry &&
      selectedClosure.length === selectedJobs.length &&
      selectedClosure.every((jobId, index) => jobId === selectedJobs[index]);
    const fullCiResult = process.env.CI_FULL_CI_RESULT || "";
    const alternatePathInactive =
      registry.workflow.executionKind === "native-job-set"
        ? fullCiResult === ""
        : fullCiResult === "skipped";
    const selectedMode =
      process.env.CI_PLAN_RESULT === "success" &&
      process.env.CI_PLAN_VALID === "true" &&
      process.env.CI_FALLBACK === "false";
    const selectedAdmitted =
      selectedMode &&
      selected.size > 0 &&
      registry.workflow.requiredJobIds.every((jobId) => selected.has(jobId)) &&
      selectedIsClosed &&
      alternatePathInactive &&
      jobIds.every((jobId) =>
        results[jobId] === (selected.has(jobId) ? "success" : "skipped")
      );
    const fallbackAdmitted =
      !selectedMode &&
      selected.size === 0 &&
      (registry.workflow.executionKind === "native-job-set"
        ? fullCiResult === "" &&
          jobIds.every((jobId) => results[jobId] === "success")
        : fullCiResult === "success" &&
          jobIds.every((jobId) => results[jobId] === "skipped"));
    process.exitCode = selectedAdmitted || fallbackAdmitted ? 0 : 1;
  } catch {
    process.exitCode = 1;
  }
}

module.exports = Object.freeze({ main });
