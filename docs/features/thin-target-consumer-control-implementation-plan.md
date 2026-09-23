# Thin Target Consumer Control Implementation Plan

Status: accepted implementation plan
Last updated: 2026-09-05
Design: [Thin Target Consumer Control](thin-target-consumer-control.md)

## 1. Objective

Replace the six-file target control representation with one reproducibly built
local bundle while preserving every observable plan-consumption,
plan-validation, fallback, and aggregate-gate behavior.

The implementation is complete only when:

```text
BehaviorParity
and ExactSourceToBundleReproducibility
and ExactSourceToMetafileDependencyClosure
and OneControlPathPerRegistry
and ExactRevisionAdmission
and PackageResourceParity
and TargetScenarioParity
and FullCheckGreenAtExactHead
```

## 2. Scope And Protected Observations

In scope:

- decomposed CommonJS source and one generated CommonJS projection;
- exact esbuild dependency and deterministic build checker;
- target artifact models, rendering, registry, and bounds;
- generated target fixtures and literal workflow commands;
- consumer contract laboratory execution;
- package-resource, schema, requirement, Proofkit, and documentation surfaces;
- tests and mutation manifests affected by the representation change.

Protected observations:

- all existing selected and FullCI fallback outcomes;
- signed-envelope, request, authenticated-run, target-registry, and static-job
  validation;
- exact Node.js 24.20.0 admission and the current permission model;
- no OIDC or provider credential in target-controlled commands;
- stable aggregate gate name and semantics;
- bounded plan transport and GitHub output;
- no runtime package installation or network dependency; and
- no change to configured tests, shards, credentials, fixtures, or services.

Out of scope:

- changing target workflow topology or selected-check policy;
- automatic migration of unsupported pre-release consumer branches;
- GitHub App permission changes;
- pilot target profile construction or provider canary execution; and
- UI or API changes.

## 3. Writer Readiness

| Owner                     | Intended delta                                                           | Derived surfaces                           | Whole-chain gate                        | Independent validator               |
|---------------------------|--------------------------------------------------------------------------|--------------------------------------------|-----------------------------------------|-------------------------------------|
| `target_artifacts`        | Six distributed controls become one generated bundle                     | fixtures, wheel resources, registry schema | bundle check plus target-artifact check | exact-head target-control scenarios |
| `execution_orchestration` | Fixed control set and adapter bounds become one path, 33 files, 39 reads | provider snapshot and registry admission   | registry and provider tests             | consumer contract laboratory        |
| `repo_context`            | Three literal commands use one path and distinct subcommands             | workflow projection hashes                 | workflow static admission               | workflow fixture tests              |
| `consumer_contract_lab`   | Execute the same bundle three times with explicit subcommands            | scenario receipts                          | lab scenario corpus                     | exact-head GitHub CI                |
| Proof governance          | Bind source, generated projection, and checker                           | routes and required tuples                 | Proofkit admission and selective plan   | repository quality job              |

No owner may weaken fallback or selected-execution admission to make the new
representation pass.

## 4. Dependency-Ordered Implementation

### Step 1: Freeze The Representation Contract

1. Add the current design and this plan.
2. Update the living bootstrap, execution-identity, and
   execution-orchestration contracts to name one generated bundle.
3. Update `REQ-CI-RUNTIME-007`, `REQ-CI-RUNTIME-012`,
   `REQ-CI-RUNTIME-026`, and `REQ-CI-RUNTIME-027` without changing their
   business semantics.
4. Record exact bounds: one control file, 33 adapter files, and 39 cold reads.

Exit condition: every owner describes one identical target representation.

### Step 2: Add The Deterministic Build Boundary

1. Pin exact `esbuild` in the root development dependency graph.
2. Add `scripts.target_control_bundle` with `write` and `check` commands.
3. Define the closed source inventory, exact build options, output bounds,
   timeout, source identity, generated header, and exact-version admission.
4. Add an independently owned tree-sitter admission for the closed CommonJS
   module-loading grammar and derive the exact lexical dependency graph.
5. Reject parse errors, ESM imports and exports, dynamic `import()`, non-literal
   or indirect `require`, alternate loaders, and unadmitted ambient loader
   identities before invoking esbuild.
6. Admit the exact esbuild metafile, complete source reachability, an acyclic
   internal import graph, a closed set of external `node:` imports, and exact
   equality with the independently parsed graph.
7. Run the build in an owned temporary directory and publish atomically only
   in `write` mode.
8. Add static falsifiers for stale output, wrong version, source-set drift,
   malformed header, process failure, output overflow, and every rejected
   module-loading form. At least one dynamic-loader falsifier must invoke the
   real pinned esbuild to prove why metafile-only admission is insufficient.

Exit condition: identical inputs rebuild byte-identical output and every input
change causes `check` to fail until `write` regenerates it.

### Step 3: Separate Source From Projection

1. Move the current six modules into `target_artifacts/control_source` using
   capability-oriented names.
2. Export exact command functions instead of running them during import.
3. Add a minimal dispatcher with a closed one-argument command grammar.
4. Preserve the shared-module dependency DAG and reject cycles through the
   bundler.
5. Remove the six old package resources after the one bundle exists.

Exit condition: source remains decomposed, while the distributed program has
one entrypoint and no implicit command.

### Step 4: Cut Over Product Models And Bounds

1. Replace six filename constants and six byte fields with one bundle constant
   and one byte field.
2. Render and hash exactly one target control resource.
3. Change `TARGET_CONTROL_FILE_PATHS` to one path.
4. Reduce `MAX_TARGET_ADAPTER_FILES` and the documented provider read bound.
5. Update registry schemas to require exactly the one control path.

Exit condition: malformed legacy, missing, extra, or digest-mismatched control
sets cannot be admitted.

### Step 5: Cut Over Runtime Consumers

1. Change generated plan, validation, and gate commands to the same bundle path
   with distinct literal subcommands.
2. Change consumer-lab execution to pass one exact subcommand per process.
3. Keep environment, filesystem permissions, process limits, and output parsing
   unchanged.
4. Remove any duplicated environment-key construction encountered on the
   touched path when its value is exactly identical.

Exit condition: runtime invocation is explicit and all existing observable
outcomes remain unchanged.

### Step 6: Regenerate And Rebind Fixtures

1. Regenerate both target fixture directories from the product renderer.
2. Remove the six retired fixture files and add one generated bundle.
3. Update exact execution-registry digests and workflow command lines.
4. Update package-resource inventories and counts.
5. Update all tests and helper factories that name the fixed control set.
6. Preserve the four-way adapter cancellation oracle with two auxiliary
   workflow blobs so the smaller minimal target does not reduce concurrency
   coverage or deadlock the failure-injection barrier.

Exit condition: no tracked executable, fixture, schema, or test references a
retired control path.

### Step 7: Update Proof Governance

1. Add the source-to-bundle checker to the witness plan and command matcher.
2. Bind the checker, source modules, generated bundle, product consumers, and
   falsifiers to the existing runtime requirements.
3. Regenerate or update exact route digests and required binding tuples through
   repository-owned tooling.
4. Route every source or generated-bundle change to bundle check, Python
   quality, target-control behavior, and workflow lint as applicable.
5. Require every current Proofkit feedback evidence path to resolve to a
   tracked regular file, and replace retired-path references with their current
   evidence owner.
6. Include the source-to-bundle witness in the canonical branch-head quality
   sequence after dependency installation, and make omission a failing
   quality-plan falsifier.
7. Preserve nested execution budgets after expanding the source-admission
   falsifier corpus: the `python.test` child process retains the Python witness
   owner's 600-second bound, its command envelope adds a 60-second wrapper
   cleanup reserve, and the Dev Container portable-proof parent retains a
   separate 900-second aggregate bound with 240 seconds beyond the longest
   command envelope. The aggregate bound is not the sum of every independent
   command maximum; it may terminate a later command before that command's
   local maximum, and such termination is a typed aggregate timeout rather than
   an omitted witness. Compute the longest command envelope from every exact
   command ID admitted into the portable route so a future longer command
   cannot bypass the hierarchy by leaving `python.test` unchanged.
8. Recompute the runtime entrypoint-disposition digest after changing the
   coordinator workflow. Verify all declared source-set memberships and
   digests, including deployment projections, and require byte equality
   between the canonical specification and packaged resource. Preserve their
   runtime-authority classifications.

Exit condition: Proofkit cannot accept a changed source or projection while the
reproducibility witness is omitted.

### Step 8: Validate The Exact Candidate

Run the allowed local static witnesses first:

```text
target-control bundle check
target-artifact checks for both fixtures
requirements admission
Proofkit admission and selective routing
repository JSON and documentation graph
Python lint, typecheck, and import boundaries
workflow lint
package-resource inspection
```

Behavioral, integration, mutation, container, and browser witnesses run only in
the exact-head GitHub Full Check. Re-freeze the candidate after every material
change and require a fresh independent review of the final bytes.

Exit condition: the exact remote PR head has green required checks and no
unresolved P0-P2 finding in the bounded changed-owner review.

## 5. Test And Falsifier Matrix

| Risk                                     | Required witness                                                                                                                                                                                 |
|------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Stale committed bundle                   | Rebuild differs and `check` rejects                                                                                                                                                              |
| Ambient/global bundler                   | Wrong or missing local exact version rejects                                                                                                                                                     |
| CommonJS loader alias omitted by esbuild | Independent AST admission rejects an `arguments[1]` real-esbuild mutant under the production build environment                                                                                   |
| Dependency-oracle disagreement           | Any AST/metafile edge mismatch rejects                                                                                                                                                           |
| Command confusion                        | Missing, extra, and unknown subcommands reject                                                                                                                                                   |
| Consumer behavior drift                  | Existing selected and fallback corpus passes through bundle commands                                                                                                                             |
| Partial target representation            | Registry rejects old, missing, or additional control paths                                                                                                                                       |
| Digest bypass                            | Provider snapshot and registry reject changed bundle bytes                                                                                                                                       |
| Package drift                            | Isolated wheel inventory equals source bundle bytes                                                                                                                                              |
| Workflow drift                           | Static projection accepts only the three exact new commands                                                                                                                                      |
| Permission broadening                    | Consumer lab retains the exact Node permission arguments                                                                                                                                         |
| Read-bound regression                    | Maximum fixture proves 33 files and 39 cold reads                                                                                                                                                |
| Cancellation regression                  | Four-blob fixture proves one failure cancels and joins three siblings                                                                                                                            |
| Source ownership collapse                | Module-ownership gate evaluates source, not generated projection metrics                                                                                                                         |
| Stale Proofkit evidence                  | Missing, untracked, directory, and symlink references reject                                                                                                                                     |
| Nested deadline inversion                | The Python test envelope preserves 60 seconds beyond its child process, and the portable parent preserves 240 seconds beyond the maximum envelope recomputed from the exact portable command set |

## 6. Rollback

Before merge, rollback is ordinary branch reversion to the last green exact
candidate. After merge but before any supported target deployment, rollback is
a new squash PR restoring the prior representation and its exact registry and
fixture bytes.

Once a target representation is supported in production, neither direction is
safe without an explicit migration design. This plan does not grant that future
authority.

## 7. Completion Criteria

The batch is complete when:

1. the design and implementation match one target representation;
2. exactly one generated control program is packaged and rendered;
3. all three commands are explicit and fail closed outside their grammar;
4. target artifact, registry, provider-read, and package bounds are reduced;
5. source-to-bundle reproducibility is a required static witness;
6. source loading has an independent closed AST admission and exact metafile
   parity witness;
7. no retired path remains in tracked current contracts, code, or current
   evidence references;
8. branch-head quality cannot omit the source-to-bundle witness;
9. nested portable-proof budgets preserve the admitted child-process deadline,
   wrapper reserve, and aggregate orchestration reserve for the maximum of the
   exact portable command set;
10. current selected and fallback behavior is preserved by exact-head CI;
11. an independent final reviewer finds no reproducible P0-P2 issue; and
12. the PR is squash-merged only after all exact-head required checks succeed.
