# Developer Experience Convergence Implementation Plan

Status: implementation and qualification contract; external adoption remains conditional

Date: 2026-09-08

Design: [Developer Experience Convergence](developer-experience-convergence.md).

Implementation baseline: `eed5a6ec56fbc35599aa21b434f989f6e2d7c2e1`.

## 1. Delivery Contract

Deliver the design's supported developer journeys while preserving H1-H7. The
design owns the decisions and alternatives; this plan owns the implementation
order, acceptance witnesses, rollback and remaining evidence. Neither document
changes executable behavior by itself. Phase completion requires its named evidence.

Rebind base/head and dirty-worktree ownership before each slice. Reconcile current
contracts rather than overwriting intervening work. Preserve historical plans and
designs; route successors through the index. Keep documentation and task help in
English. Peer-repository edits, production deployment and bot installation are
outside this repository's implementation scope. Worktree creation, pull-request
publication and merge into master are authorized for this delivery.

Use the simplest implementation that passes the relevant journey and hard-property
oracles. A proposed mechanism that fails its qualification stays disabled; record
the unmet objective and revise the design. An unsuccessful mechanism pilot cannot
be relabeled as completion of the behavior it was supposed to provide.

Each slice updates its requirement, implementation, falsifier, route binding and
user instructions together. Use existing REQ-CI-DEV requirements where applicable;
allocate a new identifier from the current owner only for a new obligation. Do not
mark a future feature implemented merely to make the requirement inventory green.
Refresh the developer-environment route-source digest after admitted route edits.

## 2. Work Packages And Dependencies

| Package | Result                                                                    | Depends on                  | Primary owner                                   |
|---------|---------------------------------------------------------------------------|-----------------------------|-------------------------------------------------|
| P0      | Frozen journeys, capability floor and cost protocol                       | Design review               | Developer environment and proof owners          |
| P1      | Safe diagnostics, partial status, nonblocking logs and open               | P0                          | Instance CLI and Compose adapter                |
| P2      | Owned watch cancellation without overlapping mutations                    | P1                          | Instance operation and process lifecycle owners |
| P3      | Complete source/configuration update feedback                             | P2                          | Compose/image and watch-witness owners          |
| P4      | Smaller app-start preparation with one tool-version authority             | P0, P1, P2                  | Task/bootstrap and dependency owners            |
| P5      | Repeatable UI demo and browser inspection commands                        | P0, P1                      | Operator UI and browser-witness owners          |
| P6      | Explicit backend attach debugging                                         | P2, P3                      | Development image and instance owners           |
| P7      | Qualified coherent dependency-update ownership                            | P0                          | Dependency-maintenance and workflow owners      |
| P8      | Integrated qualification, optional editor preservation and adoption guide | P1-P6; P7 decision recorded | Developer environment and proof owners          |

Independent preparation and UI work may proceed in parallel after their inputs
are frozen. Keep one writer for shared CLI, mise and route files; parallel work
does not authorize competing edits or provider operations. Packages are coherent
acceptance units, not a mandated number of PRs. Do not batch a failed lifecycle
prototype with unrelated improvements to obscure its outcome.

## 3. P0: Establish The Baseline And Oracles

Inspect current `mise.toml`, lockfiles, Compose files, Dockerfile inputs, instance
CLI/state/process owners, browser setup, Dev Container and selected CI commands.
Revalidate the external capabilities against the exact proposed tool releases.
Derive a complete Compose minimum from used actions and flags; 2.23 is only the
known lower bound for `sync+restart`, not an automatically certified full floor.

Create the journey observations in section 12 using the existing witness owners.
Record actual supported host/platform combinations separately from platforms
present in a lockfile. Preserve existing support; an untested platform has an
explicit qualification gap. Native Windows support is not introduced. WSL is
qualified independently before it is advertised as a Linux-host route.

Before measuring candidate performance, record host/architecture/runtime,
baseline revision, input scenario, cold/warm cache definitions, paired repetition
count, raw observations and an acceptance budget appropriate to the journey.
Report failures with successful samples. Reuse the repository's evidence workflow;
do not build a general benchmarking service or invent a population percentile.

Compare two preparation candidates: backend-only synchronization into the existing
full environment, and the design's separately admitted runtime environment. The
first removes host frontend preparation with fewer moving parts; the second can
avoid host test tooling but adds installation, identity and concurrency obligations.
Measure both before selecting the more elaborate mechanism. Separately prove
that mise task selection does not eagerly install unrelated global tools.

Acceptance: every E1-E7 observation has a current source binding and a concrete
oracle; H1-H7, platform scope and conditional-tool rejection criteria are explicit.
No runtime behavior is changed by this package alone.

## 4. P1: Diagnostics And Observation

Primary files: `scripts/dev_environment/cli.py`, `compose.py`, `private_files.py`,
their existing tests, `mise.toml`, and a new preflight module only if needed to
keep dependency-free imports separate from installed adapters.

1. Add the standard-library doctor entrypoint and closed diagnostic reasons.
   Detect missing Python dependencies before importing them. Preserve the existing
   generic error key and default JSON interface; introduce the design's versioned
   envelope and explicit human rendering without raw exception/stderr leakage.
2. Validate the derived Compose floor and return a fixed actionable remedy for an
   unsupported capability. Keep provider calls bounded. Doctor is observational;
   connected credential/provider checks require an explicit option.
3. Enumerate stopped/running owned services before resolving endpoints individually.
   Preserve known fields and distinguish missing state, partial health, provider
   unavailability and bounded observation instability.
   Implement the design's complete/partial/missing/invalid/provider-unavailable
   table exactly, including stdout/stderr, string-only available endpoints and
   exit 0/2. Bind intentionally extended failure output separately from preserved
   complete-success behavior; do not assert byte-identical compatibility.
4. Bind selected log streams to admitted container identities. Release observation
   locks before following; stop with reconnect guidance when an identity changes.
   Add bounded `--tail`, `--follow`/`--no-follow` and repeated `--service` options;
   preserve follow/tail-200 defaults. Reject invalid service/bound values before
   Docker access and prove finite completion without follow.
5. Add `dev:open` with newly resolved loopback HTTP endpoint, argv-based opener and
   headless fallback. Keep status/logs/open free of dependency installation.

Acceptance: J1-J3 and the observation parts of J4 pass, with no secret disclosure,
cross-worktree stream, repair side effect, indefinite admission or whole-status
failure caused by one stopped service. Existing success JSON fields, exit behavior
and direct CLI callers have explicit compatibility witnesses, with the designed
failure-output extension identified rather than hidden in a generic pass.

Rollback: retain old entrypoints and generic diagnostics throughout migration.
If an additive renderer or opener fails, disable that convenience route without
rolling back ownership admission or exposing raw provider errors.

## 5. P2: Lifecycle Ownership And Cancellation

Primary files: `scripts/dev_environment/private_files.py`, `cli.py`, `compose.py`,
`scripts/bounded_process.py`, the existing process/CLI tests and connected-stack
witness. Reuse existing owners; a small watch-session module is justified only
by its lifecycle responsibility, not by a line-count target.

First prototype the design's control-lock, mutation-lock and nonce protocol in
isolation. State all acquisitions, deadlines, child ownership and cleanup order.
Bound acquisition of the control lock as well as the mutation lock. Report
`quiescent` or `blocked`; never proceed to provider mutation from the latter.
Use explicit barriers and owned-process observations in witnesses; elapsed sleeps
or a recorded PID do not establish lock acquisition, quiescence or identity.

Prove the provider child cannot outlive mutation authority after hard parent death
on each supported host/provider combination. Verify the actual Compose process
behavior; a unit fake retaining a file descriptor is insufficient. If inherited
kernel ownership or an equivalent bounded mechanism cannot meet H3, stop and
revise the mechanism before exposing cross-terminal cancellation.
Audit the existing helper's unconditional final waits and explicit lock unlock:
its reuse does not by itself establish a finite interactive cancellation budget.

Then add prompt `operation_busy` for ordinary conflicting mutations and explicit
`dev:down --stop-watch` plus the corresponding confirmed reset route. Admit the
request nonce in a dependency-free cancel stage, stop/reap and establish provider
client completion within finite budgets, then release every lock held by that invocation.
Only afterward acquire an environment lease and re-admit environment/state/resources
for ordinary down/reset. No atomic cancel-plus-down claim is made. A new watcher
winning this gap produces busy; do not cancel its nonce automatically. An unusable
environment produces the original session's stop outcome, an explicit preparation
remedy and exit 2, with down/reset not performed and no inferred container state.
Cleanup never waits on a lock held by its cancellation requester. Stale requests
cannot cancel a later session. Admit normal completion only for a joined Compose
watch client with zero exit or causally owner-requested cancellation exit 130,
no escalation and no surviving process group. Reject unsolicited 130 and all
other unadmitted exits.
Compose 2.39.4 is the minimum candidate because its watch batch is joined;
qualify this behavior natively. Do not label client completion as a Docker daemon
transaction-drain proof. Unadmitted, forced and abandoned outcomes retain a fence.

Acceptance: J4-J5 cover active, stopped, failed, replaced and abandoned watchers;
same-root conflicts; different-root independence; request before/after publication;
duplicate requests; cancellation timeout; and hard parent death. Exercise lock
inversion and provider-child survival counterexamples. Existing finite process
callers retain their deadlines, bounded output and residual-process policies.
Include an edited lockfile, missing/broken venv, a joined client batch still in
flight, and a new watcher starting between cancel and down. Prove that normal
completion permits the next operation and abnormal completion fences it. Docker
daemon-internal crash effects are explicitly outside the clean-client guarantee.

Rollback: an unqualified cancellation route remains disabled. Stop an owned active
session through the admitted path before changing protocols. Do not release a live
mutation lock, delete lock state to force progress, signal a file-recorded PID or
run global cleanup. Preserve a clear diagnostic/manual recovery path.

## 6. P3: Complete Watch Inputs

Primary files: `compose.yaml`, `frontend/Dockerfile.dev`,
`docker/development/backend.Dockerfile`, relevant entrypoints,
`scripts/dev_environment/watch_witness.py` and its configuration/witness tests.

Enumerate material COPY, package-manager, entrypoint and runtime-configuration
inputs from the current source. Map each to exactly one sync, restart, rebuild or
explicit migration action. Add HTML and Vite-loader/configuration coverage as
required by actual effects. Include relevant workspace settings, patches and
Dockerfile inputs without watching caches, credentials or generated noise.

Treat Compose-model changes separately from image
inputs: stop the admitted watcher, validate the edited configuration, then owned
recreate/up with volumes and credential identity preserved. Invalid configuration
must leave existing resources intact. Prove that an environment/port/health change
reaches the new service model; an image rebuild alone does not satisfy this case.

For each input class, run the effect witness on the selected Compose floor and
current supported version. Observe rendered content/configuration, changed backend
response and runtime identity, and changed dependency/build behavior. A copied
file or successful Compose exit is not the effect oracle.

Acceptance: J6 passes for every material input class; unrelated database data,
secret identity and worktree B remain unchanged. HMR retains its narrow update
benefit, and explicit migrations cannot become automatic SQL execution.

Rollback: stop the admitted watcher before changing rules. Restore the previous
rule and use an explicit owned rebuild for an unsupported edit class; mark that
automatic-feedback objective incomplete until its effect oracle passes.

## 7. P4: Task-Specific Preparation

Primary files: `mise.toml`, existing tool/dependency locks, `.gitignore`, current
bootstrap entrypoints and configuration tests. Change package graphs only when
an independently necessary dependency is introduced.

Use P0's simpler sufficient candidate first. Container app preparation must omit
host pnpm installation and host frontend artifacts; full `install` keeps its
complete existing semantics. The selected mise configuration must use one exact
version declaration per tool and preserve lock/platform admission. Record a
blocked separation explicitly if the selected release cannot express it safely.

For both candidates, coordinate every supported environment writer and user:
full `install`, app preparation, lifecycle/observation commands and verification
tasks. The dependency-free task front door acquires a read lease before invoking
the venv; sync requires an exclusive lease. Use fail-fast acquisitions and the
design's environment/control/mutation order. Release the preparation write lease
before instance work, then acquire a read lease and re-admit. No waiting installer
may prevent the shared read lease needed by `down --stop-watch`.
Admit destination/root/platform/interpreter/lock identity for both candidates,
make interrupted sync observable, and preserve Dev Container volume isolation.

If a separate runtime environment is justified, use the existing backend lock and
`UV_PROJECT_ENVIRONMENT` as designed. Do not prune `backend/.venv`. Admit destination,
root/platform/interpreter/lock identity, serialize preparation and make interrupted
or concurrent preparation observable before any dependent command runs. Preserve
the no-install behavior of observation and verification. Dev Container provisioning
must not write this host environment through its workspace mount.

Acceptance: J7 records exactly which tools and dependency artifacts cold app start
installs; unrelated Node/pnpm/host frontend work is absent. Full install followed
by app preparation retains the full environment; prepare interruptions, symlinks,
foreign platforms and concurrent readers fail safely. Compare measured cost with
P0's predeclared budget; no startup-speed claim follows from file-count reduction.
Exercise `watch -> install attempt -> down --stop-watch`: install reports busy,
cancellation requires no environment lease, and ordinary down acquires its read
lease only after cancellation releases all locks. Also exercise a sync already
holding the exclusive lease and lockfile edits during watch: cancellation remains
available, while a blocked/stale preparation prevents only the subsequent down.

Rollback: restore the prior explicit preparation dependency without touching
credentials or data. Disable a new environment path rather than deleting a live
environment. Keep exact locked full installation available as the recovery route.

## 8. P5: UI Scenarios And Browser Debugging

Primary files: `frontend/tests/fixture.ts`, `frontend/tests/browser` scenario/setup
owners, `frontend/package.json`, `mise.toml`, and a new development-only harness
outside production imports. Inspect actual fixture ownership before extracting
shared data; do not transplant complete test files into runtime code.

Extract side-effect-free payloads and route handlers. Validate synthetic responses
using production runtime schemas. Add explicit browser preparation, `dev:demo`,
`browser:ui`, `browser:debug` and `browser:record` using existing Playwright modes.
Use a fresh owned loopback Vite instance and browser context for the demo. Expose
scenario selection/reset and a visible synthetic-session indicator in the harness.

Cover populated/empty portfolios, unauthorized access, provider failure/retry,
successful/failed retained runs, economics comparison and stale response after
navigation. Fix identity/time inputs. Reject unhandled application API requests
and non-loopback network/navigation, including paths that could evade interception;
never reuse real cookies, provider credentials or an external development stack.

Acceptance: J8-J9 reproduce all named scenarios and reset without backend/database
mutation. Unknown API traffic fails closed. The production import/bundle graph
excludes the harness, and ordinary authentication remains effective. Automated
browser witnesses retain independent CI setup; a human debug session is not a pass.

Rollback: remove/disable the harness entrypoints. Shared fixture extraction must
retain existing browser witness semantics. No database cleanup or auth rollback
is needed because the demo introduced neither state nor a product bypass.

## 9. P6: Backend Attach Debugging

Primary files: development image/Compose owners, CLI, `mise.toml`, a pinned optional
debugger dependency in its native owner, and explicit editor attach instructions.
Add a debug-only target/override and loopback dynamic debugger port. Map real host
source paths to the image. The release image and application authentication retain
their ordinary behavior and dependencies.

Integrate debug ownership with P2 before startup. Suspend competing backend watch
restart, distinguish waiting for attach from readiness, and apply a finite
attach-aware deadline. Leaving debug restores the ordinary configuration through
an admitted operation. Do not grant ptrace capabilities or a host Docker socket
as a shortcut around an unverified debugger setup.

Acceptance: J10 proves breakpoint/continue and graceful exit on the admitted image,
that a competing watcher cannot replace the debug session, and that the debug port
is loopback-only and absent from ordinary/release operation. H1-H4 remain effective.

Rollback: stop the debug-owned session, clear its override, and start ordinary mode
without deleting application data. A failed attach never becomes a reason to
disable authentication, expose a public port or leave a hidden debug service.

## 10. P7: Coherent Toolchain Updates

Primary files: `mise.toml`, `mise.lock`, backend/frontend/root manifests and locks,
runtime profiles, CI setup, image references and `.github/dependabot.yml`. Add a
Renovate configuration only as part of its admitted proposal-ownership decision.

First enumerate native version relationships and add/extend consistency admission
only for actual equalities/compatibility constraints. Do not create a universal
version registry or force unrelated component runtimes to match.

Qualify an isolated Renovate proposal under real provider permissions: extraction
of mise/uv/npm/Docker/Actions dependencies, regeneration of every supported lock
platform, one coherent Python update and one coherent Node update, tags/digests,
and exact-revision required checks. Verify the proposal needs no additional
runtime/release credential authority. Provider installation is a separate external
prerequisite; a local configuration file cannot prove it happened.

Acceptance: J11 records either an admitted single-owner transition or a concrete
failed criterion with Dependabot retained and a maintainer procedure covering
mise/runtime/image coordination. Preserve security alerts and required checks;
do not run two version-update bots over the same dependencies. No self-hosted
maintenance service is introduced as an unreviewed fallback.

Rollback: quiesce the new proposal owner before restoring the old one. Preserve
reviewed security fixes and resolve existing proposals explicitly; reverting a
bot configuration must not downgrade a dependency or suppress security alerts.

## 11. P8: Integration, Editor And Transfer Qualification

Re-run the affected supported journeys as one user flow: fresh preparation, start,
partial diagnosis, edit, debug, leave debug, stop and restart with data preserved.
Upgrade an existing admitted instance without secret rotation or volume reset.
Retain the optional Dev Container's Linux/editor, platform-isolated dependency
volumes and offline witness properties using the same tool/dependency authority.
Prove its provisioning cannot write host-native dependencies through the workspace.

Keep container lifecycle on the trusted host and retain the existing Dev Container
witness until its independent removal conditions are met. Report its provisioning
cost and actual editor use separately from total CI elapsed time. Do not add a
Docker socket to create a nominal one-command experience.

Publish one concise how-to and discoverable task help, including machine-readable
task inventory, prerequisites, human/JSON examples, safe recovery and a capability
mapping for consumers. The example explains which policies are Coordinator-specific.
Existing consumer Make facades may delegate; no shared runtime/framework is needed.

Record the synthetic application archetype as an unqualified candidate for a separately authorized first adoption exercise.
Preserve its native/npm/demo workflows; document extension-service archetype's ODBC/add-in
constraints and reporting-service archetype's Docker-only startup. Without an actual consumer,
label the example unqualified and transfer acceptance pending. Do not modify peer
repositories merely to tick the adoption box. Require two real owners before
extracting shared implementation, as specified in the design.

Acceptance: J12 and the integrated H1-H7 matrix pass for claimed platforms; every
conditional item has a reject/retain/pending-external decision, cost and owner.
Unmet core behavior blocks core closure. Pending external adoption or bot access
is reported separately and is never labeled completed adoption or qualification.

## 12. Journey And Evidence Matrix

These identifiers organize this delivery; executable requirement IDs remain owned
by the requirements inventory. Extend existing witness owners before adding new
command IDs, and bind every new proof-like path before selective-plan admission.

| Journey                     | Required counterexample or effect                                                                                            | Protected properties | Existing witness route to extend                                       |
|-----------------------------|------------------------------------------------------------------------------------------------------------------------------|----------------------|------------------------------------------------------------------------|
| J1 Doctor                   | Missing dependencies/daemon, wrong Compose floor, invalid state; distinct safe remedy                                        | H1, H5, H6           | `python.test`, `development.stack`                                     |
| J2 Partial status           | One service stopped or restarted during observation; others still observable                                                 | H1, H6               | `python.test`, `development.stack`                                     |
| J3 Logs/open                | Tail bounds, invalid service before Docker, finite non-follow, replacement during follow, unsafe/stale URL and headless host | H1, H6               | `python.test`, `development.stack`                                     |
| J4 Concurrent lifecycle     | Watch/log follow in A; status/down in A; independent mutation in B                                                           | H1-H3, H6            | `python.test`, `development.stack`                                     |
| J5 Cancellation             | Lock inversion, stale nonce, racing new watch, timeout and hard parent death                                                 | H1-H3                | `python.test`, `development.stack`                                     |
| J6 Editing                  | Each material input class changes its observable effect and preserves unrelated state                                        | H1-H3, H5            | `development.stack`, `frontend.browser`                                |
| J7 Preparation              | Cold start omits unrelated host tools; full env survives; interruption/concurrency admitted                                  | H1, H2, H5           | `python.test`, `development.stack`, `devcontainer.verify`              |
| J8 Demo                     | Every named scenario repeats/reset; unknown API and external traffic blocked                                                 | H2, H4, H6           | `frontend.browser`, `frontend.quality`, `frontend.build`               |
| J9 Browser debug            | UI/debug/record launch with isolated fixture session; no real identity reused                                                | H2, H4, H7           | `frontend.browser`; separately labeled interactive evidence            |
| J10 Backend debug           | Attach/continue, watcher exclusion, loopback-only port, ordinary-mode restoration                                            | H1-H4                | `development.stack`; separately labeled interactive evidence           |
| J11 Updates                 | Python/Node proposal keeps native relationships and full platform locks                                                      | H4, H5, H7           | `python.test`, selected exact-revision checks and provider evidence    |
| J12 Upgrade/editor/transfer | Existing state preserved; container cannot overwrite host env; consumer maps capabilities                                    | H1-H7                | `development.stack`, `devcontainer.verify`, separate adoption evidence |

Local authoring runs static checks only, following the repository's execution
policy. Behavioral/unit/browser/database/container witnesses run through the
repository-owned GitHub route, currently `.github/workflows/python-persistence.yml`.
An interactive developer session and a manually observed attach are different
evidence classes; neither replaces required native automation.

For changes confined to planning documents, run documentation graph admission,
requirements admission, Proofkit profile/witness admission, text policy and selective planning.
Planning selects work; it does not execute or pass selected behavioral commands.
For each implementation slice, regenerate selection from its actual changed paths,
run the required exact-revision checks, and preserve all existing non-claims.

## 13. Review And Closure

Use the independent reviewer policy in [AGENTS.md](../../AGENTS.md). Freeze the
reviewed base/head and changed-file bytes; adjudicate each finding against a
reachable counterexample and preserved owner contract. Add another review only
for a material unresolved finding or uncovered independent scope. A reviewer
opinion cannot replace a missing provider/process/platform witness.

Close core delivery only when P1-P6 and integrated P8 behavior meet the design's
Close predicate, current requirements/bindings match implementation, required
checks pass on the delivered revision, and rollback remains usable. P7 must have
an explicit ownership outcome; successful Renovate adoption is conditional.
Report platform, measurements, external adoption and any rejected candidates
without folding them into a universal quality score.

Final evidence contains the exact delivered revision, resolved findings, journey
results, regression results, raw cost observations, conditional decisions and
remaining qualification boundaries. Green static documentation checks establish
a coherent admitted proposal, not an implemented or regression-free environment.
