# ARM CI Runner Migration

Status: current runner migration contract

Date: 2026-09-30

Task ownership: [CI-001 and CI-006](../../ROADMAP.md#b1-own-ci-pilot-and-measured-efficiency).

## Outcome And Scope

Run Linux jobs, including reusable and generated workflows, on the standard
`ubuntu-26.04-arm` GitHub-hosted image. The public repository receives four
vCPUs and 16 GB of memory under the
[standard runner profile](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
The requested migration does not provision a paid larger runner.

The `developer-host-lifecycle` job remains on `macos-15`: its observation is
Apple Silicon developer behavior, including operating-system process and lock
semantics. Linux ARM cannot provide that evidence. This explicit exception
preserves the accepted macOS support contract and is not a skipped Linux job.

## Protected Observations

- Preserve native test identities, independent oracles, aggregate operands,
  coverage and mutation floors, time bounds, permissions and secret boundaries.
- Download the already pinned utility versions for ARM and verify their exact
  release checksums. Source/history scanning still consumes all admitted inputs.
- Admit the observed hosted runner through actionlint's exact additional-label
  configuration, without a wildcard or a runner-rule suppression. The upstream
  configuration key is named `self-hosted-runner`; it does not provision or
  reclassify this GitHub-hosted runner. Unknown labels remain errors.
- Secret-scanner exceptions require an exact public value, rule and owner path,
  with changed-value and foreign-owner negative controls. A compact Proofkit
  identifier is not an API credential; the containing file remains scanned.
- Admit only complete control-plane tuples for the original Ubuntu x64 and new
  Ubuntu ARM profiles. Mixing runner coordinates between invocation, plan and
  gate remains inadmissible. Existing consumer workflows retain their profile.
- Go utility hosts may be Linux amd64 or arm64 at the pinned version; the
  existing `GOARCH=amd64` analyzed target remains explicit and unchanged.
- Regenerate coordinated workflows and proof projections from their owners.
  Changing a runner changes its execution profile, not just its visual label.
- Keep the published runtime output `linux/amd64`. ARM host migration does not
  authorize a release platform, repair policy or verifier compatibility change.

## Cross-Architecture Image Jobs

Use the pinned Docker QEMU setup action with a digest-bound binfmt image and
only the required amd64 emulator before building or executing the existing
amd64 candidate. Use native ARM utility builds, including the independently
admitted Grype archive URL and checksum, throughout the host toolchain.
The scanned image and repair policy remain amd64; scanner architecture does
not select or redefine the image's platform. Provenance admits the two exact
Linux builder platforms separately from the protected amd64 output.
The attestation job selects the amd64 child explicitly without executing it
or installing privileged emulation in its signing environment.

The image remains subject to the same repair, scanner, TLS, migration, runtime,
shutdown, layer-reuse and release identity checks. Cross-build and execution
may be slower. A successful ARM-hosted test cannot establish native amd64
production performance; record the actual environment with every comparison.

## Acceptance And Revision Conditions

1. Compare a fresh exact-master x64 Full Check with the ARM candidate, retaining
   source, event, attempt, job/step durations, outcomes and declared skips.
2. Independently compare native inventories: every prior node remains present,
   and additions require real assertions. Existing gate failures remain failures.
3. Qualify all Linux jobs on ARM, the retained macOS job, and exact amd64 image
   execution in GitHub within existing budgets. Local checks remain static.
4. Review changes using the current repository reviewer policy; repair material
   counterexamples and rerun evidence invalidated by a source change.
5. Publish the exact chosen source only after its required source/image checks;
   installation and production admission remain separate task outcomes.

Compare total elapsed time, queue/setup overhead and individual job/step costs
under identified cache conditions. These observations describe the measured
runs; a single comparison does not prove a universal causal speedup.
If an action, wheel, tool, image or time bound is incompatible, resolve the
specific prerequisite or explicitly revise the runner decision with its
measured consequence. Never omit a required observation to report success.

A native ARM runtime artifact is a separate revision requiring deployment
architecture, component-repair and scanner evidence. It is not necessary merely
to use an ARM CI host. Revisit this choice when a real ARM deployment is chosen
or cross-build costs materially outweigh the measured benefit.

The connected SSH log witness admits only the legacy Docker CLI command and
the current CLI's quoted `--host=unix://` form for its exact selected socket.
Both select the same fixed executable and argv; incoming command text is never
executed. Wrong sockets, omitted paths and additional commands remain rejected.
This does not claim complete compatibility with every future CLI serializer.
