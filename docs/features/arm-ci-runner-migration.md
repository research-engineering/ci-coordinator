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
- Keep the admitted secret policy unchanged. Public identifiers recognized by
  upstream policy require no local exception; real-secret controls retain both
  the runtime route and foreign paths in the scanner's complete input scope.
- Admit only complete control-plane tuples for the original Ubuntu x64 and new
  Ubuntu ARM profiles. Mixing runner coordinates between invocation, plan and
  gate remains inadmissible. Existing consumer workflows retain their profile.
- Go utility hosts may be Linux amd64 or arm64 at the pinned version; the
  existing `GOARCH=amd64` analyzed target remains explicit and unchanged.
- Regenerate coordinated workflows and proof projections from their owners.
  Changing a runner changes its execution profile, not just its visual label.
- The independent native watchdog requires the observed GNU provider, not an
  ambient `timeout` name. Admit its version before starting the native shard;
  preserve interruption, escalation and the existing absolute budgets.
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

Docker's immutable selector may identify a manifest, while scanner `imageID`
identifies original configuration bytes. Observe the canonical config digest
through the same selected amd64 image's bounded Docker-save archive, without
extracting it or reserializing its config. Bind the referenced config hash,
platform and filesystem to inspect; a manifest selector also requires its
original OCI blob hash and exact config reference. Preserve the physical Docker
selector separately. Registry operations retain the exact index-addressed
subject with explicit amd64 selection: a downloaded child blob need not have a
resolvable Docker image record. Repair evidence keeps its existing config-digest meaning;
local scan admission also binds the exact caller input and platform. This
works across the two store representations without accepting arbitrary digest
aliases or assuming a synthetic local scan manifest equals the registry one.

The image remains subject to the same repair, scanner, TLS, migration, runtime,
shutdown, layer-reuse and release identity checks. Cross-build and execution
may be slower. A successful ARM-hosted test cannot establish native amd64
production performance; record the actual environment with every comparison.

## Open Qualification Observation R-1

Status: open, classified `unknown`; no root cause or repair is claimed.

The `Qualify read-only runtime and PostgreSQL TLS` step intermittently exits
124 on the ARM runner while executing the amd64 candidate under QEMU. Observed
occurrences are master push run 36717722372 (`08e4174`) and PR runs 38170139633
and 38170322646. The step took 98-102 seconds when it passed and 109-112
seconds when it failed. The retained diagnostics of run 38170322646 include a
complete `runtime.json` and `migration.txt`, but no `shutdown.json`, so the
expired command was `timeout 20` around `alembic current`,
`timeout 5 docker kill` or `timeout 15 docker wait`. A complete
`migration.txt` does not exclude the first: Alembic prints the current
revision before its connection teardown, and `docker exec` may return after
the output arrives. Duration differences alone cannot attribute the remaining
time.

Source validation found no defect under the
[audit predicate](current-source-audit-validation.md). The candidate sets
`CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS=10`, which partitions into a 5-second
Uvicorn graceful drain, 4 seconds of resource cleanup and a 1-second reserve.
Disabled mode composes no runtime resources, so its in-process work after the
signal is bounded by the graceful drain plus the trivial lifespan exit. The
setting is the
[cooperative runtime bound](../architecture/modules/runtime-composition.md),
not a forced process exit. Uvicorn re-raises the captured signal after the
server returns. Event-loop teardown, non-daemon worker-thread joins,
interpreter finalization, emulator exit and container teardown are outside it.
The deployment's container grace period remains the hard termination fence.
Qualification starts the candidate without `--init`, so the emulated
interpreter is the container's first process. The deployment recipe uses
`--init`.

On failure, the step now records each bounded verification, migration, signal
and wait command with its start time, status and elapsed time. It also records
the candidate state and, after a signaled shutdown, a separate bounded
30-second late-exit observation. These records and a bounded log tail go to
`runtime-failure.json` and `runtime-failure.log` in the failure diagnostics
artifact. The success path, its artifacts, assertions and command bounds are
unchanged, and the step's original failure status is preserved.

Keep the 15-second wait. A future occurrence justifies revising it only if
the evidence shows the expired command was `shutdown-wait`, the log tail
contains `shutdown_complete` and `process_finished` without `shutdown_failed`
or `drain_timeout`, and the late observation records an admitted exit code
with `OOMKilled` false. A revision must also be supported by successful-run
shutdown durations from the same runner profile. A missing completion event,
a drain timeout, a late wait that also expires, an expired signal command or
an expired `migration-current` command points to the application, emulator or
Docker daemon instead. Each of those needs its own investigation, not a larger
budget. This observation makes no claim about native amd64 shutdown latency,
production stop duration or emulator internals.

## Open Build-Cost Observation R-2

Status: open, classified `unknown`; no root cause, budget change or build-cost
reduction is claimed.

The `Build and verify the runtime image` step of the `Container runtime smoke`
job took about 8 minutes on PR #55 (2026-09-30) and 10.5-11 minutes on the
stacked chain ending at `62d7765`. On some PRs the image build exceeded its
600-second command bound: four times on PR #57 and three times on PR #62. The
step builds the amd64 runtime image under QEMU on the ARM runner with
`--platform linux/amd64 --pull` and no build cache from earlier runs. Until
now the smoke printed only its final JSON report, and a timeout reported only
the expired bound, so no build stage could be attributed.

Source validation: `scripts.container_runtime_smoke` builds through
`scripts.container_smoke_runner`, which calls the finite captured-command
executor directly. It does not use the proof command sequence or its
environment projection. The child inherits the step environment, its output
capture is bounded to 10,000,000 bytes, and the executor's 600-second default
bound is now passed explicitly with the same value.

The build argv adds `--progress=plain`, as the same job's build-input witness
already does. This pins BuildKit's client-side rendering to the plain format
that the non-terminal capture selects unless `BUILDKIT_PROGRESS` overrides it.
It does not change the Dockerfile, context, platform, `--pull`, cache use or
produced image. After the build command returns, whether it passed, failed,
timed out or exceeded its output bound, the runner parses the captured stderr
progress and appends one bounded JSON record to the step summary. It then
applies the unchanged failure handling. The record contains:

- the outcome (`passed`, `failed` or the executor failure kind such as
  `timeout`), exit code, measured elapsed seconds and the 600-second bound;
- vertex counts by state: `done`, `cached`, `error`, `canceled` and
  `unfinished`; and
- at most 64 vertices with their BuildKit name (at most 160 characters), the
  reported `DONE` seconds and the latest log offset. Unfinished, failed and
  canceled vertices are retained first, then the slowest completed ones; the
  omitted count is explicit.

The success report, stderr text, exit status and failure messages are
unchanged. As with R-1, the record is diagnostic: an unwritable summary emits
one stderr line and never changes the outcome. Outside GitHub Actions no
summary path exists and no record is written.

BuildKit runs stages concurrently, so vertex seconds do not sum to elapsed
time. `DONE` seconds are BuildKit's rounded report. For an unfinished vertex
the latest log offset is only a lower bound, and a silent vertex has none.
The record cannot separate emulation overhead from the work itself.

Revise the 600-second bound only with records from successful and timed-out
runs on the same runner profile. They must show the same vertices still
progressing at expiry, with no `error` or `canceled` vertex and no unfinished
vertex whose log stopped advancing. Successful elapsed times must also approach
the bound, and the growth since PR #55 must be attributed to named vertices.
A cost reduction under CI-006 needs the dominant vertices across several runs.
If emulated compilation or dependency installation dominates, each candidate
such as cache reuse, prebuilt repair artifacts or native cross-compilation
needs its own decision under the repair, provenance and release contracts.
A stalled vertex, a dominant base-image resolution or large variance between
runs of identical source points to the runner, registry or emulator instead.
None of these justifies a larger budget.

## Acceptance And Revision Conditions

Scanner execution and severity admission are separate predicates. The pinned
Grype CLI collects all unsuppressed matches with an explicit empty scanner
threshold and must exit zero. The release-owned validator still rejects every
High/Critical occurrence without current exact repair evidence, and every
unknown severity. Qualification, publication and scheduled scanning share this
version 3 contract. This avoids treating an ambiguous nonzero scanner outcome
as usable evidence; it does not change the security threshold, matching engine,
database freshness or repair policy. Older version 2 receipts remain historical
and do not qualify a new version 3 execution.

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
