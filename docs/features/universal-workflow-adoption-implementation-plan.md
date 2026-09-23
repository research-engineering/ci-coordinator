# Universal Workflow Adoption Implementation Plan

Status: accepted for implementation

Date: 2026-07-29

Design: [Universal Workflow Adoption](universal-workflow-adoption.md)

## 1. Objective

Replace the bootstrap-only selected-execution assumption with a workflow-scoped
contract supporting both generated witness shards and repository-native static
job sets, without adding executable plan data or weakening FullCI fallback.

## 2. Requirement Slice

Primary requirement:

- `REQ-CI-RUNTIME-026`

Existing requirements retained and impacted:

- `REQ-CI-RUNTIME-007`: bootstrap fallback and gate behavior;
- `REQ-CI-RUNTIME-012`: target artifact compilation and validation resources;
- `REQ-CI-RUNTIME-013`: exact provider workflow/job inventory;
- `REQ-CI-RUNTIME-019`: exact-snapshot discovery and non-authoritative
  proposals.

## 3. Implementation Sequence

### Stage A: Registry and signal owners

1. Add exact execution-kind and workflow-binding models.
2. Extend target registry admission and canonical identity with one exact plan
   request job, one plan job depending exactly on that request, an
   execution-kind-specific fallback identity, and one acyclic, closed
   intra-execution dependency graph.
3. Require exact equality between registry and validation-catalog profile sets
   so dependency closure cannot broaden configured authority.
4. Add content-addressed declared native provider signals.
5. Add negative model tests for all cross-kind and cross-workflow combinations.

Completion witness:

```text
one workflow binding
<-> exact execution job set
<-> exact profile subset
<-> one aggregate provider signal

ids(registry.profiles)
= ids(validationCatalog.executionProfiles)
```

### Stage B: Artifact source and codecs

1. Extend the source and registry schemas.
2. Extend Python source and registry codecs.
3. Update deterministic renderer and fixture source.
4. Prove schema/codec/rendered-byte round trips.
5. Preserve atomic publication and check-mode non-mutation.

### Stage C: Trusted execution projection

1. Represent target authority independently from execution readiness.
2. Preserve the exact workflow gate when manifest or capacity evidence is
   unavailable.
3. Carry the exact trusted registry with every admitted target projection.
4. Partition selected profiles by execution kind.
5. Keep shard matrices only for `witness-shards`.
6. Emit native profile/job selections only for `native-job-set`.
7. Expand native selections to their registry-bound transitive dependency
   closure at the target boundary.
8. Use selected shard signals only for authorized selected execution and use
   the exact workflow gate for FullCI reconciliation.
9. Register no reconciliation subject when target authority is unknown.
10. Keep production-admission subject hashes bound to the registry digest.
11. Skip provider target and capacity projection when neither production
    admission nor durable reconciliation can consume its result.

### Stage D: Target-side validation

1. Remove the static bootstrap-path constant.
2. admit one bounded `CI_WORKFLOW_PATH`;
3. bind it to `authenticatedRun.workflowRef`;
4. select the exact registry workflow slice;
5. validate the exact current static job set;
6. validate kind-specific signed execution;
7. expand native roots to the exact acyclic dependency closure;
8. keep output budget and fail-safe behavior.

### Stage E: Provider inventory

1. Require every workflow binding to exist and be active.
2. Require the complete top-level workflow job set to equal the registered
   plan request, plan, execution, kind-specific fallback, and gate roles.
3. Compare every complete registered `needs` set with the workflow source at
   that revision, including the plan's exact request dependency and exact gate
   dependencies.
4. For witness shards, require one exact static fallback job with only the plan
   dependency and include it in the gate's direct dependencies; for native
   jobs, reject a separate fallback identity.
5. Reject job-role IDs that collide under GitHub's case-insensitive expression
   comparison.
6. Reject incomplete, duplicate, inactive, malformed, or foreign workflow
   evidence.
7. Add a second workflow-path fixture proving the implementation is not
   bootstrap-specific.

### Stage F: Total adoption assessment

1. Add a small workflow-adoption domain package.
2. Produce exactly one assessment per source path.
3. Require explicit target-owner policy before a selectable adapter is
   recommended.
4. Preserve all discovery unknowns and blockers.
5. Expose assessments through the existing discovery API only after its
   OpenAPI and frontend runtime schemas are updated in the same slice.

### Stage G: Native workflow fixture

1. Generate one registry-bound gate validator shared by both execution kinds.
2. Prove its selected and full route truth tables against sharded and native
   registry slices.
3. For native fallback, require every registered static job to succeed and
   reject any separate FullCI result; for witness shards, require every shard
   job to remain skipped and the one registry-bound FullCI job to succeed.
4. Prove native dependency closure, selected failure, selected skip,
   unexpected execution, plan failure,
   cancellation, concurrent FullCI, stale selection, malformed results, and
   failed fallback fail safely.
5. Complete the native integration in pilot target as the external pilot.
6. Do not copy pilot target commands, secrets, runner policy, or workflow semantics
   into the coordinator fixture.
7. Retain a separately triggered local Native Full Check in each adoption
   fixture and prove that it has no remote reusable-workflow dependency.

### Stage H: Documentation and adoption

1. Update execution identity and orchestration specifications.
2. Update dynamic enforcement and repository adoption guidance.
3. Document the target semantic projection contract.
4. Document local-only, service-configured, and provider-enforced states.
5. Add pilot target as an external pilot, not as product logic.
6. Keep the native Full Check required until live provider evidence admits
   cutover; required-check configuration remains provider-admin authority.

## 4. Proofkit Protocol

Before implementation:

1. admit the requirement source;
2. bind every proof-like path to the exact requirement;
3. run a selective plan with every changed path;
4. reject unknown edges.

During implementation:

- close one owner stage and its focused witnesses before the next stage;
- use mutation or negative fixtures for each fail-safe predicate;
- keep JSON admission, native test execution, selective routing, provider CI,
  and production readiness as separate evidence classes.

Closeout:

- run full Python, package, workflow, target-artifact, requirement, text,
  Proofkit, and documentation-graph gates;
- freeze the final range;
- run an independent repair closeout;
- state provider/deployment non-claims explicitly.

## 5. Acceptance Commands

The selective Proofkit plan determines the exact command list. The minimum
expected owner commands are:

```bash
mise run check:portable
mise run check
CI=true PROOFKIT_BASE_REF="$BASE_SHA" PROOFKIT_HEAD_REF="$HEAD_SHA" \
  mise exec -- backend/.venv/bin/python -m scripts.proofkit_branch_head_quality
```

Every command must run on the final tracked branch head before merge-readiness
is claimed.

## 6. Rollback

The implementation preserves `witness-shards` as an explicit kind. If native
job-set adoption is not admitted, registries remain shard-only and target
workflows continue to use FullCI. No database or provider rollback is required
to disable native selection; repository policy forces FullCI.

## 7. Non-Claims

This plan does not claim pilot target installation, a deployed coordinator, live
GitHub provider behavior, production omission authority, or performance
savings. Those require the target repository and live pilot evidence.
