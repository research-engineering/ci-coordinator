# Developer Workflows And Adoption

Status: implementation guide; exact-revision native qualification is required for delivery

Date: 2026-09-08

The [local evaluation guide](evaluate-locally.md) owns connected startup and
credential boundaries. The [design](../features/developer-experience-convergence.md)
owns the tradeoffs and the [implementation plan](../features/developer-experience-convergence-implementation-plan.md)
owns qualification. This page provides task selection and migration guidance.

## Choose The Smallest Environment

| Goal                                                | Preparation                                                  | Command                                                                                               |
|-----------------------------------------------------|--------------------------------------------------------------|-------------------------------------------------------------------------------------------------------|
| Connected backend, database and UI                  | `mise run install:backend`                                   | `mise run dev:up`                                                                                     |
| Continuous connected editing                        | Backend preparation                                          | `mise run dev:watch`                                                                                  |
| Diagnose without changing dependencies              | Installed mise Python; no venv needed                        | `mise run dev:doctor -- --format human`                                                               |
| Generate isolated API contract cases                | Admitted backend environment                                 | `mise run test:api`; see [API testing](test-api-contracts.md) for deeper exploration and reproduction |
| Observe a partial stack                             | Previously admitted backend environment                      | `mise run dev:status -- --format human`                                                               |
| Last backend logs                                   | Previously admitted backend environment                      | `mise run dev:logs -- --service backend --tail 100 --no-follow`                                       |
| Open the current UI                                 | Previously admitted backend environment                      | `mise run dev:open`                                                                                   |
| Stop an owned watcher and then the stack            | Cancellation does not require a valid venv                   | `mise run dev:down -- --stop-watch`                                                                   |
| Inspect synthetic UI scenarios                      | `mise run install:frontend`, then `mise run browser:prepare` | `mise run dev:demo -- --scenario populated`                                                           |
| Inspect or record a synthetic browser journey       | Same frontend/browser preparation                            | `mise run browser:debug`, `browser:ui` or `browser:record`                                            |
| Attach to the connected backend                     | Healthy `dev:up`; stop the watcher first                     | `mise run dev:debug-backend`                                                                          |
| Complete repository verification                    | `mise run install`                                           | `mise run check`                                                                                      |
| Provisioned verification without external providers | Full preparation                                             | `mise run check:portable`                                                                             |

The connected stack installs its frontend graph inside the development image.
It does not need a second frontend graph on the host. Full installation and the
portable projection retain their existing meaning. Verification does not repair
an environment. An edited lockfile requires explicit preparation; a running
managed dependency user makes preparation fail promptly instead of racing it.

`status` keeps endpoint values as strings and omits unavailable endpoints. It
reports stopped one-shot services and their exit codes. Exit zero means a complete
observation; `dev:smoke` owns readiness. Partial or changing observations return
exit two, available data on stdout, and a stable diagnostic on stderr. JSON is
compatible by default; use `--format human` for terminal summaries. Provider
errors are redacted. Explicit container logs retain their application-written
contents and should be treated accordingly.

`logs` follows only the exact container IDs admitted at startup. Stopped services
produce a finite tail. Replacement or the end of a followed stream asks the user
to reconnect; it does not silently switch identities. A headless `dev:open` prints
the admitted URL so it can be opened manually.

## Edit And Reconcile

### Check Frontend Promises Before Publication

After `mise run install:frontend`, run `mise exec -- pnpm frontend:lint`.
It runs Biome, the type-aware Promise rule and its bounded static fixture corpus
without executing application tests. The same sequence is included in CI's
mandatory frontend check. Use `pnpm --dir frontend lint:promises` for the typed
rule alone while editing. Native `tsc` remains TypeScript 7.0.2.

VS Code can use the recommended ESLint extension with the repository working
directory settings; the optional Dev Container also includes it. In other
ESLint-capable editors select `frontend/eslint.config.mjs` and the installed
frontend package. Keep Biome as formatter and the native TypeScript language
service as typechecker. No global editor settings or Git hooks are installed.

Await Promise-returning React `act` calls. Use explicit `void` only when the
operation is intentionally detached and has a reviewed rejection/lifetime
owner; the syntax alone does not prove that ownership. The
[decision](../decisions/typed-promise-analysis.md) defines scope and limitations.

### Check Documentation Diagrams

Prepare the locked backend/frontend environment with `mise run install` and
Chromium with `mise run browser:prepare`. Then run the same diagram admission used
by CI:

```sh
mise exec -- pnpm diagrams:check
```

This bounded documentation witness is admitted locally. It reads the full current
documentation corpus, uses CommonMark extraction, applies the explicit lint policy
and renders every admitted block with Mermaid 11.17.2 in Playwright Chromium.
It does not start application services. Errors identify the source path and line.
Run `mise exec -- pnpm diagrams:check --artifacts /absolute/new-output-directory`
to retain SVGs for visual review; use a fresh directory for each run.

For an optional checked push, use:

```sh
mise exec -- pnpm diagrams:push -- origin HEAD
```

This enables the repository pre-push hook for that invocation only. It refuses to
replace an existing hook or `core.hooksPath` configuration. No persistent hook
installation or shared Git configuration change occurs. The hook selects relevant
ref updates and checks their complete committed diagram corpus; dirty Markdown
edits cannot change that result. If checker files or lockfiles differ from the
commit being pushed, check out that commit and explicitly prepare its dependencies.
New refs and unavailable remote objects select a full check without fetching.

The [diagram design](../features/diagram-validation.md) owns the supported dialect,
limits, GitHub version evidence and the decision to generate an inventory instead
of maintaining a second tracked registry. CI remains mandatory when a different
Git client or ordinary push bypasses this optional feedback path.

### Connected Source Changes

Source and styles synchronize through Compose Watch. Backend source restarts the
backend. Dependency manifests, locks, patches, image definitions and admitted
entrypoint inputs rebuild their affected image. Compose-model and migration
changes require deliberate reconciliation. Stop the watcher with Ctrl+C in its
terminal and wait for its successful completion, then apply the new model:

```sh
mise run dev:up
```

`dev:up` validates before changing resources, stops the backend to drain its
database connections, then reapplies migrations and runtime access before
starting it again. PostgreSQL, existing volumes and credentials are preserved. Use
`dev:down -- --stop-watch` from another terminal when the intended operation is
shutdown of the whole stack. Ordinary shutdown preserves data. `dev:reset` is
separately confirmed and removes
only the current instance's resources and local credentials. Cancellation is a
separate stage from shutdown: if the environment became stale, the watcher can
stop while shutdown returns an explicit preparation remedy. Run preparation and
retry ordinary shutdown. A newer watch session in that gap is not cancelled.

An ambiguous or forced watcher termination fences subsequent mutations. Diagnose
that session with `dev:doctor`; do not delete lock files or use a global prune to
force progress. Preserve an abandoned instance for owner-scoped inspection; a
new worktree can provide a separate instance while the ambiguous one remains
fenced. Recovery of its old state is not automatically admitted. The clean-stop
guarantee covers joined client completion, not a transactional Docker daemon
crash-recovery protocol.

## Demo And Debug

The browser harness offers `populated`, `empty`, `unauthorized`,
`provider-failure-retry`, `runs`, `economics`, and `stale-navigation`. It owns its
loopback server and fresh browser context, displays a synthetic marker, and resets
by replacing that context. External network access and unhandled API routes fail
closed. It never reuses the developer's authenticated browser profile. WebSocket
traffic is disabled in this harness; reload or scenario reset shows source edits.
The normal connected stack retains its ordinary frontend watch behavior.

Backend debugging prints an attach configuration with the actual loopback port
and source mapping. `awaiting_debugger` is separate from readiness. The attach
budget defaults to 300 seconds; the subsequent session budget defaults to 3600
seconds. Both can be set to 1-3600 seconds with `--attach-timeout` and
`--session-timeout`. Interrupt the command to restore the ordinary backend.
Handled failure and timeout also restore it and verify removal of the debug port.
After a hard host/daemon crash, explicitly reconcile ordinary mode with `dev:up`.
Debug images and their override are absent from production topology.

## Optional Editor Container

The Dev Container remains an optional, separately qualified editor environment.
Its dependency artifacts use its existing five platform-specific volumes. It does
not mount the Docker socket or introduce another application topology. Host
connected development remains the default; the default editor container supplies
the existing provisioned portable workflow. Enabling a socket or remote workspace
would need its own capability and ownership decision.

## Adopt In Another Repository

Adopt the responsibilities, not the Coordinator's database topology or private
state schema. Keep exactly one tool-version authority, one dependency graph per
ecosystem, one task owner, and one owner of each long-lived process.

1. Inventory actual commands, protected data, host platforms and provider
   prerequisites. Record representative startup, edit, failure and stop journeys.
2. Introduce mise for exact tool versions while retaining existing Make targets.
   This isolates the toolchain improvement from task migration and preserves CI.
3. Move task entrypoints only when command and failure behavior remain equivalent.
   Keep a compatibility Make target as a thin delegation if external callers still
   require it. Keep Make's file dependency graph where incremental artifacts need
   it; do not duplicate a task's implementation in both files.
4. Add root-specific ownership, bounded diagnostics and dependency coordination
   only where concurrent worktrees or partial failure demonstrate the need.
5. Transfer deterministic demo scenarios and browser inspection where the product
   has a UI. Transfer provider-specific doctor checks where the project actually
   depends on those providers.
6. Compare the same journeys under declared cache/platform conditions. Remove the
   compatibility facade only after its callers migrate. Publish measured costs and
   unresolved provider boundaries before proposing a company-wide default.

For an application-pilot archetype, preserve useful native-process workflows and existing demo journeys;
Compose is not a requirement for those processes. For extension-service archetype, preserve
its integration-specific prerequisite checks. For reporting-service archetype, preserve its small
host prerequisite set. None needs Coordinator's secret custody, database roles or
GitHub control-plane machinery merely to adopt mise.

Do not add Tilt without a concrete multi-service/Kubernetes reconciliation need,
or process-compose without multiple host processes whose readiness and shutdown
need supervision. Do not create a shared framework before a second actual consumer
proves both equivalence and lower maintenance cost. External adoption and universal
superiority are not outcomes established by this repository's CI.
