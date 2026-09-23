"use strict";

const {
  IDENTIFIER,
  JOB_ID,
  MAX_PARALLEL,
  MAX_PROFILES,
  MAX_SERVICE_PROFILE_IDS,
  MAX_SHARDS,
  MAX_TESTS,
  MAX_TESTS_PER_SHARD,
  MAX_WORKFLOWS,
  canonicalStrings,
  compareUtf16CodeUnits,
  digest,
  hasExactKeys,
  readJson,
  reject,
  stableStringify,
} = require("./validation_core.cjs");
const {
  validateAuthenticatedRun,
  validateBinding,
} = require("./validation_envelope.cjs");

const EXECUTION_KINDS = new Set(["native-job-set", "witness-shards"]);
const WORKFLOW_PATH = /^\.github\/workflows\/[^/\\]+\.ya?ml$/;
const LOCAL_REQUESTER_PATH = ".github/workflows/trusted-plan-request.yml";
const LOCAL_REQUESTER_REF = `$/${LOCAL_REQUESTER_PATH}`;

function loadStaticJobIds() {
  let value;
  try {
    value = JSON.parse(process.env.CI_STATIC_JOB_IDS_JSON ?? "");
  } catch {
    reject("static_job_ids_invalid");
  }
  if (
    !canonicalStrings(value, {
      maximum: MAX_PROFILES,
      predicate: (item) => JOB_ID.test(item),
    })
  ) {
    reject("static_job_ids_invalid");
  }
  return new Set(value);
}

function loadWorkflowPath() {
  const path = process.env.CI_WORKFLOW_PATH;
  const workflowRef = process.env.CI_WORKFLOW_REF;
  const owner = process.env.CI_OWNER;
  const repository = process.env.CI_REPO;
  if (
    typeof path !== "string" ||
    !WORKFLOW_PATH.test(path) ||
    Buffer.byteLength(path, "utf8") > 256 ||
    typeof workflowRef !== "string" ||
    typeof owner !== "string" ||
    typeof repository !== "string" ||
    !workflowRef.startsWith(`${owner}/${repository}/${path}@`)
  ) {
    reject("workflow_path_binding_invalid");
  }
  return path;
}

function loadRegistry(staticJobIds, workflowPath) {
  const { text: canonical, value } = readJson(
    process.env.CI_TARGET_REGISTRY_PATH,
    131_072,
  );
  if (!canonical.endsWith("\n") || stableStringify(value) !== canonical.slice(0, -1)) {
    throw new Error("target_registry_not_canonical");
  }
  if (
    !hasExactKeys(
      value,
      new Set(["adapterFiles", "generator", "profiles", "schemaVersion", "workflows"]),
    ) ||
    !hasExactKeys(value.generator, new Set(["id", "version"])) ||
    !IDENTIFIER.test(value.generator.id) ||
    !/^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$/.test(value.generator.version) ||
    value.schemaVersion !== "dynamic-ci-target-execution-registry/v1" ||
    !validAdapterFiles(value.adapterFiles) ||
    !Array.isArray(value.workflows) ||
    value.workflows.length === 0 ||
    value.workflows.length > MAX_WORKFLOWS ||
    !Array.isArray(value.profiles) ||
    value.profiles.length === 0 ||
    value.profiles.length > MAX_PROFILES
  ) {
    throw new Error("target_registry_shape_invalid");
  }
  const workflowPaths = value.workflows.map((workflow) => workflow?.workflowPath);
  const adapterPaths = new Set(value.adapterFiles.map((item) => item.path));
  const profileIds = value.profiles.map((profile) => profile?.profileId);
  if (
    !canonicalStrings(workflowPaths, {
      maximum: MAX_WORKFLOWS,
      predicate: validWorkflowPath,
    }) ||
    !canonicalStrings(profileIds, {
      maximum: MAX_PROFILES,
      predicate: (item) => IDENTIFIER.test(item),
    }) ||
    workflowPaths.some((path) => !adapterPaths.has(path))
  ) {
    throw new Error("target_registry_identity_invalid");
  }

  const workflows = loadWorkflows(value.workflows);
  if (
    [...workflows.values()].some(
      (workflow) => workflow.planRequestWorkflowRef === LOCAL_REQUESTER_REF,
    ) && !adapterPaths.has(LOCAL_REQUESTER_PATH)
  ) {
    throw new Error("target_registry_local_requester_missing");
  }
  const profiles = loadProfiles(value.profiles, workflows);
  for (const workflow of workflows.values()) {
    const actualJobs = [...profiles.values()]
      .filter((profile) => profile.workflowPath === workflow.workflowPath)
      .map((profile) => profile.jobId)
      .sort(compareUtf16CodeUnits);
    if (
      actualJobs.length === 0 ||
      stableStringify(actualJobs) !== stableStringify(workflow.executionJobIds)
    ) {
      throw new Error("target_registry_workflow_closure_invalid");
    }
  }
  const workflow = workflows.get(workflowPath);
  if (!workflow) {
    throw new Error("target_registry_workflow_unknown");
  }
  if (
    workflow.executionJobIds.length !== staticJobIds.size ||
    workflow.executionJobIds.some((jobId) => !staticJobIds.has(jobId))
  ) {
    throw new Error("static_job_set_mismatch");
  }
  return {
    hash: digest(canonical.slice(0, -1)),
    profiles,
    workflow,
  };
}

function validAdapterFiles(values) {
  if (!Array.isArray(values) || values.length < 2 || values.length > 33) {
    return false;
  }
  const paths = values.map((item) => item?.path);
  const controlFiles = new Set([
    ".ci-coordinator/ci-coordinator.cjs",
  ]);
  return (
    canonicalStrings(paths, {
      maximum: 33,
      predicate: (path) => validWorkflowPath(path) || controlFiles.has(path),
    }) &&
    values.every(
      (item) =>
        hasExactKeys(item, new Set(["path", "sha256"])) &&
        /^[0-9a-f]{64}$/.test(item.sha256),
    ) &&
    [...controlFiles].every((path) => paths.includes(path)) &&
    paths.some((path) => validWorkflowPath(path))
  );
}

function loadWorkflows(values) {
  const keys = new Set([
    "executionJobs",
    "executionKind",
    "fallbackJobId",
    "gateJobId",
    "gateSignalName",
    "planJobId",
    "planRequestJobId",
    "planRequestWorkflowRef",
    "requiredJobIds",
    "workflowPath",
  ]);
  const workflows = new Map();
  for (const workflow of values) {
    const executionJobs = loadExecutionJobs(
      workflow?.executionJobs,
      workflow?.planJobId,
    );
    const executionJobIds = executionJobs.map((job) => job.jobId);
    const requiredJobIds = workflow?.requiredJobIds;
    const roleJobIds = [
      workflow?.planRequestJobId,
      workflow?.planJobId,
      workflow?.gateJobId,
      ...executionJobIds,
      ...(workflow?.fallbackJobId === null ? [] : [workflow?.fallbackJobId]),
    ];
    if (
      !hasExactKeys(workflow, keys) ||
      !validWorkflowPath(workflow.workflowPath) ||
      !EXECUTION_KINDS.has(workflow.executionKind) ||
      executionJobs.length === 0 ||
      !canonicalStrings(requiredJobIds, {
        allowEmpty: true,
        maximum: MAX_PROFILES,
        predicate: (item) => JOB_ID.test(item),
      }) ||
      requiredJobIds.some((jobId) => !executionJobIds.includes(jobId)) ||
      !JOB_ID.test(workflow.planRequestJobId) ||
      !validPlanRequestWorkflowRef(workflow.planRequestWorkflowRef) ||
      !JOB_ID.test(workflow.planJobId) ||
      !JOB_ID.test(workflow.gateJobId) ||
      (workflow.executionKind === "witness-shards"
        ? !JOB_ID.test(workflow.fallbackJobId)
        : workflow.fallbackJobId !== null) ||
      new Set(roleJobIds.map((jobId) => jobId.toLowerCase())).size !==
        roleJobIds.length ||
      typeof workflow.gateSignalName !== "string" ||
      workflow.gateSignalName.length === 0 ||
      Buffer.byteLength(workflow.gateSignalName, "utf8") > 256
    ) {
      throw new Error("target_registry_workflow_invalid");
    }
    workflows.set(workflow.workflowPath, {
      ...workflow,
      executionJobIds,
      executionJobs,
      requiredJobIds,
    });
  }
  return workflows;
}

function loadExecutionJobs(value, planJobId) {
  if (!Array.isArray(value) || value.length === 0 || value.length > MAX_PROFILES) {
    throw new Error("target_registry_execution_jobs_invalid");
  }
  const keys = new Set(["jobId", "needs"]);
  const jobIds = value.map((job) => job?.jobId);
  if (
    !canonicalStrings(jobIds, {
      maximum: MAX_PROFILES,
      predicate: (item) => JOB_ID.test(item),
    })
  ) {
    throw new Error("target_registry_execution_jobs_invalid");
  }
  const known = new Set(jobIds);
  const dependencies = new Map();
  for (const job of value) {
    if (
      !hasExactKeys(job, keys) ||
      !canonicalStrings(job.needs, {
        allowEmpty: true,
        maximum: MAX_PROFILES,
        predicate: (item) => JOB_ID.test(item),
      }) ||
      job.needs.includes(job.jobId) ||
      !job.needs.includes(planJobId) ||
      job.needs.some(
        (dependency) => dependency !== planJobId && !known.has(dependency),
      )
    ) {
      throw new Error("target_registry_execution_jobs_invalid");
    }
    dependencies.set(
      job.jobId,
      job.needs.filter((dependency) => dependency !== planJobId),
    );
  }
  requireAcyclicJobs(dependencies);
  return value;
}

function requireAcyclicJobs(dependencies) {
  const visiting = new Set();
  const visited = new Set();
  function visit(jobId) {
    if (visiting.has(jobId)) {
      throw new Error("target_registry_execution_cycle");
    }
    if (visited.has(jobId)) {
      return;
    }
    visiting.add(jobId);
    dependencies.get(jobId).forEach(visit);
    visiting.delete(jobId);
    visited.add(jobId);
  }
  dependencies.forEach((_needs, jobId) => visit(jobId));
}

function loadProfiles(values, workflows) {
  const keys = new Set([
    "capacityClassId",
    "credentialProfileId",
    "executionKind",
    "fixtureProfileId",
    "jobId",
    "permissionProfileId",
    "profileId",
    "runnerProfileId",
    "serviceProfileIds",
    "workflowPath",
  ]);
  const profiles = new Map();
  const jobs = new Set();
  for (const profile of values) {
    const workflow = workflows.get(profile?.workflowPath);
    const jobKey = `${profile?.workflowPath}\0${profile?.jobId}`;
    if (
      !hasExactKeys(profile, keys) ||
      !workflow ||
      profile.executionKind !== workflow.executionKind ||
      !JOB_ID.test(profile.jobId) ||
      jobs.has(jobKey) ||
      ![
        profile.profileId,
        profile.runnerProfileId,
        profile.permissionProfileId,
        profile.credentialProfileId,
        profile.fixtureProfileId,
        profile.capacityClassId,
      ].every((item) => typeof item === "string" && IDENTIFIER.test(item)) ||
      !canonicalStrings(profile.serviceProfileIds, {
        allowEmpty: true,
        maximum: MAX_SERVICE_PROFILE_IDS,
        predicate: (item) => IDENTIFIER.test(item),
      })
    ) {
      throw new Error("target_registry_profile_invalid");
    }
    profiles.set(profile.profileId, profile);
    jobs.add(jobKey);
  }
  return profiles;
}

function validWorkflowPath(value) {
  return (
    typeof value === "string" &&
    WORKFLOW_PATH.test(value) &&
    Buffer.byteLength(value, "utf8") <= 256
  );
}

function validPlanRequestWorkflowRef(value) {
  if (value === LOCAL_REQUESTER_REF) return true;
  return (
    typeof value === "string" &&
    Buffer.byteLength(value, "utf8") <= 512 &&
    /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\/[A-Za-z0-9_.-]{1,100}\/\.github\/workflows\/[^/\\]+\.ya?ml@[0-9a-f]{40}$/.test(
      value,
    )
  );
}

function validateProfileBinding(profile, binding) {
  const keys = new Set([
    "capacityClassId",
    "credentialProfileId",
    "fixtureProfileId",
    "permissionProfileId",
    "profileId",
    "runnerProfileId",
    "serviceProfileIds",
    "shardingPolicy",
  ]);
  if (!hasExactKeys(profile, keys) || profile.profileId !== binding.profileId) {
    return false;
  }
  return [
    "runnerProfileId",
    "permissionProfileId",
    "credentialProfileId",
    "fixtureProfileId",
    "capacityClassId",
  ].every((key) => profile[key] === binding[key]) &&
    canonicalStrings(profile.serviceProfileIds, {
      allowEmpty: true,
      maximum: MAX_SERVICE_PROFILE_IDS,
      predicate: (item) => IDENTIFIER.test(item),
    }) &&
    JSON.stringify(profile.serviceProfileIds) === JSON.stringify(binding.serviceProfileIds);
}

function validateShardingPolicy(policy) {
  const keys = new Set([
    "cpuWeight",
    "maxItemsPerShard",
    "maxParallel",
    "maxShards",
    "operatorWeight",
    "setupSecondsPerShard",
    "wallWeight",
  ]);
  if (!hasExactKeys(policy, keys)) {
    return false;
  }
  const integers = [policy.maxItemsPerShard, policy.maxParallel, policy.maxShards];
  const weights = [policy.cpuWeight, policy.operatorWeight, policy.wallWeight];
  return (
    integers.every((value) => Number.isSafeInteger(value) && value >= 1) &&
    policy.maxItemsPerShard <= MAX_TESTS_PER_SHARD &&
    policy.maxParallel <= MAX_PARALLEL &&
    policy.maxShards <= MAX_SHARDS &&
    policy.maxParallel <= policy.maxShards &&
    weights.every(
      (value) =>
        typeof value === "number" &&
        Number.isFinite(value) &&
        value >= 0 &&
        value <= 1_000,
    ) &&
    typeof policy.setupSecondsPerShard === "number" &&
    Number.isFinite(policy.setupSecondsPerShard) &&
    policy.setupSecondsPerShard >= 0 &&
    policy.setupSecondsPerShard <= 3_600 &&
    policy.cpuWeight + policy.operatorWeight + policy.wallWeight > 0
  );
}

function deriveProviderSignal(profileId, shardId) {
  const hash = digest(
    stableStringify({
      schemaVersion: "provider-signal/v1",
      executionProfileId: profileId,
      shardId,
    }),
  );
  return {
    signalId: `provider_signal_${hash.slice(0, 32)}`,
    jobName: `ci/${profileId}/${hash.slice(0, 20)}`,
    kind: "derived-shard",
    executionProfileId: profileId,
    shardId,
    workflowPath: null,
    jobId: null,
  };
}

function declaredProviderSignal(workflow) {
  const projection = {
    schemaVersion: "declared-native-provider-signal/v1",
    workflowPath: workflow.workflowPath,
    jobId: workflow.gateJobId,
    jobName: workflow.gateSignalName,
  };
  const hash = digest(stableStringify(projection));
  return {
    signalId: `provider_signal_${hash.slice(0, 32)}`,
    jobName: workflow.gateSignalName,
    kind: "declared-native",
    executionProfileId: null,
    shardId: null,
    workflowPath: workflow.workflowPath,
    jobId: workflow.gateJobId,
  };
}

function selectedOutputs(execution, registry) {
  const keys = new Set([
    "catalogHash",
    "deterministicPlanId",
    "executionKind",
    "gateProviderSignal",
    "mode",
    "omittedObligationIds",
    "profiles",
    "selectedObligationIds",
    "selectedWitnessIds",
    "targetRegistryHash",
    "testManifestId",
    "verifiedPlanId",
    "workflowPath",
  ]);
  if (!hasExactKeys(execution, keys) || execution.mode !== "selected") {
    return { reason: "selected_execution_shape_invalid" };
  }
  if (
    execution.executionKind !== registry.workflow.executionKind ||
    execution.workflowPath !== registry.workflow.workflowPath ||
    stableStringify(execution.gateProviderSignal) !==
      stableStringify(declaredProviderSignal(registry.workflow)) ||
    !/^[0-9a-f]{64}$/.test(execution.catalogHash) ||
    execution.targetRegistryHash !== registry.hash ||
    typeof execution.deterministicPlanId !== "string" ||
    execution.deterministicPlanId.length === 0 ||
    typeof execution.verifiedPlanId !== "string" ||
    execution.verifiedPlanId.length === 0 ||
    !canonicalStrings(execution.selectedObligationIds, {
      predicate: (item) => IDENTIFIER.test(item),
    }) ||
    !canonicalStrings(execution.omittedObligationIds, {
      allowEmpty: true,
      predicate: (item) => IDENTIFIER.test(item),
    }) ||
    !canonicalStrings(execution.selectedWitnessIds, {
      predicate: (item) => IDENTIFIER.test(item),
    }) ||
    execution.selectedObligationIds.some((id) => execution.omittedObligationIds.includes(id)) ||
    !Array.isArray(execution.profiles) ||
    execution.profiles.length === 0 ||
    execution.profiles.length > MAX_PROFILES ||
    !canonicalStrings(
      execution.profiles.map((profile) => profile?.profile?.profileId),
      {
        maximum: MAX_PROFILES,
        predicate: (item) => IDENTIFIER.test(item),
      },
    )
  ) {
    return { reason: "selected_execution_identity_invalid" };
  }
  if (execution.executionKind === "native-job-set") {
    return nativeOutputs(execution, registry);
  }
  if (
    execution.executionKind !== "witness-shards" ||
    !/^test_manifest_[0-9a-f]{32}$/.test(execution.testManifestId)
  ) {
    return { reason: "selected_execution_identity_invalid" };
  }
  return shardOutputs(execution, registry);
}

function nativeOutputs(execution, registry) {
  if (execution.testManifestId !== null) {
    return { reason: "native_execution_manifest_invalid" };
  }
  const keys = new Set(["executionKind", "jobId", "profile", "witnessIds"]);
  const selectedJobs = [];
  const coveredWitnessIds = new Set();
  for (const signedProfile of execution.profiles) {
    const profileId = signedProfile?.profile?.profileId;
    const binding = registry.profiles.get(profileId);
    if (
      !hasExactKeys(signedProfile, keys) ||
      signedProfile.executionKind !== "native-job-set" ||
      !binding ||
      binding.workflowPath !== registry.workflow.workflowPath ||
      binding.executionKind !== "native-job-set" ||
      signedProfile.jobId !== binding.jobId ||
      !validateProfileBinding(signedProfile.profile, binding) ||
      !validateShardingPolicy(signedProfile.profile.shardingPolicy) ||
      !canonicalStrings(signedProfile.witnessIds, {
        predicate: (item) => IDENTIFIER.test(item),
      }) ||
      signedProfile.witnessIds.some((witnessId) => coveredWitnessIds.has(witnessId))
    ) {
      return { reason: "native_profile_execution_invalid" };
    }
    signedProfile.witnessIds.forEach((witnessId) => coveredWitnessIds.add(witnessId));
    selectedJobs.push(binding.jobId);
  }
  const rootJobIds = [...selectedJobs].sort(compareUtf16CodeUnits);
  const executableJobIds = dependencyClosure(
    rootJobIds,
    registry.workflow.executionJobs,
  );
  if (
    registry.workflow.requiredJobIds.some(
      (jobId) => !executableJobIds.includes(jobId),
    )
  ) {
    return { reason: "required_target_job_missing" };
  }
  if (
    rootJobIds.length !== new Set(rootJobIds).size ||
    execution.selectedWitnessIds.length !== coveredWitnessIds.size ||
    execution.selectedWitnessIds.some((id) => !coveredWitnessIds.has(id))
  ) {
    return { reason: "selected_witness_coverage_invalid" };
  }
  return {
    matrices: {},
    maxParallelByJob: {},
    selectedJobs: executableJobIds,
  };
}

function dependencyClosure(rootJobIds, executionJobs) {
  const known = new Set(executionJobs.map((job) => job.jobId));
  const dependencies = new Map(
    executionJobs.map((job) => [
      job.jobId,
      job.needs.filter((dependency) => known.has(dependency)),
    ]),
  );
  const selected = new Set(rootJobIds);
  const pending = [...rootJobIds];
  while (pending.length > 0) {
    const jobId = pending.pop();
    for (const dependency of dependencies.get(jobId)) {
      if (!selected.has(dependency)) {
        selected.add(dependency);
        pending.push(dependency);
      }
    }
  }
  return [...selected].sort(compareUtf16CodeUnits);
}

function shardOutputs(execution, registry) {
  const matricesByJob = new Map();
  const maxParallelByJob = new Map();
  const seenShardIds = new Set();
  const seenTestIds = new Set();
  const coveredWitnessIds = new Set();
  const profileKeys = new Set([
    "capacityMode",
    "capacityReason",
    "executionKind",
    "maxParallel",
    "profile",
    "shards",
  ]);
  const shardKeys = new Set([
    "executionProfileId",
    "manifestId",
    "providerSignal",
    "shardId",
    "testIds",
    "witnessIds",
  ]);
  for (const signedProfile of execution.profiles) {
    const profileId = signedProfile?.profile?.profileId;
    const binding = registry.profiles.get(profileId);
    if (
      !hasExactKeys(signedProfile, profileKeys) ||
      signedProfile.executionKind !== "witness-shards" ||
      !binding ||
      binding.workflowPath !== registry.workflow.workflowPath ||
      binding.executionKind !== "witness-shards" ||
      !validateProfileBinding(signedProfile.profile, binding) ||
      !validateShardingPolicy(signedProfile.profile.shardingPolicy) ||
      !Array.isArray(signedProfile.shards) ||
      !canonicalStrings(
        signedProfile.shards.map((shard) => shard?.shardId),
        {
          maximum: signedProfile.profile.shardingPolicy.maxShards,
          predicate: (item) => /^ci_shard_[0-9a-f]{32}$/.test(item),
        },
      ) ||
      !Number.isSafeInteger(signedProfile.maxParallel) ||
      signedProfile.maxParallel < 1 ||
      signedProfile.maxParallel > Math.min(signedProfile.shards.length, MAX_PARALLEL) ||
      signedProfile.maxParallel > signedProfile.profile.shardingPolicy.maxParallel ||
      !["optimized", "conservative"].includes(signedProfile.capacityMode) ||
      (signedProfile.capacityMode === "optimized" && signedProfile.capacityReason !== null) ||
      (signedProfile.capacityMode === "conservative" &&
        (typeof signedProfile.capacityReason !== "string" ||
          signedProfile.capacityReason.length === 0 ||
          signedProfile.capacityReason.length > 200))
    ) {
      return { reason: "profile_execution_invalid" };
    }

    const include = [];
    for (const shard of signedProfile.shards) {
      if (
        !hasExactKeys(shard, shardKeys) ||
        !/^ci_shard_[0-9a-f]{32}$/.test(shard.shardId) ||
        seenShardIds.has(shard.shardId) ||
        shard.manifestId !== execution.testManifestId ||
        shard.executionProfileId !== profileId ||
        !canonicalStrings(shard.witnessIds, {
          predicate: (item) => IDENTIFIER.test(item),
        }) ||
        !canonicalStrings(shard.testIds, {
          maximum: signedProfile.profile.shardingPolicy.maxItemsPerShard,
          predicate: (item) =>
            item.length <= 256 && /^[\x20-\x7E]+$/.test(item),
        })
      ) {
        return { reason: "execution_shard_invalid" };
      }
      const expectedSignal = deriveProviderSignal(profileId, shard.shardId);
      if (stableStringify(shard.providerSignal) !== stableStringify(expectedSignal)) {
        return { reason: "provider_signal_invalid" };
      }
      if (shard.testIds.some((testId) => seenTestIds.has(testId))) {
        return { reason: "execution_test_identity_duplicate" };
      }
      seenShardIds.add(shard.shardId);
      shard.testIds.forEach((testId) => seenTestIds.add(testId));
      if (seenTestIds.size > MAX_TESTS) {
        return { reason: "execution_test_count_exceeded" };
      }
      shard.witnessIds.forEach((witnessId) => coveredWitnessIds.add(witnessId));
      include.push({
        provider_job_name: expectedSignal.jobName,
        shard_json: JSON.stringify({
          schemaVersion: "dynamic-ci-shard/v1",
          shardId: shard.shardId,
          manifestId: shard.manifestId,
          executionProfileId: shard.executionProfileId,
          witnessIds: shard.witnessIds,
          testIds: shard.testIds,
          providerSignal: expectedSignal,
        }),
      });
    }
    matricesByJob.set(binding.jobId, { include });
    maxParallelByJob.set(binding.jobId, signedProfile.maxParallel);
  }
  if (
    execution.selectedWitnessIds.length !== coveredWitnessIds.size ||
    execution.selectedWitnessIds.some((id) => !coveredWitnessIds.has(id))
  ) {
    return { reason: "selected_witness_coverage_invalid" };
  }
  const selectedJobs = [...matricesByJob].map(([jobId]) => jobId).sort(compareUtf16CodeUnits);
  const selectedClosure = dependencyClosure(
    selectedJobs,
    registry.workflow.executionJobs,
  );
  if (
    selectedClosure.length !== selectedJobs.length ||
    selectedClosure.some((jobId, index) => jobId !== selectedJobs[index])
  ) {
    return { reason: "selected_job_dependency_missing" };
  }
  if (
    registry.workflow.requiredJobIds.some(
      (jobId) => !selectedJobs.includes(jobId),
    )
  ) {
    return { reason: "required_target_job_missing" };
  }
  return {
    matrices: Object.fromEntries(selectedJobs.map((jobId) => [jobId, matricesByJob.get(jobId)])),
    maxParallelByJob: Object.fromEntries(
      selectedJobs.map((jobId) => [jobId, maxParallelByJob.get(jobId)]),
    ),
    selectedJobs,
  };
}

function validatePayload(payload, registry) {
  const bindingError = validateBinding(payload);
  if (bindingError) {
    return { reason: bindingError };
  }
  const authError = validateAuthenticatedRun(payload.authenticatedRun);
  if (authError) {
    return { reason: authError };
  }
  const authenticatedRun = payload.authenticatedRun;
  let expectedRequesterRef = registry.workflow.planRequestWorkflowRef;
  let expectedRequesterSha = expectedRequesterRef.slice(
    expectedRequesterRef.lastIndexOf("@") + 1,
  );
  if (expectedRequesterRef === LOCAL_REQUESTER_REF) {
    const callerPrefix = `${authenticatedRun.repository}/${registry.workflow.workflowPath}@`;
    const callerRef = authenticatedRun.workflowRef;
    if (typeof callerRef !== "string" || !callerRef.startsWith(callerPrefix)) {
      return { reason: "auth_job_workflow_ref_mismatch" };
    }
    const revisionRef = callerRef.slice(callerPrefix.length);
    if (!revisionRef.startsWith("refs/") || revisionRef.length <= 5) {
      return { reason: "auth_job_workflow_ref_mismatch" };
    }
    expectedRequesterRef = `${authenticatedRun.repository}/${LOCAL_REQUESTER_PATH}@${revisionRef}`;
    expectedRequesterSha = authenticatedRun.workflowSha;
    if (
      typeof expectedRequesterSha !== "string" ||
      !/^[0-9a-f]{40}$/.test(expectedRequesterSha)
    ) {
      return { reason: "auth_job_workflow_sha_mismatch" };
    }
  }
  if (authenticatedRun.jobWorkflowRef !== expectedRequesterRef) {
    return { reason: "auth_job_workflow_ref_mismatch" };
  }
  if (authenticatedRun.jobWorkflowSha !== expectedRequesterSha) {
    return { reason: "auth_job_workflow_sha_mismatch" };
  }
  if (
    payload.verifiedPlanId === null &&
    payload.productionAdmissionReceiptId === null &&
    payload.verifierVersion === null &&
    typeof payload.fallbackReason === "string" &&
    hasExactKeys(payload.execution, new Set(["mode", "reason"])) &&
    payload.execution.mode === "full-ci" &&
    payload.execution.reason === payload.fallbackReason
  ) {
    return { fallback: true, reason: payload.fallbackReason };
  }
  if (
    typeof payload.planId !== "string" ||
    payload.planId !== payload.verifiedPlanId ||
    !/^production_admission_[0-9a-f]{32}$/.test(payload.productionAdmissionReceiptId) ||
    typeof payload.verifierVersion !== "string" ||
    payload.verifierVersion.length === 0 ||
    payload.fallbackReason !== null ||
    payload.execution?.verifiedPlanId !== payload.verifiedPlanId
  ) {
    return { reason: "signed_plan_state_inconsistent" };
  }
  return selectedOutputs(payload.execution, registry);
}

module.exports = Object.freeze({
  dependencyClosure,
  loadRegistry,
  loadStaticJobIds,
  loadWorkflowPath,
  validatePayload,
});
