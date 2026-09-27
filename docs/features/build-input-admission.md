# Build Input Admission

Status: candidate preparation; native qualification and publication remain pending.
This is the bounded CICD-30/31 implementation and CICD-26 source requalification under the
[current-source audit workflow](current-source-audit-validation.md), not a new backlog.
The [runtime owner](../architecture/modules/runtime-composition.md) and
[toolchain procedure](../how-to/toolchain-updates.md) own the enduring contracts.

## 1. Decision And Frozen Inputs

The initial source cohort used f81b5860edd7e7be6f41d6a1f68a870b2a2622c3. The 42 inspected input
preimages have manifest SHA256
b4f589b749f95ef9820443ec77df85cbb3cfb92b2098fd03748093ccc82b73e9.
Relative to the earlier design, 39 are equal; module-ownership prose and two
frontend-related proof projections changed without changing this build contract.

Pinned uv0.12.17 already passes the full-lock hash strategy, including unselected
groups, into isolated build resolution. Keep setuptools84.0.0, uv.lock,
requirements-dev.lock and pyproject bytes unchanged. Missing explicit constraints
does not establish a missing content guard. Retain the existing build positive;
do not describe the source counterguard or unchanged positive as a new repair.

Root packageManager owns pnpm12.5.1 content identity:
`sha512:e3f305bc784a2bc89f5ad3b6138889470fae8d2af5f36b61216ec91c2c3d64089775f9de38aac331044ea40f245cb0d5666392dfdf65824e1907ef6a2c62de5f`.
Complete official archive bytes (991276) and registry signature were independently
bound before this candidate. This is not a native Corepack/image receipt.

## 2. Closed Mechanism

All three Node recipes retain their exact Node24.21.0 image digest and require
bundled Corepack0.36.0 before prepare. Reject missing/wrong executables and any
pre-existing COREPACK_HOME, including dangling links. Create the fresh owned
home once; do not delete or adopt a cache and do not reinstall Corepack via npm.
Prepare the full root pnpm version+sha512 literal before install. Preserve
PNPM_HOME, proxy behavior, user/chown, patches, frozen install and pnpm store.
The existing declaration checker compares the root spec, optional frontend
override and all three Docker projections; it never certifies image contents.

Append only nested dotenv/DS_Store and the five named frontend transient
populations to .dockerignore. Keep existing exclusions and actual packaging
inputs. Docker's native context matcher, not a replacement glob implementation,
decides inclusion. No general secret-detection or reproducible-image claim.

## 3. Native Qualification Boundary

One command, `python3 -m scripts.build_input_witness`, has independent admission
and runs separately from existing container smoke in the same GitHub job.
It reuses bounded_process, the existing Docker declaration reader, native
Corepack and Docker's context matcher, not an installer or trust registry.
All execution is GitHub-only; orchestration doubles cannot qualify a carrier.
The existing smoke entrypoint has only the standard library. Keep YAML imports
inside the declaration-checking path and reuse only its pure Docker reader and
manager-spec predicate in the native helper. Full declaration validation remains
the existing separate owner gate; add a site-packages-disabled import witness.
This avoids installing a new dependency or widening the workflow just to reuse
the parser. Missing YAML must still fail the full checker, not skip its inputs.

Use a clean tracked Git snapshot, uniquely owned temporary roots, finite
source/member/byte/process limits and exact owned image/container names.
Do not copy workstation untracked files, arbitrary URLs or credentials.
Standard tar extraction admits only bounded regular files and directories.
Only declared context markers and Dockerfile controls are synthetic.

Actual pinned-carrier positives execute all three source-owned bootstrap recipes
and the prepared pnpm version. In a fresh home, retain the original valid archive
and change ONLY expected sha512: require Corepack's hash-mismatch diagnostic with
the independently bound actual digest. A network failure or guard text echoed
from a Docker RUN command is not this witness. No custom index, archive mutation,
signing key or integrity-disable flag. No signed same-version tamper claim.

Atomic mkdir without -p rejects existing directories, files and dangling links.
Use a cache populated by actual successful prepare as the negative; the
fixture-only countercontrol relaxes only mkdir to mkdir -p with the wrong
expected hash. Native cache reuse must then succeed. This separates the
freshness boundary from the download guard without adopting cache authority.
Missing/wrong bundled versions reject before manager preparation using fixed
diagnostics. Exact-input layer reuse is not a hostile-cache guarantee.

A scratch COPY probe uses Docker's context with one synthetic marker per new
exclusion. Remove one rule and require its exact marker to appear. Independently
compare licenses, frontend entry/config/fonts/OFL, patch, backend source/resources
and Alembic files. No replacement glob matcher or real dotenv contents.
Excluding tsbuildinfo does not prove a past compiler run skipped an error.

CICD-26 remains source-scoped requalification, not a repaired or newly mandatory
adversarial gate. Preserve the unchanged full Docker frozen isolated project
build and packaged-runtime positive. Pinned uv's full-lock counterguard defeats
the missing-guard inference; binary adversarial behavior and all future backend
hook dependencies remain unproved. No backend archive/index fixture or lock edit.

Final smoke retains startup, non-root, liveness, UI resources and all 17 revisions,
and checks development/build distribution and executable absence. Qualification
or cleanup failure prevents PASS. No new job, existing leaf/job budget increase
or release DAG is introduced. The branch-head aggregate includes the added
600000ms leaf and retains its existing 420000ms orchestration reserve.

## 4. Before-Write Owner Readiness

| Exact owner paths | Delta and protected observation | Independent falsifier / gate |
| --- | --- | --- |
| package.json | Full native manager pin only; versions/scripts unchanged | Wrong/missing/malformed hash; original archive with correct/wrong expected hash |
| Dockerfile; frontend/Dockerfile.dev; docker/ci/connected-browser.Dockerfile | Bundled carrier and fresh home; preserve images/proxy/users/layers/patches | Actual carrier positive, wrong/missing carrier, cached-home rejection |
| .dockerignore | Eight narrow added patterns; no broad allowlist | Actual Docker exclusion and individual removed-rule control; required bytes retained |
| scripts/dev_environment/toolchain.py | Reuse native declaration parser; full manager spec and third recipe | Root/override/each prepare mismatch; unsupported syntax; input recapture |
| scripts/tests/test_dev_environment_toolchain.py; backend/tests/unit/runtime/test_supply_chain_configuration.py | Independent literal and ordering controls | Rest-valid current declaration plus one changed operand |
| scripts/build_input_witness.py; scripts/tests/test_build_input_witness.py | Finite native qualification orchestration | Hash/cache/context controls, command/output/cleanup bounds; no fake native PASS |
| scripts/container_runtime_smoke.py; scripts/tests/test_container_runtime_smoke.py; scripts/tests/fake_docker.py | Preserve independent smoke; actual runtime absence check | Same profile/argv without qualification bypass; real runtime positive; fake tests remain orchestration-only |
| docs/how-to/toolchain-updates.md; docs/architecture/modules/runtime-composition.md | Honest normative content/cache/context boundary | Source consistency and retained old feature/plan bytes |
| docs/features/build-input-admission.md | This new rationale/readiness owner | Explicit inputs, alternatives and unresolved native gates |

The initial 16-path source cohort excludes backend constraints/locks,
API/runtime behavior and CICD-22 release authority. Command, workflow and
requirement integration preserves every old route and required gate; generated
projections follow their existing owners. Final review follows current AGENTS.

## 5. Alternatives And Reopening

Source requalification and the retained build positive avoid a redundant lock cycle.
Native literal pnpm projections reuse the existing checker; copying package.json
earlier for argument-free prepare would require new stage/provenance parsing.
Bounded expected-hash/cache/context controls require no new installer or parser.
Small shared build/context qualification avoids separate proof pipelines without
claiming measured savings. No broad fixture framework or dependency is needed.

Reopen on missing bundled Corepack, native evidence contrary to uv's pinned
source counterguard, or a cache hit masking the fresh-home boundary,
required context/resource loss, new dependency delta, unbounded cleanup or a
qualification exceeding its existing budget. Do not weaken the oracle or silently
expand the whitelist. Carrier, native hash/cache/context controls and actual image
population remain UNEXECUTED until exact-head GitHub evidence is admitted.

Primary sources: [uv sync](https://raw.githubusercontent.com/astral-sh/uv/0.12.17/crates/uv/src/commands/project/sync.rs),
[Corepack install/cache](https://raw.githubusercontent.com/nodejs/corepack/v0.36.0/sources/corepackUtils.ts),
[Corepack registry verification](https://raw.githubusercontent.com/nodejs/corepack/v0.36.0/sources/npmRegistryUtils.ts),
[Docker context](https://docs.docker.com/build/concepts/context/#dockerignore-files).

## 6. Corrective Readiness Before BUILD-1..3 Edits

The first candidate incorrectly composed qualification 600s, cleanup 30s and the
old smoke under container.smoke 600s. Independent review also established primary
failure masking and synchronous host cleanup outside that window. Correct only
these temporal/lifecycle boundaries; keep N1-N10 and all content/resource inputs.

Prefer a standalone CLI to squeezing both workloads into the old budget. Root
wires an independent catalog command (600000ms) in the existing 25-minute job.
Old container.smoke 600000ms and its actual image/startup/resource checks remain
unchanged. The new helper owns D=start+570s, work ending at D-60s, Docker cleanup
at most 30s and no later than D-25s, then host cleanup at most 20s and no later than
D-5s. Every child timeout leaves 3s for ordinary termination and is followed by
an exact phase-time check; final late completion/publication rejects.

The existing process owner normally allows 1s TERM-to-KILL, another 1s to close
pipes and 20ms polling. Its exception/finally path can ultimately wait without a
timeout. Reserved time is not a hard OS-kill guarantee: abnormal scheduler,
filesystem or kernel delay remains unconfirmed and cannot produce a late PASS.

Record exact unique temp-root/Docker names before effects; a temp-root collision
is not owned and must not be removed. Supervise one stdlib recursive-removal child
through bounded_process for the exact owned root. No TemporaryDirectory context
exit, synchronous recursive removal or global prune. Retain nine copied contexts
until final cleanup, bounded by 11*128MiB source/archive/copy payload plus finite
marker/recipe data. Never restore the throwaway snapshot's ignore file.

Keep the first Exception/BaseException, including cancellation; cleanup failure
adds only bounded class information. Successful work plus uncertain cleanup is
failure. Required rest-valid cases independently alter primary/cancellation,
Docker cleanup, filesystem cleanup, timeout, remaining reserve and late result.
Remove qualification coupling from old smoke and its test bypass; preserve the
new forbidden-tool check and same profile/argv. Only the helper/test, smoke/test
and three already-admitted documents require corrections. Existing shared process
and smoke runners, Python inputs, prior designs and root metadata stay untouched.

## 7. Exact Repair Policy Readmission

Dockerfile is an exact source input of the existing
[runtime repair policy](../../docker/runtime/security/repaired-matches.v1.json).
Its changed build preparation therefore requires a new source binding and
fresh image qualification, even when installed repaired component bytes remain
unchanged. Combine this with the time-bounded policy readmission owned by
[runtime base qualification](runtime-base-qualification.md); do not issue
two immediately superseding image-policy epochs.

The candidate retains all eight occurrence identities, installed-file hashes,
causal before/after/final-image expectations and the maximum 14-day interval.
Its new dates and source hash are a proposal, not evidence. Admission requires
fresh observations under the new policy hash; old-policy evidence cannot be
relabelled. No severity threshold, repair expectation or pinned base is relaxed.
Release publication still needs exact-master and registry-bound evidence.
An older signed release payload does not become valid under the new policy hash,
and source merge alone neither activates a deployment nor closes CI-002.
