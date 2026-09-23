# Proof Command Execution

Status: cross-cutting specification

Last verified: 2026-08-22

## 1. Decision

Finite captured proof commands execute through one bounded POSIX process
lifecycle owned by `scripts/bounded_process.py`. Signal-aware finite command
orchestration, bounded Git, and detached-worktree owners delegate execution to
that lifecycle; they do not implement independent pipe, timeout, or
process-group engines. The execution owner projects only declared environment
variables, closes undeclared standard input, captures a single bounded
stdout-plus-stderr budget while the process runs, applies a positive finite
timeout, and owns the complete child process group.

An interactive provider session is a distinct algebra: its caller must observe
provider state concurrently and later request shutdown. Such a session remains
under its dedicated domain contract and lifecycle owner; it is not represented
as a finite captured command and does not weaken the finite-command boundary.

Proofkit environment, credential, network, and cache fields remain admitted
command metadata. The local executor enforces environment projection and the
`none` credential boundary. It validates network and cache classifications but
does not represent subprocess execution as a network or filesystem sandbox.

## 2. Scope

This contract owns finite captured repository tooling invoked by the local
quality plan, branch-head quality, direct command sequences, and Proofkit CLI
adapters. It does not own product runtime subprocesses, interactive provider
sessions such as Compose Watch, target-repository workflow jobs, provider
runners, or deployment isolation.

Supported hosts are Linux and macOS. Windows and musl remain outside the
tooling support contract.

## 3. Model

For command `c`, source environment `E`, allowlist `A`, timeout `T`, output
limit `L`, process group `G`, stop predicate `S`, and result `r`:

```text
AdmittedRequest(c) := c.argv != empty and finite(T) and T > 0 and L > 0

ChildEnvironment(c, E) := {name -> E[name] | name in A and name in E}

CapturedBytes(r) := bytes(r.stdout) + bytes(r.stderr)

BoundedCompletion(c, r) :=
  AdmittedRequest(c)
  and CapturedBytes(r) <= L
  and elapsed(c) is finite
  and not StopObserved(c, S)
  and no executable descendant remains in G
```

The stop predicate is an optional code-owned, side-effect-free cancellation
input. A true value sampled at the pre-spawn admission point suppresses process
creation. A value that changes after that linearization point may race with
process creation, but its first observation after spawn irreversibly suppresses
success and starts the same bounded group termination used for timeout. A
signal-aware adapter may record a signal in its own state and project that state
as the stop predicate, but it cannot acquire direct process handles or
independently classify process-group liveness.

The subprocess starts a new session, so its process-group id equals the direct
child pid. After the direct child exits, the executor admits one fixed 250 ms
quiescence interval, capped by the command's absolute timeout, for descendants
that are already completing. On Linux, a process-group existence probe is
refined through a bounded `/proc` scan: stopped or runnable members remain
executable authority. A zombie or dead leader is non-executable only when its
process record reports exactly one thread; additional or invalid thread counts
remain executable or unknown because another thread can continue after the
leader calls `pthread_exit`. A timeout, output overflow, or parent-side
lifecycle error terminates the group and suppresses success. An executable
descendant that remains after the interval suppresses success under the default
`reject` policy. A code-owned caller may instead select `terminate`; this
terminates the complete residual group while preserving the direct child's
status. `T` is the success deadline, not a false wall-clock return deadline:
reaching it irreversibly suppresses success. Termination starts at most once,
allows one fixed one-second `SIGTERM` grace, sends `SIGKILL`, and allows one
fixed one-second pipe-closure grace. Host syscall scheduling is outside this
timer claim. Neither policy can produce success until the group is quiescent
and every captured pipe is closed. A negative process-group observation is not
terminal evidence while a capture pipe remains open: the executor re-observes
liveness until both observations agree, or until the existing timeout or
termination schedule resolves the command. This prevents a transiently
incomplete process-table observation from admitting a descendant that still
owns a captured descriptor. The narrower policy does not prove a product
postcondition, so its caller must independently validate every required effect
after cleanup.

## 4. Invariants

```text
inherit(c) = allowlist => Environment(c) = ChildEnvironment(c, sourceEnvironment)
inherit(c) = none => Environment(c) = empty
credentialClass(c) = none => no credential-shaped allowlist name
id(c) = selective.plan => allowlist(c) includes the exact base/head range pair
orchestrates(parent, child) => allowlist(child) subset-of allowlist(parent)
input(c) = undeclared => stdin(c) = DEVNULL
CapturedBytes(c) > L => failure and terminate(processGroup(c))
elapsed(c) >= T => failure and terminate(processGroup(c))
StopSampledAtPreSpawnAdmission(c) => failure and not Spawned(c)
StopObservedAfterSpawn(c) => failure and terminate(processGroup(c))
primaryFailureAssigned(c, f) => primaryFailure(c) = f
terminationStarted(c, s) => forceKillAt(c) = s + 1s
terminationStarted(c, s) => hardStopAt(c) = s + 2s
count(terminationStarted(c)) <= 1
resultResolved(c) and directChildReaped(c) => no final signal(processGroupId(c))
directChildExitedAt(c, t)
  and executableDescendantsRemainAt(c, min(t + 250ms, timeoutAt(c)))
  and residualPolicy(c) = reject
  => failure and terminate(processGroup(c))
directChildExitedAt(c, t)
  and executableDescendantsRemainAt(c, min(t + 250ms, timeoutAt(c)))
  and residualPolicy(c) = terminate
  => terminate(processGroup(c))
     and success(c) only after not executableDescendantsRemain(c)
linuxState(descendant) in {Z, X, x}
  and linuxThreadCount(descendant) = 1
  => not executable(descendant)
linuxThreadCount(descendant) != 1 => executable-or-unknown(descendant)
directChildExited(c)
  and not executableProcessGroupObserved(c)
  and capturePipeOpen(c)
  => not residualGroupChecked(c)
success(c) => status(c) = 0 and error(c) = none
failure(c) => no success receipt
DelegatingAdapter(c) => executionOwner(c) = scripts/bounded_process.py
DelegatingAdapter(c) => finite(timeout(c)) and timeout(c) > 0
```

The quiescence interval distinguishes an observable leak from ordinary process
shutdown scheduling. It is not a daemonization allowance: the interval is
fixed, included in the absolute command timeout, and success still requires no
executable member in the owned process group. Only a sole-thread zombie proves
that its process has no sibling thread capable of consuming CPU, retaining
descriptors, or producing further side effects.

The Dev Container provisioning owner selects `terminate` only for its exact
internal `devcontainer up` route. The pinned CLI may exit after the owned
container starts while an attached host-side Docker client still retains the
capture pipes. The witness makes no process-name, command-line, executable, or
cardinality claim about that residual group: it terminates every member. It then
independently resolves exactly one labelled container, verifies its mount and
package-store boundaries, disconnects every network, runs the admitted portable
proof through a new command bound to the same full container ID, and finally
removes only that rediscovered ID. Therefore damage to any required observable
caused by residual cleanup, or replacement of the owned container, makes a
downstream postcondition false rather than creating a success receipt. Every
other command retains the default `reject` policy.

The output limit is shared across stdout and stderr. Two independent limits
would admit `2L` bytes and therefore would not prove the declared bound.

`PROOFKIT_BASE_REF`, `PROOFKIT_HEAD_REF`, the non-secret `CI` execution
acknowledgement, and `HOME` are the only non-toolchain values admitted by the
branch-head orchestrator. The branch-head owner admits the SHA pair as full
lowercase commits, ancestry, and current-head identity before execution; the
selective planner rejects a partial pair. `CI` reaches only children that
declare it and allows the destructive connected-stack witness to distinguish
deliberate gate execution from an accidental direct invocation. `HOME` reaches
that same provider witness because Docker Desktop resolves its selected context
from the user Docker configuration. Without transitive forwarding, an
exact-range quality run could become vacuous or fail after its cheaper
witnesses had already completed.

## 5. Ownership

| Owner                                             | Responsibility                                                                                                                                                                 |
|---------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `scripts/bounded_process.py`                      | Sole finite captured-command owner: process group, non-blocking I/O, timeout, stop observation, output budget, stdin, residual classification, and normalized lifecycle result |
| `scripts/bounded_git.py`                          | Git executable and environment projection; delegates process execution to the finite-command owner                                                                             |
| `scripts/dev_environment/watch_witness.py`        | Managed interactive Compose Watch session, concurrent provider observation, and bounded session shutdown under the developer-environment contract                              |
| `scripts/devcontainer_witness.py`                 | Exact Dev Container cleanup route and independent post-cleanup provider postconditions                                                                                         |
| `scripts/quality_plan.py`                         | Command order, policy admission, exact environment projection, and exit-policy interpretation                                                                                  |
| `scripts/proofkit_cli.py`                         | Proofkit argv/input and public result adaptation                                                                                                                               |
| `scripts/command_sequence.py`                     | Ordered direct command execution and first-failure status                                                                                                                      |
| `scripts/mutation/detached_worktree_lifecycle.py` | Signal state, detached-worktree allocation, idempotent cleanup, and result adaptation; delegates every command and Git process to the sole execution owner                     |

No lower owner interprets product requirements, Proofkit semantic output, or
provider authority.

## 6. Classification Non-Claims

`networkPolicy=none` does not prevent socket syscalls. `cachePolicy=read-only`
does not make the filesystem immutable. Those fields remain necessary route
facts for provider/container isolation, but local execution may claim only
that their vocabulary relation was admitted and retained.

Formally:

```text
AdmittedClassification(c) does not imply OSIsolation(c)
```

An OS-level network or filesystem enforcement claim requires a separately
specified sandbox and adversarial escape witnesses.

## 7. Rejected Alternatives

| Alternative                                                    | Rejection proof                                                                                                                                                                                                                           |
|----------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `subprocess.run(capture_output=True)` followed by a size check | Memory is allocated before the check, so the limit is observational rather than preventive.                                                                                                                                               |
| Killing only the direct child                                  | Grandchildren can retain pipes, CPU, locks, and filesystem effects after timeout.                                                                                                                                                         |
| Preserving unknown post-exit descendants                       | A descendant can retain pipes, CPU, locks, or effects after the direct child reports completion. The default rejects it; an explicit cleanup owner must terminate the whole group and independently revalidate every required observable. |
| Inheriting `os.environ`                                        | Undeclared credentials and provider context cross the command boundary.                                                                                                                                                                   |
| Per-stream output limits                                       | Combined capture can reach twice the stated bound.                                                                                                                                                                                        |
| Claiming network isolation from metadata                       | Classification without an OS enforcement mechanism cannot falsify a socket escape.                                                                                                                                                        |

## 8. Acceptance

The contract is admitted only when native tests prove the following:

- normal status and non-zero status;
- exact environment projection;
- closed stdin;
- combined-stream overflow;
- pre-spawn-admission and in-flight stop observation;
- first-failure preservation;
- absence of an unconditional post-resolution signal;
- timeout across direct-child and post-exit drain phases;
- transient descendant quiescence;
- persistent grandchild termination;
- Linux live/stopped versus sole-thread zombie/dead classification;
- a zombie leader with live sibling threads;
- fail-closed malformed process metadata;
- default rejection and explicit termination of a residual group;
- exact Dev Container route scoping;
- bounded return latency;
- the fixed TERM-to-KILL grace;
- input delivery; and
- argument-redacted spawn failure.

The full
quality plan must then run from its declared command catalog with no ambient
inheritance beyond each exact allowlist, the signal/worktree adapter must prove
that residual descendants and every zero or invalid timeout suppress success,
and an exact-range branch-head run must route a non-empty current diff through
`selective.plan`.
