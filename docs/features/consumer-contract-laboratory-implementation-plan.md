# Consumer Contract Laboratory Implementation Plan

Status: active

Date: 2026-07-30

Owner requirement: `REQ-CI-RUNTIME-028`

Design authority:
[consumer-contract-laboratory.md](consumer-contract-laboratory.md)

## 1. Delivery Predicate

```text
Complete :=
  ContractsPublished
  and StrictAdmissionImplemented
  and SourceEpochClosed
  and RealApplicationCompositionUsed
  and ExactTargetControlsExecuted
  and IndependentScenariosVerified
  and ProofkitBindingsComplete
  and RepositoryQualityGreen
  and IndependentReviewClosed
```

The implementation is not complete merely because the CLI returns zero on one
fixture.

## 2. File Plan

### Step 1: contracts and routing

- Add `REQ-CI-RUNTIME-028`.
- Add the design and this plan to documentation routing.
- Update the roadmap without changing production-readiness claims.
- Add structural JSON Schema preflight for the external profile and scenario
  corpus while keeping the runtime codec as exact admission authority.

Exit: every new normative path has one owner and one non-claim boundary.

### Step 2: strict target-owned inputs

- `consumer_contract_lab/model.py`: exact immutable values only.
- `consumer_contract_lab/codec.py`: bounded strict JSON admission.
- `consumer_contract_lab/bootstrap.py`: bounded exact-commit package
  materialization and isolated child launch.
- `consumer_contract_lab/source_epoch.py`: loaded image identity, sealed target
  bytes, relevant-byte manifest, lock and adapter closure.
- `consumer_contract_lab/execution_image.py`: private target image created only
  from sealed bytes.

Exit: malformed or mixed epochs fail before planning or Node execution.

### Step 3: real application composition

- `consumer_contract_lab/composition.py`: admitted epoch, repository context,
  selected-only native capacity inputs and in-memory reconciliation,
  source-epoch-derived non-persisted lab authority, `DynamicPlanService`, and
  `SignedPlanIssuer`.

Exit: no copied planning, verification, capacity, reconciliation-contract, or
issuance algorithm exists in the laboratory.

### Step 4: exact consumer controls

- `consumer_contract_lab/node_runtime.py`: exact Node.js runtime admission,
  bounded chunk projection, generated plan consumer, plan validator, and
  aggregate gate invocation.
- `consumer_contract_lab/process.py`: aggregate-output and wall-time bounded
  execution with process-group identity held until cleanup and reap.
- `consumer_contract_lab/resources/runtime-harness.cjs`: deterministic clock
  projection for generated validators without provider emulation.
- `consumer_contract_lab/resources/node-runtime-guard.cjs`: same-process
  admission of exact Node.js `24.18.1` before target control code loads.
- `consumer_contract_lab/runner.py`: scenario orchestration and receipt.
- `consumer_contract_lab/cli.py`: stable command boundary.

Exit: each scenario traverses the complete composed path and labels job results
as synthetic.

### Step 5: falsifiers and Proofkit

- Add unit and integration-style tests for valid selected/fallback routes,
  every profile event, stale epochs, unchecked bytecode, descriptor and Git
  epoch races, non-blocking special-file rejection, complete opaque byte
  bindings, sealed-image execution, same-process Node identity, process
  authority, digest mutation, corpus mismatch, and expected-outcome mismatch.
- Add one parameterized finite-language admission suite for model bounds,
  strict codecs, Git and archive metadata, bootstrap classification, and
  bounded-process requests.
- Collect branch coverage from the bootstrap and isolated source image through
  Coverage.py subprocess support. Map only the target-copy and exact-image path
  shapes owned by this laboratory back to canonical package sources; do not
  admit a generic temporary-source alias.
- Add import-boundary enforcement for the lab composition.
- Add `runtime028.test`, command catalog, requirement bindings, and required
  tuples.

Exit: every design falsifier has an executable witness or a declared deferred
  provider obligation.

### Step 6: target adoption

For a separately admitted pilot target:

- regenerate artifacts from the merged coordinator revision;
- add `.ci-coordinator/local-lab-profile.v1.json`;
- add an independently authored scenario corpus;
- add one stable CLI-based test;
- run all target deterministic gates;
- commit one coherent exact source epoch.

Exit: local receipt covers pilot target selected and fallback routes for every adopted
event.

### Step 7: one provider canary

- Push the exact reviewed pilot target head.
- Run one GitHub canary after all local evidence is green.
- Admit a separate canary execution profile and receipt binding the exact
  `localReceiptId`, scenario and diff/event digests, repository/head/workflow
  identities, run ID/attempt, observation interval, explicit REST version, and
  the relevant runner, image, action, container, event-policy, and OIDC facts.
- Record an unobservable rollout cohort as `unknown` and bound every conclusion
  to the one exact observed run.
- Record wall time and aggregate runner occupancy against the frozen baseline.
- Preserve FullCI fallback and avoid production omission claims.

Exit: provider evidence is tied to one exact head and one versioned provider
profile. Any mismatch returns to local diagnosis rather than repeated blind
provider attempts.

## 3. Review Questions

Each changed file must answer:

1. Which single authority does this file own?
2. Could the responsibility be expressed by an existing type or service?
3. Does a dependency direction follow the owner boundary?
4. Is every input finite, canonical, and source-epoch bound?
5. Can unknown evidence become selected success?
6. Is the oracle independent from the implementation under test?
7. Does a local claim accidentally imply provider or production truth?
8. Is a new abstraction necessary for at least two real interactions, a trust
   boundary, or a materially simpler proof?

Unknown answers block completion; metrics alone only select decomposition
review.

## 4. Rollback

The laboratory is additive and has no production import path. Rollback removes
its CLI, package, docs, and Proofkit bindings. Target repositories retain their
generated FullCI fallback controls.
