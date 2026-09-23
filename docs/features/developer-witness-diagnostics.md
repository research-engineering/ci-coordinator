# Developer Witness Failure Boundaries

Status: accepted design for bounded native qualification diagnostics

Owner: developer environment. [Implementation plan](developer-witness-diagnostics-plan.md).

## Decision

Preserve the connected-stack acceptance checks while separating supervised HMR
work from cleanup and identifying failed debug-provider operations. This changes
developer qualification, not application behavior, GitHub coordination or data.

Two observed native failures motivate this scope. An HMR child can consume its
parent's entire 90-second budget before cleanup. A debug command's nonzero exit
does not identify whether configuration, startup, port observation or restoration
failed. Neither observation identifies a Docker or Vite root cause.

## Lifecycle Invariants

The parent retains its existing 90-second process deadline. The child receives
70 seconds for work and one shared 10-second cleanup budget. The remaining
nominal 10 seconds covers startup and reporting. Each action races against its
remaining budget and rejects a late result even if the timer callback was delayed.

```text
work <= 70s; cleanup <= 10s
startup + reporting + scheduling < 10s
  => child completion precedes parent deadline
not observed assumption => parent rejects timeout, never success
```

This is not a guarantee under arbitrary scheduler starvation. A timed-out Promise
is not proof that its underlying operation stopped. The existing process-group
guard and Python fixture restoration remain necessary. Cleanup cannot renew its
budget for each action. Later cleanup checks reject without starting over-budget
I/O; Python still restores owned probe files. Every work/cleanup phase emits bounded
elapsed milliseconds. Existing material phases report monotonic elapsed milliseconds
on stderr; this does not alter the machine-readable result on stdout. Passive
post-baseline HMR update/reload/error counts and the existing module response status
use closed fields only and do not issue additional requests.

Success still requires all rendered DOM/style checks, the same document and HMR
socket, every phase marker, no failed marker and a clean process exit. Priming and
the actual update each remain one write. There is no polling configuration,
artificial sleep, timestamp manipulation, extra HTTP probe or relaxed oracle.

Debug failures contain only a code-owned operation, bounded exit status, closed
process failure kind and monotonic elapsed time. Provider output, command lines,
environment and exception messages are not newly exported. Restoration is
attempted exactly once even after failure. A failed restoration retains both
errors; successful restoration preserves the original exception. Cancellation
retains its original identity even if restoration also fails.

## Alternatives And Revision Conditions

Increasing the parent timeout alone leaves unbounded cleanup and hides attribution.
Retrying the job alone cannot establish a cause. Global Vite watcher changes or a
new process framework add unproved behavior. The selected owner-local change has
smaller blast radius and makes the next native observation discriminate phases.

The cost is a shorter work budget and a small per-phase timer. Native minimum and
current Compose checks must still pass. Revise the partition if measured legitimate
work exceeds 70 seconds, or startup/reporting consumes the reserve; do not silently
increase it. Investigate the exact failed provider operation before changing image,
network, cache or runtime policy. A later passing run does not erase either failure.

## Proof

Native Node controls independently reject late completion, hung work, exhausted
shared cleanup, and late cleanup. Positive controls retain successful action values.
Python controls cover every debug operation, clean/nonzero/timeout outcomes,
private output exclusion, and primary/restoration combinations including cancellation.
The existing real connected-stack matrix remains the whole-operation witness.
Local static checks cannot close these runtime obligations or production readiness.
